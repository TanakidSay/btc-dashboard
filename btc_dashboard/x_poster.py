from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Any

from .config import Settings

logger = logging.getLogger(__name__)

REQUIRED_CREDENTIALS = {
    "X_API_KEY": "x_api_key",
    "X_API_SECRET": "x_api_secret",
    "X_ACCESS_TOKEN": "x_access_token",
    "X_ACCESS_SECRET": "x_access_secret",
}
NORMAL_COOLDOWN_SECONDS = 60 * 60
MAX_POSTS_PER_DAY = 4
DUPLICATE_TEXT_WINDOW = timedelta(hours=24)
EVENT_RETENTION = timedelta(days=7)

_status_lock = Lock()
_last_post_time: str | None = None
_last_error: str | None = None
_last_block_reason: str | None = None


@dataclass(frozen=True)
class XPostResult:
    posted: bool
    reason: str | None = None
    error: str | None = None


def post_to_x(
    text: str,
    settings: Settings,
    *,
    event_id: str | None = None,
    signal_type: str = "manual_test",
    bypass_cooldown: bool = False,
) -> XPostResult:
    text = text.strip()
    if not text:
        error = "X post blocked: generated text is empty"
        logger.error(error)
        _set_error(error)
        return XPostResult(posted=False, reason="x_empty_text", error=error)
    if len(text) > 280:
        error = f"X post blocked: generated text is {len(text)} characters"
        logger.error(error)
        _set_error(error)
        return XPostResult(posted=False, reason="x_text_too_long", error=error)

    if not settings.enable_x_posting:
        logger.info("[X preview] %s", text)
        _set_error(None)
        return XPostResult(posted=False, reason="x_posting_disabled")

    event_id = event_id or _text_hash(text)
    events = _load_events(settings)
    text_hash = _text_hash(text)

    duplicate = _duplicate_event(events, event_id, text_hash)
    if duplicate:
        error = f"X post blocked: duplicate {duplicate}"
        logger.warning(error)
        _set_error(error)
        return XPostResult(posted=False, reason="x_duplicate", error=error)

    daily_count = _daily_post_count(events)
    if not bypass_cooldown and daily_count >= MAX_POSTS_PER_DAY:
        error = f"X post blocked: daily limit of {MAX_POSTS_PER_DAY} reached"
        logger.warning(error)
        _set_error(error)
        return XPostResult(posted=False, reason="daily_limit_reached", error=error)

    if not bypass_cooldown:
        remaining = _cooldown_remaining_seconds(events)
        if remaining > 0:
            error = f"X post blocked: normal signal cooldown has {remaining}s remaining"
            logger.warning(error)
            _set_error(error)
            return XPostResult(posted=False, reason="cooldown_active", error=error)

    missing = missing_credentials(settings)
    if missing:
        error = f"X posting enabled but missing credentials: {', '.join(missing)}"
        logger.error(error)
        _set_error(error)
        return XPostResult(posted=False, reason="x_credentials_missing", error=error)

    try:
        import tweepy
    except ImportError as exc:
        error = "X posting enabled but tweepy is not installed"
        logger.error("%s: %s", error, exc)
        _set_error(error)
        return XPostResult(posted=False, reason="x_dependency_missing", error=error)

    try:
        client = tweepy.Client(
            consumer_key=settings.x_api_key,
            consumer_secret=settings.x_api_secret,
            access_token=settings.x_access_token,
            access_token_secret=settings.x_access_secret,
        )
        client.create_tweet(text=text)
    except Exception as exc:
        error = f"X post failed: {exc}"
        logger.exception(error)
        _set_error(error)
        return XPostResult(posted=False, reason="x_post_failed", error=error)

    posted_at = _utc_now_iso()
    events.append({
        "event_id": event_id,
        "signal_type": signal_type,
        "text_hash": text_hash,
        "posted_at": posted_at,
        "bypass_cooldown": bypass_cooldown,
    })
    _save_events(settings, events)
    logger.info("[X posted] %s", text)
    _set_posted(posted_at)
    return XPostResult(posted=True)


def get_x_status(settings: Settings) -> dict[str, object]:
    events = _load_events(settings)
    with _status_lock:
        return {
            "enabled": settings.enable_x_posting,
            "test_enabled": settings.enable_x_test_post,
            "credentials_configured": credentials_configured(settings),
            "last_post_time": _last_post_time or _last_post_time_from_events(events),
            "last_error": _last_error,
            "cooldown_remaining_seconds": _cooldown_remaining_seconds(events),
            "max_posts_per_day": MAX_POSTS_PER_DAY,
            "daily_post_count": _daily_post_count(events),
            "daily_limit_remaining": max(MAX_POSTS_PER_DAY - _daily_post_count(events), 0),
            "last_block_reason": _last_block_reason,
            "posted_events_count": len(events),
        }


def record_x_block(reason: str) -> None:
    logger.info("X auto-post blocked: %s", reason)
    _set_error(reason)


def credentials_configured(settings: Settings) -> bool:
    return not missing_credentials(settings)


def missing_credentials(settings: Settings) -> list[str]:
    return [
        env_name
        for env_name, setting_name in REQUIRED_CREDENTIALS.items()
        if not getattr(settings, setting_name)
    ]


def _load_events(settings: Settings) -> list[dict[str, Any]]:
    path = settings.x_posted_events_path
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    events = raw if isinstance(raw, list) else raw.get("events", [])
    if not isinstance(events, list):
        return []
    pruned = _prune_events([event for event in events if isinstance(event, dict)])
    if len(pruned) != len(events):
        _save_events(settings, pruned)
    return pruned


def _save_events(settings: Settings, events: list[dict[str, Any]]) -> None:
    path = settings.x_posted_events_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"events": _prune_events(events)}, indent=2), encoding="utf-8")


def _prune_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cutoff = datetime.now(UTC) - EVENT_RETENTION
    return [
        event
        for event in events
        if (posted_at := _parse_iso(event.get("posted_at"))) is not None and posted_at >= cutoff
    ]


def _duplicate_event(events: list[dict[str, Any]], event_id: str, text_hash: str) -> str | None:
    cutoff = datetime.now(UTC) - DUPLICATE_TEXT_WINDOW
    for event in events:
        if event.get("event_id") == event_id:
            return "event_id"
        posted_at = _parse_iso(event.get("posted_at"))
        if posted_at and posted_at >= cutoff and event.get("text_hash") == text_hash:
            return "text"
    return None


def _cooldown_remaining_seconds(events: list[dict[str, Any]]) -> int:
    last_normal = None
    for event in events:
        if event.get("bypass_cooldown"):
            continue
        posted_at = _parse_iso(event.get("posted_at"))
        if posted_at and (last_normal is None or posted_at > last_normal):
            last_normal = posted_at
    if last_normal is None:
        return 0
    elapsed = (datetime.now(UTC) - last_normal).total_seconds()
    return max(int(NORMAL_COOLDOWN_SECONDS - elapsed), 0)


def _daily_post_count(events: list[dict[str, Any]]) -> int:
    cutoff = datetime.now(UTC) - timedelta(hours=24)
    return sum(
        1
        for event in events
        if not event.get("bypass_cooldown")
        and (posted_at := _parse_iso(event.get("posted_at"))) is not None
        and posted_at >= cutoff
    )


def _last_post_time_from_events(events: list[dict[str, Any]]) -> str | None:
    last_event = max(
        events,
        key=lambda event: _parse_iso(event.get("posted_at")) or datetime.min.replace(tzinfo=UTC),
        default=None,
    )
    return None if last_event is None else last_event.get("posted_at")


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _set_posted(posted_at: str) -> None:
    global _last_block_reason, _last_error, _last_post_time
    with _status_lock:
        _last_post_time = posted_at
        _last_error = None
        _last_block_reason = None


def _set_error(error: str | None) -> None:
    global _last_block_reason, _last_error
    with _status_lock:
        _last_error = error
        if error:
            _last_block_reason = error


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
