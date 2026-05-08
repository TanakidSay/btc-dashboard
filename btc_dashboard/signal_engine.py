from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import Settings
from .services import (
    build_alerts,
    cached_dashboard_resource,
    get_btc_treasury_holdings,
    get_etf_flow,
    get_recent_whale_transactions,
    get_security_overview,
    snapshot,
)
from .x_poster import get_x_status, post_to_x, record_x_block

logger = logging.getLogger(__name__)

DASHBOARD_URL = "https://btcwindow.up.railway.app/"
NORMAL_COOLDOWN_SECONDS = 60 * 60
MEGA_WHALE_BTC = 1000
WHALE_BTC = 500
ETF_FLOW_THRESHOLD_USD = 200_000_000
MINING_POOL_SHARE_THRESHOLD = 30
HASHRATE_MAJOR_MOVE_PERCENT = 10
AUTO_POST_ALLOWED_SIGNAL_TYPES = [
    "fee_spike",
    "whale_alert",
    "mega_whale",
    "security_event",
    "price_breakout",
]
AUTO_POST_BLOCKED_SIGNAL_TYPES = [
    "fee_trend_rising",
    "combined_congestion",
    "cheap_fee_window",
    "cheap_window",
    "hashrate_spike",
    "hashrate_drop",
    "etf_inflow",
    "etf_outflow",
    "treasury_holdings",
    "mining_pool_concentration",
]
SIGNAL_SCORES = {
    "security_critical": 100,
    "mega_whale": 95,
    "whale": 80,
    "strong_fee_spike": 75,
    "price_breakout_low_fee": 65,
    "cheap_fee_window": 45,
    "pool_concentration_warning": 40,
    "normal_informational": 0,
}
PENDING_SIGNAL_MAX_AGE_SECONDS = 24 * 60 * 60
DAILY_POST_SIGNAL_TYPE = "daily_snapshot"


@dataclass(frozen=True)
class Signal:
    signal_type: str
    severity: str
    message: str
    post_text: str
    duplicate_key: str
    immediate: bool
    detected_at: str
    source: dict[str, Any]


def detect_signals(settings: Settings) -> list[Signal]:
    data = snapshot()
    now_iso = _utc_now_iso()
    signals: list[Signal] = []

    try:
        whale_transactions = get_recent_whale_transactions(settings)
    except Exception as exc:
        logger.warning("whale signal lookup failed: %s", exc)
        whale_transactions = []

    alerts = build_alerts(
        data["fee_data"],
        data["price_history"],
        fee_spike_threshold=settings.fee_spike_threshold,
        price_breakout_lookback=settings.price_breakout_lookback,
        hashrate=data["hashrate"],
        whale_transactions=whale_transactions,
        whale_alert_threshold_btc=WHALE_BTC,
    )

    latest_fee = _latest_fee(data.get("fee_data"))
    for alert in alerts:
        alert_type = alert.get("type", "")
        if alert_type in {"fee_spike", "fee_trend_rising", "combined_congestion"}:
            signals.append(_fee_signal(alert, now_iso))
        elif alert_type == "price_breakout":
            signal = _price_breakout_signal(
                alert,
                latest_fee,
                settings.fee_spike_threshold,
                now_iso,
            )
            if signal:
                signals.append(signal)
        elif alert_type == "cheap_window":
            signals.append(_low_fee_signal(alert, now_iso))
        elif alert_type == "whale_transaction":
            signal = _whale_signal(alert, now_iso)
            if signal:
                signals.append(signal)

    hashrate_signal = _hashrate_signal(data.get("hashrate_points", []), now_iso)
    if hashrate_signal:
        signals.append(hashrate_signal)

    try:
        security = get_security_overview(settings)
    except Exception as exc:
        logger.warning("security signal lookup failed: %s", exc)
        security = {}

    pool_signal = _pool_concentration_signal(security, now_iso)
    if pool_signal:
        signals.append(pool_signal)

    security_signal = _security_event_signal(security, now_iso)
    if security_signal:
        signals.append(security_signal)

    try:
        etf_flow = get_etf_flow(settings)
    except Exception as exc:
        logger.warning("ETF signal lookup failed: %s", exc)
        etf_flow = {}

    etf_signal = _etf_signal(etf_flow, now_iso)
    if etf_signal:
        signals.append(etf_signal)

    try:
        treasury = get_btc_treasury_holdings(settings)
    except Exception as exc:
        logger.warning("treasury signal lookup failed: %s", exc)
        treasury = {}

    # Expose treasury cache visibility even though no posting threshold was requested.
    if treasury:
        signals.append(_informational_signal("treasury_holdings", treasury, now_iso))

    return signals


def process_signals(settings: Settings) -> list[dict[str, Any]]:
    state = _load_post_state(settings.x_signal_state_path)
    state["pending_signal_queue"] = _prune_pending_queue(state.get("pending_signal_queue", []))
    results = []
    for signal in detect_signals(settings):
        result = _process_signal(signal, settings, state)
        results.append(result)
    drained = _drain_pending_signal_queue(settings, state)
    if drained:
        results.append(drained)
    _save_post_state(settings.x_signal_state_path, state)
    return results


def process_daily_post(
    settings: Settings,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now().astimezone()
    today = now.date().isoformat()
    state = _load_post_state(settings.x_signal_state_path)

    if state.get("last_daily_post_date") == today:
        logger.info("daily post skipped because already posted today")
        return {"posted": False, "reason": "already_posted_today"}

    if state.get("last_daily_attempt_date") == today:
        logger.info("daily post skipped because already attempted today")
        return {"posted": False, "reason": "already_attempted_today"}

    post_hour = max(0, min(int(settings.x_daily_post_hour), 23))
    if now.hour < post_hour:
        return {"posted": False, "reason": "not_due"}

    post_text, invalid_reason = generate_daily_dashboard_post(settings)
    if not post_text:
        logger.info("daily post skipped because data invalid: %s", invalid_reason)
        state["last_daily_attempt_date"] = today
        _save_post_state(settings.x_signal_state_path, state)
        return {"posted": False, "reason": invalid_reason or "invalid_data"}

    text_hash = _text_hash(post_text)
    if state.get("last_daily_post_hash") == text_hash:
        logger.info("daily post skipped because data invalid: duplicate daily content")
        state["last_daily_attempt_date"] = today
        _save_post_state(settings.x_signal_state_path, state)
        return {"posted": False, "reason": "duplicate_daily_content", "text": post_text}

    logger.info("daily post created")
    state["last_daily_attempt_date"] = today
    result = post_to_x(
        post_text,
        settings,
        event_id=f"daily:{today}",
        signal_type=DAILY_POST_SIGNAL_TYPE,
        bypass_cooldown=False,
    )
    response = {
        "posted": result.posted,
        "reason": result.reason,
        "text": post_text,
    }
    if result.error:
        response["error"] = result.error

    if result.posted:
        logger.info("daily post sent successfully")
        state["last_daily_post_date"] = today
        state["last_daily_post_hash"] = text_hash
        state["last_daily_posted_at"] = _utc_now_iso()
    else:
        logger.warning("daily post failed with error: %s", result.error or result.reason)

    _save_post_state(settings.x_signal_state_path, state)
    return response


def generate_daily_dashboard_post(settings: Settings) -> tuple[str | None, str | None]:
    del settings
    data = snapshot()
    ownership = cached_dashboard_resource("ownership_cache")
    security = cached_dashboard_resource("security_cache")
    percent_mined = _float_or_none(ownership.get("percent_mined"))
    remaining_to_mine = _float_or_none(ownership.get("remaining_to_mine"))
    if percent_mined is None or remaining_to_mine is None:
        return None, "invalid_core_data"

    price = _float_or_none(data.get("btc_price"))
    latest_fee = _latest_fee(data.get("fee_data"))
    security_status = _daily_security_status(security)

    lines = [
        "Daily Bitcoin Snapshot",
        "",
        f"BTC Price: {_format_usd(price) if price is not None else 'Limited visibility'}",
        f"Mined Supply: {percent_mined:.2f}%",
        f"Only {_format_btc_amount(remaining_to_mine)} BTC left to mine",
    ]
    if latest_fee is not None:
        lines.append(f"Fees: {latest_fee:.1f} sat/vB")
    lines.extend([
        f"Network Security: {security_status}",
        "",
        "Live dashboard:",
        DASHBOARD_URL,
    ])
    text = "\n".join(lines)
    if len(text) > 280:
        text = "\n".join([
            "Bitcoin scarcity update:",
            "",
            f"{percent_mined:.2f}% of BTC has already been mined.",
            f"Only {_format_btc_amount(remaining_to_mine)} BTC remain.",
            "",
            f"Track supply, fees, network health, and ownership: {DASHBOARD_URL}",
        ])
    if len(text) > 280:
        return None, "post_too_long"
    return text, None


def latest_signals(settings: Settings) -> dict[str, Any]:
    return {
        "signals": [asdict(signal) for signal in detect_signals(settings)],
        "x_posting_enabled": settings.enable_x_posting,
        "cooldown_seconds": NORMAL_COOLDOWN_SECONDS,
        "dashboard_url": DASHBOARD_URL,
        "post_state": _public_post_state(_load_post_state(settings.x_signal_state_path)),
    }


def signals_policy() -> dict[str, Any]:
    return {
        "allowed_signal_types": AUTO_POST_ALLOWED_SIGNAL_TYPES,
        "thresholds": {
            "whale_btc": WHALE_BTC,
            "mega_whale_btc": MEGA_WHALE_BTC,
            "minimum_normal_score": 65,
            "strong_fee_spike_type": "fee_spike",
            "security_severity": "critical",
        },
        "scoring": SIGNAL_SCORES,
        "cooldown_minutes": NORMAL_COOLDOWN_SECONDS // 60,
        "max_posts_per_day": 1,
        "blocked_signal_types": AUTO_POST_BLOCKED_SIGNAL_TYPES,
        "daily_post_hour": int(os.getenv("X_DAILY_POST_HOUR", "9")),
    }


def should_auto_post_signal(signal: Signal) -> dict[str, Any]:
    severity = signal.severity.lower()
    cooldown_applied = not signal.immediate

    if signal.signal_type == "mega_whale":
        amount_btc = _signal_amount_btc(signal)
        allowed = amount_btc is not None and amount_btc >= MEGA_WHALE_BTC
        return _policy_result(
            allowed,
            "mega_whale_threshold_met" if allowed else "mega_whale_below_threshold",
            False,
            severity,
            SIGNAL_SCORES["mega_whale"] if allowed else 0,
        )

    if signal.signal_type == "whale_alert":
        amount_btc = _signal_amount_btc(signal)
        allowed = amount_btc is not None and amount_btc >= WHALE_BTC
        return _policy_result(
            allowed,
            "whale_threshold_met" if allowed else "whale_below_500_btc",
            True,
            severity,
            SIGNAL_SCORES["whale"] if allowed else 0,
        )

    if signal.signal_type == "fee_spike":
        allowed = severity == "high"
        score = SIGNAL_SCORES["strong_fee_spike"] if allowed else 0
        return _policy_result(
            allowed,
            "strong_fee_spike" if allowed else "score_too_low",
            True,
            severity,
            score,
        )

    if signal.signal_type == "price_breakout":
        low_fee = bool(signal.source.get("fee_still_low"))
        score = SIGNAL_SCORES["price_breakout_low_fee"] if low_fee else 0
        return _policy_result(
            score >= 65,
            "price_breakout_low_fee" if score >= 65 else "score_too_low",
            True,
            severity,
            score,
        )

    if signal.signal_type == "security_event":
        allowed = severity == "critical"
        return _policy_result(
            allowed,
            "security_critical" if allowed else "security_not_critical",
            False if allowed else cooldown_applied,
            severity,
            SIGNAL_SCORES["security_critical"] if allowed else 0,
        )

    if signal.signal_type == "mining_pool_concentration":
        score = SIGNAL_SCORES["pool_concentration_warning"]
        return _policy_result(
            False,
            "score_too_low",
            True,
            severity,
            score,
        )

    if signal.signal_type == "cheap_fee_window":
        return _policy_result(
            False,
            "score_too_low",
            True,
            severity,
            SIGNAL_SCORES["cheap_fee_window"],
        )

    return _policy_result(False, "score_too_low", cooldown_applied, severity, 0)


def pending_signal_status(settings: Settings) -> dict[str, Any]:
    state = _load_post_state(settings.x_signal_state_path)
    queue = _prune_pending_queue(state.get("pending_signal_queue", []))
    return {
        "pending_signals_count": len(queue),
        "highest_pending_score": max((int(item.get("score", 0)) for item in queue), default=0),
    }


def _process_signal(
    signal: Signal,
    settings: Settings,
    state: dict[str, Any],
) -> dict[str, Any]:
    result = asdict(signal)
    result["posted"] = False
    result["suppressed_reason"] = None

    if not signal.post_text:
        result["suppressed_reason"] = "informational_only"
        record_x_block("informational_only")
        return result

    policy = should_auto_post_signal(signal)
    result["policy"] = policy
    if not policy["allowed"]:
        result["suppressed_reason"] = policy["reason"]
        logger.info(
            "X auto-post blocked for %s: %s",
            signal.signal_type,
            policy["reason"],
        )
        record_x_block(policy["reason"])
        return result

    if policy["cooldown_applied"]:
        queued = _queue_pending_signal(signal, policy, state)
        result["suppressed_reason"] = queued["reason"]
        result["queued"] = queued["queued"]
        if not queued["queued"]:
            record_x_block(queued["reason"])
        return result

    now = time.time()
    post_result = post_to_x(
        signal.post_text,
        settings,
        event_id=signal.duplicate_key,
        signal_type=signal.signal_type,
        bypass_cooldown=not policy["cooldown_applied"],
    )
    result["posted"] = post_result.posted
    result["suppressed_reason"] = post_result.reason
    if post_result.error:
        result["error"] = post_result.error
    if post_result.posted:
        _remember_signal(state, signal, now)
    return result


def _remember_signal(state: dict[str, Any], signal: Signal, posted_at: float) -> None:
    posted_keys = state.setdefault("posted_keys", [])
    if signal.duplicate_key not in posted_keys:
        posted_keys.append(signal.duplicate_key)
    del posted_keys[:-500]

    if not signal.immediate:
        state["last_normal_posted_at"] = posted_at

    state["last_post"] = {
        "signal_type": signal.signal_type,
        "duplicate_key": signal.duplicate_key,
        "post_text": signal.post_text,
        "posted_at": _utc_now_iso(),
    }


def _fee_signal(alert: dict[str, Any], now_iso: str) -> Signal:
    message = str(alert.get("message") or "Bitcoin fees are spiking")
    fee = alert.get("fee")
    height = alert.get("height")
    detail = f"{message}." if fee is None else f"Bitcoin fee alert: {fee} sat/vB at block {height}."
    return Signal(
        signal_type=str(alert.get("type") or "fee_alert"),
        severity=str(alert.get("severity") or "high"),
        message=message,
        post_text=_fit_post(f"{detail} Check the live BTC Window: {DASHBOARD_URL}"),
        duplicate_key=_bucket_key(str(alert.get("type") or "fee_alert"), now_iso),
        immediate=False,
        detected_at=now_iso,
        source=alert,
    )


def _low_fee_signal(alert: dict[str, Any], now_iso: str) -> Signal:
    message = str(alert.get("message") or "Low Bitcoin fee window detected")
    return Signal(
        signal_type="cheap_fee_window",
        severity="low",
        message=message,
        post_text=_fit_post(f"Low BTC fee window detected. {message}. Live view: {DASHBOARD_URL}"),
        duplicate_key=_bucket_key("low_fee_window", now_iso),
        immediate=False,
        detected_at=now_iso,
        source=alert,
    )


def _price_breakout_signal(
    alert: dict[str, Any],
    latest_fee: float | None,
    fee_threshold: float,
    now_iso: str,
) -> Signal | None:
    fee_still_low = latest_fee is not None and latest_fee <= fee_threshold
    message = str(alert.get("message") or "Bitcoin price breakout")
    return Signal(
        signal_type="price_breakout",
        severity=str(alert.get("severity") or "medium"),
        message=message,
        post_text=_fit_post(
            f"BTC price breakout while fees are still low. Live view: {DASHBOARD_URL}"
        ),
        duplicate_key=_bucket_key("price_breakout", now_iso),
        immediate=False,
        detected_at=now_iso,
        source={**alert, "fee_still_low": fee_still_low, "latest_fee": latest_fee},
    )


def _whale_signal(alert: dict[str, Any], now_iso: str) -> Signal | None:
    value_btc = _float_or_none(alert.get("value_btc"))
    if value_btc is None or value_btc < WHALE_BTC:
        return None
    txid = str(alert.get("txid") or "")
    signal_type = "mega_whale" if value_btc >= MEGA_WHALE_BTC else "whale_alert"
    label = "Mega whale" if value_btc >= MEGA_WHALE_BTC else "Whale"
    return Signal(
        signal_type=signal_type,
        severity="critical" if value_btc >= MEGA_WHALE_BTC else "high",
        message=str(alert.get("message") or f"{value_btc:,.0f} BTC moved"),
        post_text=_fit_post(
            f"{label} alert: {value_btc:,.0f} BTC moved in mempool. "
            f"Live view: {DASHBOARD_URL}",
        ),
        duplicate_key=f"whale:{txid}" if txid else _bucket_key(signal_type, now_iso),
        immediate=value_btc >= MEGA_WHALE_BTC,
        detected_at=now_iso,
        source=alert,
    )


def _hashrate_signal(points: list[dict[str, Any]], now_iso: str) -> Signal | None:
    if len(points) < 2:
        return None
    previous = _float_or_none(points[-2].get("value"))
    latest = _float_or_none(points[-1].get("value"))
    if previous is None or latest is None or previous <= 0:
        return None
    change_pct = ((latest - previous) / previous) * 100
    if abs(change_pct) < HASHRATE_MAJOR_MOVE_PERCENT:
        return None
    direction = "spike" if change_pct > 0 else "drop"
    return Signal(
        signal_type=f"hashrate_{direction}",
        severity="high",
        message=f"Hashrate major {direction}: {change_pct:+.1f}%",
        post_text=_fit_post(
            f"Bitcoin hashrate major {direction}: {change_pct:+.1f}% on latest sample. "
            f"Live view: {DASHBOARD_URL}",
        ),
        duplicate_key=_bucket_key(f"hashrate_{direction}", now_iso),
        immediate=False,
        detected_at=now_iso,
        source={"previous": previous, "latest": latest, "change_pct": round(change_pct, 2)},
    )


def _pool_concentration_signal(security: dict[str, Any], now_iso: str) -> Signal | None:
    attack_51 = security.get("attack_51") or {}
    share = _float_or_none(attack_51.get("top_pool_share")) or 0
    if share <= MINING_POOL_SHARE_THRESHOLD:
        return None
    pool_name = "top mining pool"
    pools = attack_51.get("pools")
    if isinstance(pools, list) and pools:
        pool_name = str(pools[0].get("name") or pool_name)
    return Signal(
        signal_type="mining_pool_concentration",
        severity="high",
        message=f"Mining pool concentration: {pool_name} at {share:.1f}%",
        post_text=_fit_post(
            f"Mining pool concentration alert: {pool_name} is {share:.1f}% "
            f"of recent blocks. Live view: {DASHBOARD_URL}",
        ),
        duplicate_key=_bucket_key("mining_pool_concentration", now_iso),
        immediate=False,
        detected_at=now_iso,
        source=attack_51,
    )


def _security_event_signal(security: dict[str, Any], now_iso: str) -> Signal | None:
    counts = {
        "orphans": _int_or_zero((security.get("double_spend") or {}).get("orphan_count")),
        "invalid": _int_or_zero((security.get("invalid_blocks") or {}).get("invalid_count")),
        "reorgs": _int_or_zero((security.get("reorgs") or {}).get("reorg_count")),
    }
    event_count = sum(counts.values())
    if event_count <= 0:
        return None
    return Signal(
        signal_type="security_event",
        severity="critical",
        message=f"Security monitor detected {event_count} event(s)",
        post_text=_fit_post(
            f"Bitcoin security monitor: {event_count} event(s) detected. Live view: {DASHBOARD_URL}"
        ),
        duplicate_key=_bucket_key("security_event", now_iso),
        immediate=True,
        detected_at=now_iso,
        source={"counts": counts, "security": security},
    )


def _etf_signal(etf_flow: dict[str, Any], now_iso: str) -> Signal | None:
    flow = _float_or_none(etf_flow.get("latest_net_flow_usd"))
    if flow is None or abs(flow) <= ETF_FLOW_THRESHOLD_USD:
        return None
    direction = "inflow" if flow > 0 else "outflow"
    amount = abs(flow) / 1_000_000
    return Signal(
        signal_type=f"etf_{direction}",
        severity="high",
        message=f"Bitcoin ETF {direction}: ${amount:,.0f}M",
        post_text=_fit_post(
            f"Spot BTC ETF {direction}: ${amount:,.0f}M latest net flow. Live view: {DASHBOARD_URL}"
        ),
        duplicate_key=_bucket_key(f"etf_{direction}", now_iso),
        immediate=False,
        detected_at=now_iso,
        source=etf_flow,
    )


def _informational_signal(signal_type: str, source: dict[str, Any], now_iso: str) -> Signal:
    total_btc = source.get("total_btc_held", "N/A")
    return Signal(
        signal_type=signal_type,
        severity="info",
        message=f"Treasury holdings cache available: {total_btc} BTC",
        post_text="",
        duplicate_key=_bucket_key(signal_type, now_iso),
        immediate=False,
        detected_at=now_iso,
        source=source,
    )


def _load_post_state(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "posted_keys": [],
            "last_normal_posted_at": 0,
            "last_post": None,
            "pending_signal_queue": [],
        }


def _save_post_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def _public_post_state(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "posted_key_count": len(state.get("posted_keys", [])),
        "last_normal_posted_at": state.get("last_normal_posted_at"),
        "last_post": state.get("last_post"),
        "last_daily_post_date": state.get("last_daily_post_date"),
        "last_daily_attempt_date": state.get("last_daily_attempt_date"),
    }


def _bucket_key(signal_type: str, now_iso: str) -> str:
    return f"{signal_type}:{now_iso[:13]}"


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _fit_post(text: str) -> str:
    if len(text) <= 280:
        return text
    suffix = f" {DASHBOARD_URL}"
    trimmed = text[: 280 - len(suffix) - 3].rstrip()
    return f"{trimmed}...{suffix}"


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _format_usd(value: float) -> str:
    return f"${value:,.0f}"


def _format_btc_amount(value: float) -> str:
    return f"{round(value):,}"


def _daily_security_status(security: dict[str, Any]) -> str:
    if not isinstance(security, dict) or not security:
        return "Limited visibility"
    event_count = (
        _int_or_zero((security.get("double_spend") or {}).get("orphan_count"))
        + _int_or_zero((security.get("invalid_blocks") or {}).get("invalid_count"))
        + _int_or_zero((security.get("reorgs") or {}).get("reorg_count"))
    )
    if event_count > 0:
        return f"{event_count} event(s) under review"
    top_pool_share = _float_or_none((security.get("attack_51") or {}).get("top_pool_share"))
    if top_pool_share is not None and top_pool_share > MINING_POOL_SHARE_THRESHOLD:
        return f"mining pool concentration {top_pool_share:.1f}%"
    return "normal"


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_zero(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _queue_pending_signal(
    signal: Signal,
    policy: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any]:
    queue = _prune_pending_queue(state.get("pending_signal_queue", []))
    incoming = {
        "signal": asdict(signal),
        "score": int(policy["score"]),
        "queued_at": _utc_now_iso(),
    }
    for index, item in enumerate(queue):
        existing_signal = item.get("signal", {})
        if existing_signal.get("signal_type") != signal.signal_type:
            continue
        existing_score = int(item.get("score", 0))
        if existing_score > int(policy["score"]):
            state["pending_signal_queue"] = queue
            return {"queued": False, "reason": "lower_priority_duplicate"}
        queue[index] = incoming
        state["pending_signal_queue"] = queue
        return {"queued": True, "reason": "queued"}
    queue.append(incoming)
    state["pending_signal_queue"] = queue
    return {"queued": True, "reason": "queued"}


def _drain_pending_signal_queue(settings: Settings, state: dict[str, Any]) -> dict[str, Any] | None:
    queue = _prune_pending_queue(state.get("pending_signal_queue", []))
    state["pending_signal_queue"] = queue
    if not queue:
        return None
    status = get_x_status(settings)
    if int(status["daily_limit_remaining"]) <= 0:
        record_x_block("daily_limit_reached")
        return None
    if int(status["cooldown_remaining_seconds"]) > 0:
        record_x_block("cooldown_active")
        return None
    selected = max(
        queue,
        key=lambda item: (int(item.get("score", 0)), str(item.get("queued_at") or "")),
    )
    queue.remove(selected)
    state["pending_signal_queue"] = queue
    signal = _signal_from_dict(selected["signal"])
    post_result = post_to_x(
        signal.post_text,
        settings,
        event_id=signal.duplicate_key,
        signal_type=signal.signal_type,
        bypass_cooldown=False,
    )
    if not (post_result.posted or post_result.reason == "x_posting_disabled"):
        queue.append(selected)
        state["pending_signal_queue"] = queue
    if post_result.posted:
        _remember_signal(state, signal, time.time())
    return {
        **asdict(signal),
        "posted": post_result.posted,
        "suppressed_reason": post_result.reason,
        "selected_from_queue": True,
        "score": selected["score"],
        **({"error": post_result.error} if post_result.error else {}),
    }


def _prune_pending_queue(queue: Any) -> list[dict[str, Any]]:
    if not isinstance(queue, list):
        return []
    cutoff = time.time() - PENDING_SIGNAL_MAX_AGE_SECONDS
    pruned = []
    for item in queue:
        if not isinstance(item, dict) or "signal" not in item:
            continue
        queued_at = _parse_iso_to_epoch(item.get("queued_at"))
        if queued_at is not None and queued_at >= cutoff:
            pruned.append(item)
    return pruned


def _signal_from_dict(data: dict[str, Any]) -> Signal:
    return Signal(
        signal_type=str(data["signal_type"]),
        severity=str(data["severity"]),
        message=str(data["message"]),
        post_text=str(data["post_text"]),
        duplicate_key=str(data["duplicate_key"]),
        immediate=bool(data["immediate"]),
        detected_at=str(data["detected_at"]),
        source=dict(data.get("source") or {}),
    )


def _latest_fee(fee_data) -> float | None:
    if fee_data is None or fee_data.empty:
        return None
    try:
        return float(fee_data["sat_per_vbyte"].iloc[-1])
    except (KeyError, TypeError, ValueError):
        return None


def _parse_iso_to_epoch(value: Any) -> float | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _signal_amount_btc(signal: Signal) -> float | None:
    for key in ("amount_btc", "value_btc"):
        value = signal.source.get(key)
        if value is not None:
            return _float_or_none(value)
    return None


def _policy_result(
    allowed: bool,
    reason: str,
    cooldown_applied: bool,
    severity: str,
    score: int,
) -> dict[str, Any]:
    return {
        "allowed": allowed,
        "reason": reason,
        "cooldown_applied": cooldown_applied,
        "severity": severity,
        "score": score,
    }
