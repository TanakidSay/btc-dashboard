from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from collections import deque
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from html import unescape
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import urlparse

import pandas as pd
import requests
from requests.auth import HTTPBasicAuth

from .config import BASE_DIR, Settings

session = requests.Session()
logger = logging.getLogger(__name__)
_analytics_salt_warning_logged = False

API_HEADERS = {"Accept": "application/json", "User-Agent": "btc-dashboard/0.1"}
BROWSER_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}
FALLBACK_NODE_COUNT = "N/A"
SAFE_SECURITY_RISK = "low"
BITCOIN_MAX_SUPPLY_BTC = 21_000_000
SATOSHI_ESTIMATED_BTC = 1_100_000
LOST_BTC_ESTIMATE_RANGE = {"low": 3_000_000, "high": 4_000_000}
ETF_FUNDS_ESTIMATED_BTC = 1_400_000
GOVERNMENTS_ESTIMATED_BTC = 530_000
EXCHANGES_ESTIMATED_BTC = 2_200_000
MINERS_ESTIMATED_BTC = 1_800_000
ETF_MAX_AGE_DAYS = 7
BANGKOK_UTC_OFFSET_HOURS = 7
BANGKOK_PRICE_BASELINE_HOUR = 7
SATS_PER_BTC = 100_000_000
BTC_PRICE_TTL_SECONDS = 5
FEE_MEMPOOL_TTL_SECONDS = 30
HASHRATE_TTL_SECONDS = 10 * 60
NODE_COUNT_TTL_SECONDS = 30 * 60
INSTITUTIONAL_TTL_SECONDS = 60 * 60
TREASURY_TTL_SECONDS = 24 * 60 * 60
FEAR_GREED_TTL_SECONDS = 60 * 60
SECURITY_TTL_SECONDS = 30 * 60
VIEWER_ANALYTICS_RETENTION_DAYS = 30
BITNODES_LATEST_SNAPSHOT_URL = "https://bitnodes.io/api/v1/snapshots/latest/"
MEMPOOL_RECENT_TX_URL = "https://mempool.space/api/mempool/recent"
BINANCE_BTC_TICKER_URL = "https://api.binance.com/api/v3/ticker/24hr?symbol=BTCUSDT"
COINGLASS_BTC_ETF_FLOW_URL = "https://open-api-v4.coinglass.com/api/etf/bitcoin/flow-history"
SOSOVALUE_BTC_ETF_FLOW_URL = "https://api.sosovalue.xyz/openapi/v2/etf/historicalInflowChart"
FARSIDE_BTC_ETF_FLOW_URL = "https://farside.co.uk/bitcoin-etf-flow-all-data/"
FARSIDE_BTC_ETF_LATEST_URL = "https://farside.co.uk/btc/"
FARSIDE_BTC_ETF_READER_URL = "https://r.jina.ai/http://https://farside.co.uk/btc/"
FARSIDE_BTC_ETF_FLOW_READER_URL = (
    "https://r.jina.ai/http://https://farside.co.uk/bitcoin-etf-flow-all-data/"
)
FARSIDE_BTC_ETF_READER_URLS = (
    FARSIDE_BTC_ETF_READER_URL,
    FARSIDE_BTC_ETF_FLOW_READER_URL,
    "https://r.jina.ai/http://r.jina.ai/http://https://farside.co.uk/btc/",
    "https://r.jina.ai/http://r.jina.ai/http://https://farside.co.uk/bitcoin-etf-flow-all-data/",
)
BITBO_BTC_ETF_FLOW_URL = "https://bitbo.io/treasuries/etf-flows/"
WALLETPILOT_BTC_ETF_URL = "https://www.walletpilot.com/bitcoin-tracker/etfs"
GLOBALCOINGUIDE_BTC_ETF_URL = "https://globalcoinguide.com/research/data/etf-flows"
ALTERNATIVE_FEAR_GREED_URL = "https://api.alternative.me/fng/?limit=30&format=json"
BUNDLED_ETF_FLOW_PATH = BASE_DIR / "data/etf_flows.json"
COINGECKO_TREASURY_URLS = (
    "https://api.coingecko.com/api/v3/companies/public_treasury/bitcoin",
)
FALLBACK_ETF_FLOW = {
    "latest_date": "",
    "latest_net_flow_usd": 0,
    "7d_flow": 0,
    "trend": "neutral",
    "flow_history": [],
    "source": "fallback",
    "is_fallback": True,
    "is_stale": True,
    "source_label": "Fallback estimate",
    "data_note": "ETF flow history unavailable.",
    "updated_at": "",
    "status": "error",
    "error": "",
}
SEEDED_ETF_FLOW_MILLIONS = [
    ("11 Feb 2026", -276.3),
    ("12 Feb 2026", -410.2),
    ("13 Feb 2026", 15.1),
    ("17 Feb 2026", -104.9),
    ("18 Feb 2026", -133.3),
    ("19 Feb 2026", -165.8),
    ("20 Feb 2026", 88.1),
    ("23 Feb 2026", -203.8),
    ("24 Feb 2026", 257.7),
    ("25 Feb 2026", 506.6),
    ("26 Feb 2026", 254.4),
    ("27 Feb 2026", -27.5),
    ("02 Mar 2026", 458.2),
    ("03 Mar 2026", 225.2),
    ("04 Mar 2026", 461.9),
    ("05 Mar 2026", -227.9),
    ("06 Mar 2026", -348.9),
    ("09 Mar 2026", 167.1),
    ("10 Mar 2026", 246.9),
    ("11 Mar 2026", 115.2),
    ("12 Mar 2026", 53.8),
    ("13 Mar 2026", 180.4),
    ("16 Mar 2026", 199.4),
    ("17 Mar 2026", 199.4),
    ("18 Mar 2026", -163.5),
    ("19 Mar 2026", -90.2),
    ("27 Apr 2026", 87.6),
    ("28 Apr 2026", 173.2),
    ("29 Apr 2026", -41.5),
    ("30 Apr 2026", 204.8),
    ("01 May 2026", 118.9),
    ("04 May 2026", 532.3),
    ("05 May 2026", 467.3),
    ("06 May 2026", 46.2),
    ("07 May 2026", -268.5),
    ("08 May 2026", -145.7),
    ("11 May 2026", 27.2),
]
FALLBACK_BTC_TREASURY = {
    "total_btc_held": "N/A",
    "treasury_dominance_percent": "N/A",
    "top_holders": [],
    "source": "fallback",
    "source_label": "Treasury unavailable",
    "status": "error",
    "updated_at": None,
    "error": "",
    "data_note": "Treasury data is unavailable.",
}
FALLBACK_FEAR_GREED = {
    "value": "N/A",
    "classification": "N/A",
    "historical": {},
    "source": "alternative.me",
    "source_label": "Alternative.me",
    "status": "error",
    "updated_at": "",
    "data_timestamp": "",
    "data_note": "Fear & Greed data is unavailable.",
    "error": "",
}
ESTIMATED_BTC_TREASURY = {
    "total_btc_held": 1_271_929,
    "treasury_dominance_percent": 6.06,
    "top_holders": [
        {
            "name": "Strategy",
            "symbol": "MSTR.US",
            "btc_held": 843_738,
            "supply_percent": 4.018,
        },
        {
            "name": "XXI",
            "symbol": "XXI.US",
            "btc_held": 43_514,
            "supply_percent": 0.207,
        },
        {
            "name": "Metaplanet",
            "symbol": "3350.T",
            "btc_held": 40_177,
            "supply_percent": 0.191,
        },
        {
            "name": "MARA Holdings",
            "symbol": "MARA.US",
            "btc_held": 35_303,
            "supply_percent": 0.168,
        },
        {
            "name": "Bitcoin Standard Treasury Company",
            "symbol": "CEPO.US",
            "btc_held": 30_021,
            "supply_percent": 0.143,
        },
        {
            "name": "Galaxy Digital Holdings Ltd",
            "symbol": "GLXY.US",
            "btc_held": 25_723,
            "supply_percent": 0.122,
        },
        {
            "name": "Bullish",
            "symbol": "BLSH.US",
            "btc_held": 23_300,
            "supply_percent": 0.111,
        },
        {
            "name": "SpaceX",
            "symbol": "",
            "btc_held": 18_712,
            "supply_percent": 0.089,
        },
        {
            "name": "Riot Platforms",
            "symbol": "RIOT.US",
            "btc_held": 15_680,
            "supply_percent": 0.075,
        },
        {
            "name": "Coinbase Global",
            "symbol": "COIN.US",
            "btc_held": 15_389,
            "supply_percent": 0.073,
        },
    ],
    "source": "coingecko-treasury-estimate",
    "source_label": "CoinGecko estimate",
    "status": "fallback",
    "updated_at": "2026-05-23T00:00:00Z",
    "error": "Live treasury source unavailable; using checked public estimate data",
    "data_note": "Treasury data is using checked public estimate data from CoinGecko.",
}
FALLBACK_SUPPLY_OWNERSHIP = {
    "circulating_supply": "N/A",
    "max_supply": BITCOIN_MAX_SUPPLY_BTC,
    "remaining_to_mine": "N/A",
    "percent_mined": "N/A",
    "estimated_lost_btc": deepcopy(LOST_BTC_ESTIMATE_RANGE),
    "effective_liquid_supply": {"low": "N/A", "high": "N/A"},
    "categories": [],
    "insights": [],
    "max_supply_btc": BITCOIN_MAX_SUPPLY_BTC,
    "circulating_supply_btc": "N/A",
    "ownership": [],
    "top_holders": [],
    "source": "fallback",
    "status": "error",
    "updated_at": None,
    "error": "",
    "note": "Bitcoin addresses are pseudonymous, so owner attribution is estimated.",
}
DEFAULT_VIEWER_STATS = {
    "total_views": 0,
    "unique_visitors": 0,
    "last_viewed_at": None,
    "known_visitors": [],
    "suppressed_views": 0,
    "dedupe_window_seconds": 60,
    "recent_view_fingerprints": {},
}
DEFAULT_VIEWER_ANALYTICS = {
    "total_events": 0,
    "suppressed_events": 0,
    "dedupe_window_seconds": 60,
    "last_viewed_at": None,
    "sources": {},
    "referrers": {},
    "devices": {},
    "browsers": {},
    "countries": {},
    "paths": {},
    "recent": [],
    "recent_fingerprints": {},
    "visitor_events": [],
}


class DataSourceError(RuntimeError):
    pass


@dataclass
class CacheEntry:
    value: Any
    expires_at: float


@dataclass(frozen=True)
class MetricValue:
    value: Any
    source: str
    change_24h_usd: float | None = None
    change_24h_percent: float | None = None
    is_cached: bool = False


@dataclass
class PersistentCache:
    data: Any
    last_updated: datetime | None = None
    status: str = "empty"


@dataclass
class DashboardState:
    lock: Lock = field(default_factory=Lock)
    fee_data: pd.DataFrame | None = None
    table_html: str = ""
    hashrate: float | None = None
    node_count: int | str | None = None
    btc_price: float | None = None
    btc_change_24h_usd: float | None = None
    btc_change_24h_percent: float | None = None
    btc_price_source: str = "unknown"
    btc_price_is_cached: bool = True
    hashrate_history: deque[float] = field(default_factory=deque)
    price_history: deque[float] = field(default_factory=deque)
    time_labels: deque[str] = field(default_factory=deque)
    price_points: deque[dict[str, Any]] = field(default_factory=deque)
    hashrate_points: deque[dict[str, Any]] = field(default_factory=deque)
    metric_timestamps: dict[str, str] = field(default_factory=dict)
    last_fee_spike_notification_key: str | None = None
    last_fee_spike_notification_ts: float = 0


state = DashboardState()
_cache: dict[str, CacheEntry] = {}
_cache_refreshing: set[str] = set()
_cache_lock = Lock()
_viewer_lock = Lock()
_alert_history_lock = Lock()
_treasury_cache_lock = Lock()
_treasury_result_cache: CacheEntry | None = None
_last_successful_treasury: dict[str, Any] | None = None
_persistent_cache_lock = Lock()
_persistent_cache_refreshing: set[str] = set()
_persistent_caches: dict[str, PersistentCache] = {
    "treasury_cache": PersistentCache(deepcopy(FALLBACK_BTC_TREASURY)),
    "ownership_cache": PersistentCache(deepcopy(FALLBACK_SUPPLY_OWNERSHIP)),
    "institutional_cache": PersistentCache({}),
    "etf_cache": PersistentCache(deepcopy(FALLBACK_ETF_FLOW)),
    "fear_greed_cache": PersistentCache(deepcopy(FALLBACK_FEAR_GREED)),
    "security_cache": PersistentCache({}),
}


def configure_state(settings: Settings) -> None:
    with state.lock:
        state.hashrate_history = deque(state.hashrate_history, maxlen=settings.max_chart_rows)
        state.price_history = deque(state.price_history, maxlen=settings.max_chart_rows)
        state.time_labels = deque(state.time_labels, maxlen=settings.max_chart_rows)
        state.price_points = deque(state.price_points, maxlen=24 * 60)
        state.hashrate_points = deque(state.hashrate_points, maxlen=7 * 24 * 12)


def load_fee_data(path: Path, max_rows: int) -> pd.DataFrame:
    path = Path(path)
    if not path.exists():
        return pd.DataFrame(columns=["height", "tx_count", "total_fee_btc", "sat_per_vbyte"])
    return pd.read_csv(path).tail(max_rows)


def get_viewer_stats(settings: Settings) -> dict[str, Any]:
    with _viewer_lock:
        stats = _load_viewer_stats(settings.viewer_stats_path)
        stats = _ensure_unique_visitors_floor(
            stats,
            settings.viewer_stats_initial_unique,
        )
        stats["total_views"] = load_total_views(
            settings.view_counter_path,
            fallback=max(
                int(stats.get("total_views") or 0),
                settings.view_counter_initial_total,
            ),
        )
        _save_viewer_stats(settings.viewer_stats_path, stats)
    return _public_viewer_stats(stats)


def get_viewer_analytics(settings: Settings) -> dict[str, Any]:
    with _viewer_lock:
        analytics = _load_viewer_analytics(settings.viewer_analytics_path)
        _prune_viewer_events(analytics, _viewer_today_utc())
        _save_viewer_analytics(settings.viewer_analytics_path, analytics)
    return _public_viewer_analytics(analytics)


def record_view(
    settings: Settings,
    remote_addr: str | None,
    user_agent: str | None,
    referrer: str | None = None,
    path: str | None = None,
    country: str | None = None,
    accept_language: str | None = None,
    visitor_key: str | None = None,
) -> dict[str, Any]:
    visitor_key = visitor_key or _viewer_key(
        remote_addr,
        user_agent,
        accept_language=accept_language,
        country=country,
    )
    with _viewer_lock:
        stats = _load_viewer_stats(settings.viewer_stats_path)
        stats = _ensure_unique_visitors_floor(
            stats,
            settings.viewer_stats_initial_unique,
        )
        current_total_views = load_total_views(
            settings.view_counter_path,
            fallback=max(
                int(stats.get("total_views") or 0),
                settings.view_counter_initial_total,
            ),
        )
        stats["total_views"] = current_total_views
        if _should_count_view(stats, visitor_key, path):
            stats["total_views"] = increment_total_views(
                settings.view_counter_path,
                fallback=max(current_total_views, settings.view_counter_initial_total),
            )
        else:
            stats["suppressed_views"] = max(int(stats.get("suppressed_views") or 0), 0) + 1
        if visitor_key not in stats["known_visitors"]:
            stats["known_visitors"].append(visitor_key)
        stats["unique_visitors"] = len(stats["known_visitors"])
        stats["last_viewed_at"] = _viewer_now_iso()
        _save_viewer_stats(settings.viewer_stats_path, stats)
        analytics = _load_viewer_analytics(settings.viewer_analytics_path)
        _record_viewer_analytics(
            analytics,
            visitor_key=visitor_key,
            user_agent=user_agent,
            referrer=referrer,
            path=path,
            country=country,
            viewed_at=stats["last_viewed_at"],
        )
        _save_viewer_analytics(settings.viewer_analytics_path, analytics)
    return _public_viewer_stats(stats)


def get_privacy_safe_visitor_key(req) -> str:
    forwarded_for = (req.headers.get("X-Forwarded-For") or "").split(",", 1)[0].strip()
    remote_addr = (
        req.headers.get("CF-Connecting-IP")
        or forwarded_for
        or req.remote_addr
        or "unknown"
    )
    return _viewer_key(
        remote_addr,
        req.headers.get("User-Agent"),
        accept_language=req.headers.get("Accept-Language"),
        country=req.headers.get("CF-IPCountry") or req.headers.get("X-Country-Code"),
    )


def _viewer_key(
    remote_addr: str | None,
    user_agent: str | None,
    *,
    accept_language: str | None = None,
    country: str | None = None,
) -> str:
    fingerprint = "|".join((
        _analytics_salt(),
        remote_addr or "unknown",
        user_agent or "unknown",
        accept_language or "unknown",
        country or "unknown",
    ))
    return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()


def _analytics_salt() -> str:
    global _analytics_salt_warning_logged
    salt = os.getenv("ANALYTICS_SALT")
    if salt:
        return salt
    if not _analytics_salt_warning_logged:
        logger.warning("ANALYTICS_SALT is not set; using stable fallback analytics salt")
        _analytics_salt_warning_logged = True
    return "btcwindow-stable-analytics-salt"


def load_total_views(path: Path, fallback: int = 0) -> int:
    safe_fallback = max(int(fallback or 0), 0)
    if not path.exists():
        save_total_views(path, safe_fallback)
        return safe_fallback
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        total_views = int(payload.get("total_views", safe_fallback))
        return max(total_views, 0)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.warning("view counter load failed: %s", exc)
        save_total_views(path, safe_fallback)
        return safe_fallback


def save_total_views(path: Path, total_views: int) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"total_views": max(int(total_views), 0)}, indent=2),
            encoding="utf-8",
        )
    except (OSError, TypeError, ValueError) as exc:
        logger.warning("view counter save failed: %s", exc)


def increment_total_views(path: Path, fallback: int = 0, floor: int = 0) -> int:
    total_views = ensure_total_views_floor(path, load_total_views(path, fallback=fallback), floor)
    total_views += 1
    save_total_views(path, total_views)
    return total_views


def load_recent_alerts(settings: Settings, limit: int = 5) -> list[dict[str, Any]]:
    with _alert_history_lock:
        return _load_alert_history(settings.alerts_history_path)[:limit]


def record_alert_history(
    settings: Settings,
    alerts: list[dict[str, Any]],
    limit: int = 20,
) -> list[dict[str, Any]]:
    if not alerts:
        return load_recent_alerts(settings)
    now_iso = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    with _alert_history_lock:
        history = _load_alert_history(settings.alerts_history_path)
        known_keys = {str(item.get("event_key") or "") for item in history}
        for alert in alerts:
            event_key = _alert_event_key(alert)
            if event_key in known_keys:
                continue
            history.insert(0, {
                "event_key": event_key,
                "type": alert.get("type", "alert"),
                "severity": alert.get("severity", "medium"),
                "status": alert.get("status", "yellow"),
                "message": alert.get("message", ""),
                "action": alert.get("action", ""),
                "recorded_at": now_iso,
            })
            known_keys.add(event_key)
        history = history[:limit]
        _save_alert_history(settings.alerts_history_path, history)
        return deepcopy(history[:5])


def _alert_event_key(alert: dict[str, Any]) -> str:
    parts = [
        str(alert.get("type") or "alert"),
        str(alert.get("message") or ""),
        str(alert.get("height") or ""),
        str(alert.get("txid") or ""),
        str(alert.get("threshold") or ""),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]


def _load_alert_history(path: Path) -> list[dict[str, Any]]:
    path = Path(path)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _save_alert_history(path: Path, history: list[dict[str, Any]]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(f"{path.suffix}.tmp")
    temp_path.write_text(json.dumps(history, indent=2), encoding="utf-8")
    temp_path.replace(path)


def ensure_total_views_floor(path: Path, total_views: int, floor: int = 0) -> int:
    safe_total = max(int(total_views or 0), 0)
    safe_floor = max(int(floor or 0), 0)
    if safe_total >= safe_floor:
        return safe_total
    save_total_views(path, safe_floor)
    return safe_floor


def _load_viewer_stats(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _default_viewer_stats()
    try:
        stats = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _default_viewer_stats()
    merged = _default_viewer_stats()
    merged.update(stats)
    if not isinstance(merged["known_visitors"], list):
        merged["known_visitors"] = []
    if not isinstance(merged.get("recent_view_fingerprints"), dict):
        merged["recent_view_fingerprints"] = {}
    merged["unique_visitors"] = len(merged["known_visitors"])
    merged["suppressed_views"] = max(int(merged.get("suppressed_views") or 0), 0)
    merged["dedupe_window_seconds"] = max(int(merged.get("dedupe_window_seconds") or 60), 1)
    return merged


def _ensure_unique_visitors_floor(stats: dict[str, Any], floor: int = 0) -> dict[str, Any]:
    safe_floor = max(int(floor or 0), 0)
    known_visitors = stats["known_visitors"]
    missing = safe_floor - len(known_visitors)
    if missing > 0:
        known_visitors.extend(f"seeded-visitor-{index}" for index in range(missing))
    stats["unique_visitors"] = len(known_visitors)
    return stats


def _save_viewer_stats(path: Path, stats: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(stats, indent=2), encoding="utf-8")


def _should_count_view(stats: dict[str, Any], visitor_key: str, path: str | None) -> bool:
    dedupe_window_seconds = max(int(stats.get("dedupe_window_seconds") or 60), 1)
    now = time.time()
    fingerprints = stats["recent_view_fingerprints"]
    _prune_fingerprints(fingerprints, now, dedupe_window_seconds)
    fingerprint = _viewer_analytics_fingerprint(visitor_key, "view", _viewer_path(path))
    last_seen = _safe_float(fingerprints.get(fingerprint))
    fingerprints[fingerprint] = now
    return last_seen is None or now - last_seen >= dedupe_window_seconds


def _load_viewer_analytics(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _default_viewer_analytics()
    try:
        analytics = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _default_viewer_analytics()
    merged = _default_viewer_analytics()
    merged.update(analytics)
    for key in ("sources", "referrers", "devices", "browsers", "countries", "paths"):
        if not isinstance(merged.get(key), dict):
            merged[key] = {}
    if not isinstance(merged.get("recent"), list):
        merged["recent"] = []
    if not isinstance(merged.get("recent_fingerprints"), dict):
        merged["recent_fingerprints"] = {}
    if not isinstance(merged.get("visitor_events"), list):
        merged["visitor_events"] = []
    merged["total_events"] = max(int(merged.get("total_events") or 0), 0)
    merged["suppressed_events"] = max(int(merged.get("suppressed_events") or 0), 0)
    merged["dedupe_window_seconds"] = max(int(merged.get("dedupe_window_seconds") or 60), 1)
    _prune_viewer_events(merged, _viewer_today_utc())
    return merged


def _save_viewer_analytics(path: Path, analytics: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(analytics, indent=2), encoding="utf-8")


def _record_viewer_analytics(
    analytics: dict[str, Any],
    visitor_key: str,
    user_agent: str | None,
    referrer: str | None,
    path: str | None,
    country: str | None,
    viewed_at: str | None,
) -> None:
    source = _viewer_source(referrer)
    referrer_host = _viewer_referrer_host(referrer)
    device = _viewer_device(user_agent)
    browser = _viewer_browser(user_agent)
    country_code = _viewer_country(country)
    safe_path = _viewer_path(path)
    dedupe_window_seconds = max(int(analytics.get("dedupe_window_seconds") or 60), 1)
    now = time.time()
    _prune_viewer_fingerprints(analytics, now, dedupe_window_seconds)
    fingerprint = _viewer_analytics_fingerprint(visitor_key, source, safe_path)
    recent_fingerprints = analytics["recent_fingerprints"]
    last_seen = _safe_float(recent_fingerprints.get(fingerprint))
    if last_seen is not None and now - last_seen < dedupe_window_seconds:
        analytics["suppressed_events"] = max(int(analytics.get("suppressed_events") or 0), 0) + 1
        recent_fingerprints[fingerprint] = now
        analytics["last_viewed_at"] = viewed_at
        return
    recent_fingerprints[fingerprint] = now

    analytics["total_events"] = max(int(analytics.get("total_events") or 0), 0) + 1
    analytics["last_viewed_at"] = viewed_at
    _increment_bucket(analytics["sources"], source)
    _increment_bucket(analytics["referrers"], referrer_host)
    _increment_bucket(analytics["devices"], device)
    _increment_bucket(analytics["browsers"], browser)
    _increment_bucket(analytics["countries"], country_code)
    _increment_bucket(analytics["paths"], safe_path)
    analytics["recent"] = (analytics.get("recent") or [])[-49:] + [
        {
            "viewed_at": viewed_at,
            "source": source,
            "referrer": referrer_host,
            "device": device,
            "browser": browser,
            "country": country_code,
            "path": safe_path,
        }
    ]
    _record_viewer_event(
        analytics,
        visitor_key=visitor_key,
        viewed_at=viewed_at,
        source=source,
        referrer=referrer_host,
        device=device,
        browser=browser,
        country=country_code,
        path=safe_path,
    )


def _record_viewer_event(
    analytics: dict[str, Any],
    visitor_key: str,
    viewed_at: str | None,
    source: str,
    referrer: str,
    device: str,
    browser: str,
    country: str,
    path: str,
) -> None:
    analytics["visitor_events"] = (analytics.get("visitor_events") or []) + [
        {
            "visitor_key": visitor_key,
            "viewed_at": viewed_at,
            "source": source,
            "referrer": referrer,
            "device": device,
            "browser": browser,
            "country": country,
            "path": path,
        },
    ]
    _prune_viewer_events(analytics, _viewer_today_utc())


def _viewer_analytics_fingerprint(visitor_key: str, source: str, path: str) -> str:
    raw = f"{visitor_key}|{source}|{path}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _prune_viewer_fingerprints(
    analytics: dict[str, Any],
    now: float,
    dedupe_window_seconds: int,
) -> None:
    _prune_fingerprints(analytics["recent_fingerprints"], now, dedupe_window_seconds)


def _prune_viewer_events(analytics: dict[str, Any], today: date) -> None:
    cutoff = today - timedelta(days=VIEWER_ANALYTICS_RETENTION_DAYS - 1)
    analytics["visitor_events"] = [
        event
        for event in analytics.get("visitor_events") or []
        if (event_date := _viewer_event_date(event)) is not None and event_date >= cutoff
    ]


def _viewer_retention_metrics(analytics: dict[str, Any]) -> dict[str, Any]:
    today = _viewer_today_utc()
    start_7d = today - timedelta(days=6)
    visitor_days: dict[str, set[date]] = {}

    for event in _viewer_metric_events(analytics):
        visitor = _viewer_metric_event_key(event)
        event_date = _viewer_event_date(event)
        if not visitor or event_date is None or event_date < start_7d or event_date > today:
            continue
        visitor_days.setdefault(visitor, set()).add(event_date)

    unique_today = sum(1 for days in visitor_days.values() if today in days)
    unique_7d = len(visitor_days)
    returning_visitors = sum(1 for days in visitor_days.values() if len(days) > 1)
    returning_rate = returning_visitors / unique_7d * 100 if unique_7d else 0
    return {
        "unique_today": unique_today,
        "unique_7d": unique_7d,
        "returning_visitors": returning_visitors,
        "returning_rate": f"{returning_rate:.1f}%",
    }


def _viewer_metric_events(analytics: dict[str, Any]) -> list[dict[str, Any]]:
    events = [event for event in analytics.get("visitor_events") or [] if isinstance(event, dict)]
    seen_signatures = {_viewer_event_signature(event) for event in events}
    for event in analytics.get("recent") or []:
        if not isinstance(event, dict):
            continue
        signature = _viewer_event_signature(event)
        if signature in seen_signatures:
            continue
        events.append(event)
        seen_signatures.add(signature)
    return events


def _viewer_metric_event_key(event: dict[str, Any]) -> str:
    key = str(event.get("visitor_key") or event.get("visitor") or "").strip()
    if key:
        return key
    event_date = _viewer_event_date(event)
    if event_date is None:
        return ""
    bucket = _viewer_event_hour_bucket(event)
    raw = "|".join((
        "legacy",
        str(event.get("country") or "unknown"),
        str(event.get("browser") or "unknown"),
        str(event.get("device") or "unknown"),
        str(event.get("path") or "/"),
        str(event.get("referrer") or "direct"),
        str(event.get("source") or "direct"),
        bucket,
    ))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _viewer_event_signature(event: dict[str, Any]) -> str:
    return "|".join((
        str(event.get("viewed_at") or ""),
        str(event.get("source") or ""),
        str(event.get("referrer") or ""),
        str(event.get("device") or ""),
        str(event.get("browser") or ""),
        str(event.get("country") or ""),
        str(event.get("path") or ""),
    ))


def _viewer_event_hour_bucket(event: dict[str, Any]) -> str:
    viewed_at = event.get("viewed_at")
    if isinstance(viewed_at, str) and viewed_at:
        try:
            parsed = datetime.fromisoformat(viewed_at.replace("Z", "+00:00"))
            return parsed.replace(minute=0, second=0, microsecond=0).isoformat()
        except ValueError:
            pass
    event_date = _viewer_event_date(event)
    return event_date.isoformat() if event_date else ""


def _viewer_event_date(event: dict[str, Any]) -> date | None:
    viewed_at = event.get("viewed_at")
    if isinstance(viewed_at, str) and viewed_at:
        try:
            return datetime.fromisoformat(viewed_at.replace("Z", "+00:00")).date()
        except ValueError:
            pass
    date_value = event.get("date")
    if isinstance(date_value, str) and date_value:
        try:
            return datetime.fromisoformat(date_value).date()
        except ValueError:
            return None
    return None


def _viewer_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time()))


def _viewer_today_utc() -> date:
    return datetime.fromtimestamp(time.time(), UTC).date()


def _prune_fingerprints(
    recent_fingerprints: dict[str, Any],
    now: float,
    dedupe_window_seconds: int,
) -> None:
    stale_keys = [
        key
        for key, last_seen in recent_fingerprints.items()
        if (_safe_float(last_seen) or 0) < now - dedupe_window_seconds
    ]
    for key in stale_keys:
        recent_fingerprints.pop(key, None)


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _increment_bucket(bucket: dict[str, int], key: str) -> None:
    bucket[key] = int(bucket.get(key, 0) or 0) + 1


def _viewer_source(referrer: str | None) -> str:
    host = _viewer_referrer_host(referrer)
    if host == "direct":
        return "direct"
    if _viewer_host_matches(host, ("x.com", "twitter.com", "t.co")):
        return "x"
    if _viewer_host_matches(host, ("youtube.com", "youtu.be", "youtube-nocookie.com")):
        return "youtube"
    if _viewer_host_matches(host, ("tiktok.com", "tiktokv.com")):
        return "tiktok"
    if _viewer_host_matches(host, ("google.",)):
        return "google"
    if _viewer_host_matches(host, ("facebook.",)):
        return "facebook"
    if _viewer_host_matches(host, ("reddit.",)):
        return "reddit"
    return "other"


def _viewer_host_matches(host: str, domains: tuple[str, ...]) -> bool:
    for domain in domains:
        if domain.endswith("."):
            if host.startswith(domain) or f".{domain}" in host:
                return True
            continue
        if host == domain or host.endswith(f".{domain}"):
            return True
    return False


def _viewer_referrer_host(referrer: str | None) -> str:
    if not referrer:
        return "direct"
    parsed = urlparse(referrer)
    host = (parsed.netloc or parsed.path or "").lower()
    if not host:
        return "direct"
    if host.startswith("www."):
        host = host[4:]
    return host.split("@")[-1].split(":")[0][:80] or "direct"


def _viewer_device(user_agent: str | None) -> str:
    agent = (user_agent or "").lower()
    if not agent:
        return "unknown"
    if "bot" in agent or "crawler" in agent or "spider" in agent:
        return "bot"
    if "mobile" in agent or "iphone" in agent or "android" in agent:
        return "mobile"
    if "ipad" in agent or "tablet" in agent:
        return "tablet"
    return "desktop"


def _viewer_browser(user_agent: str | None) -> str:
    agent = (user_agent or "").lower()
    if not agent:
        return "unknown"
    if "edg/" in agent:
        return "edge"
    if "chrome/" in agent and "chromium" not in agent:
        return "chrome"
    if "safari/" in agent and "chrome/" not in agent:
        return "safari"
    if "firefox/" in agent:
        return "firefox"
    if "bot" in agent or "crawler" in agent or "spider" in agent:
        return "bot"
    return "other"


def _viewer_country(country: str | None) -> str:
    value = (country or "").strip().upper()
    if len(value) == 2 and value.isalpha():
        return value
    return "unknown"


def _viewer_path(path: str | None) -> str:
    value = (path or "/").strip() or "/"
    if not value.startswith("/"):
        value = f"/{value}"
    return value[:120]


def _public_viewer_stats(stats: dict[str, Any]) -> dict[str, Any]:
    return {
        "total_views": stats["total_views"],
        "unique_visitors": stats["unique_visitors"],
        "last_viewed_at": stats["last_viewed_at"],
        "suppressed_views": stats.get("suppressed_views", 0),
        "dedupe_window_seconds": stats.get("dedupe_window_seconds", 60),
    }


def _default_viewer_stats() -> dict[str, Any]:
    return deepcopy(DEFAULT_VIEWER_STATS)


def _public_viewer_analytics(analytics: dict[str, Any]) -> dict[str, Any]:
    retention = _viewer_retention_metrics(analytics)
    return {
        "total_events": analytics["total_events"],
        "suppressed_events": analytics["suppressed_events"],
        "dedupe_window_seconds": analytics["dedupe_window_seconds"],
        "last_viewed_at": analytics["last_viewed_at"],
        **retention,
        "sources": analytics["sources"],
        "referrers": analytics["referrers"],
        "devices": analytics["devices"],
        "browsers": analytics["browsers"],
        "countries": analytics["countries"],
        "paths": analytics["paths"],
        "recent": analytics["recent"][-25:],
        "privacy": "Aggregate only; IP addresses are not stored.",
    }


def _default_viewer_analytics() -> dict[str, Any]:
    return deepcopy(DEFAULT_VIEWER_ANALYTICS)


def format_fee_value(value: float) -> str:
    if value > 5:
        color = "red"
    elif value < 2:
        color = "green"
    else:
        color = "orange"
    return f"<span style='color:{color}'>{value}</span>"


def build_table_html(df: pd.DataFrame, max_rows: int) -> str:
    if df.empty:
        return "<p>No fee data available.</p>"
    table_df = df.tail(max_rows).copy()
    table_df["sat_per_vbyte"] = table_df["sat_per_vbyte"].apply(format_fee_value)
    return table_df.to_html(index=False, escape=False, classes="data-table")


def _cache_get(key: str) -> Any | None:
    with _cache_lock:
        entry = _cache.get(key)
        if entry is None or entry.expires_at <= time.monotonic():
            return None
        logger.debug("cache served key=%s", key)
        return entry.value


def _cache_peek(key: str) -> Any | None:
    with _cache_lock:
        entry = _cache.get(key)
        return None if entry is None else entry.value


def _cache_set(key: str, value: Any, ttl_seconds: int) -> Any:
    with _cache_lock:
        _cache[key] = CacheEntry(value=value, expires_at=time.monotonic() + ttl_seconds)
    return value


def clear_cache() -> None:
    global _treasury_result_cache, _last_successful_treasury
    with _cache_lock:
        _cache.clear()
        _cache_refreshing.clear()
    with _treasury_cache_lock:
        _treasury_result_cache = None
        _last_successful_treasury = None
    with _persistent_cache_lock:
        _persistent_cache_refreshing.clear()
        _persistent_caches["treasury_cache"] = PersistentCache(deepcopy(FALLBACK_BTC_TREASURY))
        _persistent_caches["ownership_cache"] = PersistentCache(deepcopy(FALLBACK_SUPPLY_OWNERSHIP))
        _persistent_caches["institutional_cache"] = PersistentCache({})
        _persistent_caches["etf_cache"] = PersistentCache(deepcopy(FALLBACK_ETF_FLOW))
        _persistent_caches["fear_greed_cache"] = PersistentCache(deepcopy(FALLBACK_FEAR_GREED))
        _persistent_caches["security_cache"] = PersistentCache({})


def clear_etf_cache() -> None:
    with _persistent_cache_lock:
        _persistent_cache_refreshing.discard("etf_cache")
        _persistent_caches["etf_cache"] = PersistentCache(deepcopy(FALLBACK_ETF_FLOW))


def _cached(key: str, settings: Settings, loader):
    return _cached_for(key, settings.cache_ttl_seconds, loader)


def _cached_for(key: str, ttl_seconds: int, loader, refresh_log: str | None = None):
    cached_value = _cache_get(key)
    if cached_value is not None:
        return _cache_mark_cached(cached_value)
    stale_value = _cache_peek(key)
    with _cache_lock:
        if key in _cache_refreshing:
            if stale_value is not None:
                logger.info("refresh skipped key=%s reason=already_running", key)
                return _cache_mark_cached(stale_value)
            raise RuntimeError(f"{key} refresh already running")
        _cache_refreshing.add(key)
    try:
        loaded_value = loader()
        if _cache_value_is_empty(loaded_value) and not _cache_value_is_empty(stale_value):
            logger.warning("cache refresh kept stale key=%s reason=empty_refresh", key)
            return _cache_mark_cached(stale_value)
        _cache_set(key, loaded_value, ttl_seconds)
        if refresh_log:
            logger.info(refresh_log)
        return loaded_value
    except Exception as exc:
        if stale_value is not None:
            logger.warning("cache refresh failed key=%s; serving stale: %s", key, exc)
            return _cache_mark_cached(stale_value)
        raise
    finally:
        with _cache_lock:
            _cache_refreshing.discard(key)


def _cache_mark_cached(value: Any) -> Any:
    if isinstance(value, MetricValue):
        return replace(value, is_cached=True)
    return value


def _cache_value_is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, MetricValue):
        return _cache_value_is_empty(value.value)
    if isinstance(value, pd.DataFrame):
        return value.empty
    if isinstance(value, dict):
        return value == {} or value.get("status") == "error"
    if value == "N/A":
        return True
    return False


def _get_json(url: str, settings: Settings) -> Any:
    response = session.get(url, headers=API_HEADERS, timeout=settings.request_timeout)
    response.raise_for_status()
    return response.json()


def _get_json_with_headers(url: str, settings: Settings, headers: dict[str, str]) -> Any:
    response = session.get(url, headers=headers, timeout=settings.request_timeout)
    response.raise_for_status()
    return response.json()


def _post_json_with_headers(
    url: str,
    settings: Settings,
    headers: dict[str, str],
    payload: dict[str, Any],
) -> Any:
    response = session.post(url, headers=headers, json=payload, timeout=settings.request_timeout)
    response.raise_for_status()
    return response.json()


def _get_json_with_headers_retry(
    url: str,
    settings: Settings,
    headers: dict[str, str],
    attempts: int = 3,
) -> Any:
    last_error: Exception | None = None
    for _ in range(attempts):
        try:
            return _get_json_with_headers(url, settings, headers)
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise ValueError("request attempts exhausted")


def _get_text(url: str, settings: Settings) -> str:
    response = session.get(url, headers=API_HEADERS, timeout=settings.request_timeout)
    response.raise_for_status()
    return response.text.strip()


def _get_browser_text(url: str, settings: Settings) -> str:
    response = session.get(url, headers=BROWSER_HEADERS, timeout=settings.request_timeout)
    response.raise_for_status()
    return response.text.strip()


def rpc_call(method: str, params: list[Any], settings: Settings) -> Any:
    if not settings.bitcoin_rpc_password:
        raise DataSourceError("BITCOIN_RPC_PASSWORD is not configured")
    payload = {
        "jsonrpc": "1.0",
        "id": "btc-dashboard",
        "method": method,
        "params": params,
    }
    response = session.post(
        settings.bitcoin_rpc_url,
        json=payload,
        auth=HTTPBasicAuth(settings.bitcoin_rpc_user, settings.bitcoin_rpc_password),
        timeout=settings.request_timeout,
    )
    response.raise_for_status()
    body = response.json()
    if body.get("error"):
        raise DataSourceError(str(body["error"]))
    return body["result"]


def get_fee_data(settings: Settings) -> pd.DataFrame:
    return _cached_for(
        "fee_data",
        FEE_MEMPOOL_TTL_SECONDS,
        lambda: _load_fee_data_with_fallbacks(settings),
        "fees/mempool refreshed",
    ).copy()


def _load_fee_data_with_fallbacks(settings: Settings) -> pd.DataFrame:
    providers = (
        _get_fee_data_from_node,
        _get_fee_data_from_mempool,
    )
    for provider in providers:
        try:
            fee_data = provider(settings)
        except (DataSourceError, requests.RequestException, KeyError, TypeError, ValueError) as exc:
            logger.warning("%s failed: %s", provider.__name__, exc)
            continue
        if not fee_data.empty:
            return fee_data.tail(settings.max_csv_rows)
    return load_fee_data(settings.fee_csv_path, settings.max_csv_rows)


def _get_fee_data_from_node(settings: Settings) -> pd.DataFrame:
    current_hash = rpc_call("getbestblockhash", [], settings)
    rows: list[dict[str, float | int]] = []
    for _ in range(settings.node_block_count):
        block = rpc_call("getblock", [current_hash, 2], settings)
        coinbase_tx = block["tx"][0]
        coinbase_output = sum(float(vout["value"]) for vout in coinbase_tx["vout"])
        total_fee_btc = max(coinbase_output - settings.bitcoin_block_reward_btc, 0)
        block_weight = block.get("weight")
        if block_weight is None:
            block_weight = block["size"] * 4
        virtual_size = float(block_weight) / 4
        rows.append({
            "height": int(block["height"]),
            "tx_count": len(block["tx"]),
            "total_fee_btc": total_fee_btc,
            "sat_per_vbyte": (
                (total_fee_btc * 100_000_000) / virtual_size if virtual_size > 0 else 0
            ),
        })
        current_hash = block.get("previousblockhash")
        if not current_hash:
            break
    return pd.DataFrame(reversed(rows))


def _get_fee_data_from_mempool(settings: Settings) -> pd.DataFrame:
    blocks = _get_json("https://mempool.space/api/v1/blocks", settings)
    rows = []
    for block in blocks[: settings.node_block_count]:
        extras = block.get("extras", {})
        total_fee_sat = float(extras.get("totalFees", 0))
        virtual_size = float(extras.get("virtualSize") or block.get("weight", 0) / 4)
        rows.append({
            "height": int(block["height"]),
            "tx_count": int(block["tx_count"]),
            "total_fee_btc": total_fee_sat / 100_000_000,
            "sat_per_vbyte": total_fee_sat / virtual_size if virtual_size > 0 else 0,
        })
    return pd.DataFrame(reversed(rows))


def get_hashrate(settings: Settings) -> float | None:
    result = get_hashrate_result(settings)
    return None if result is None else result.value


def get_hashrate_result(settings: Settings) -> MetricValue | None:
    return _cached_for(
        "hashrate",
        HASHRATE_TTL_SECONDS,
        lambda: _get_hashrate_with_fallbacks(settings),
        "hashrate refreshed",
    )


def _get_hashrate_with_fallbacks(settings: Settings) -> float | None:
    providers = (
        _get_hashrate_from_node,
        _get_hashrate_from_mempool,
    )
    for provider in providers:
        try:
            hashrate = provider(settings)
        except (DataSourceError, requests.RequestException, KeyError, TypeError, ValueError) as exc:
            logger.warning("%s failed: %s", provider.__name__, exc)
            continue
        if hashrate is not None:
            return MetricValue(hashrate, _provider_source(provider.__name__))
    return None


def _get_hashrate_from_node(settings: Settings) -> float:
    return float(rpc_call("getnetworkhashps", [], settings)) / 1e12


def _get_hashrate_from_mempool(settings: Settings) -> float | None:
    data = _get_json("https://mempool.space/api/v1/mining/hashrate/3d", settings)
    current_hashrate = data.get("currentHashrate")
    if current_hashrate is None:
        hashrates = data.get("hashrates", [])
        if not hashrates:
            return None
        current_hashrate = hashrates[-1].get("avgHashrate")
    if current_hashrate is None:
        return None
    return float(current_hashrate) / 1e12


def get_hashrate_chart_points(settings: Settings) -> list[dict[str, Any]]:
    try:
        return _get_hashrate_chart_points_from_mempool(settings)
    except (requests.RequestException, KeyError, TypeError, ValueError) as exc:
        logger.warning("hashrate chart source failed: %s", exc)
        return []


def _get_hashrate_chart_points_from_mempool(settings: Settings) -> list[dict[str, Any]]:
    data = _get_json("https://mempool.space/api/v1/mining/hashrate/1w", settings)
    rows = data if isinstance(data, list) else data.get("hashrates", [])
    points: list[dict[str, Any]] = []
    for row in rows:
        avg_hashrate = _first_number(row, ("avgHashrate", "hashrate", "currentHashrate"))
        timestamp_value = row.get("timestamp") or row.get("time") or row.get("date")
        if avg_hashrate is None or timestamp_value is None:
            continue
        points.append({
            "timestamp": _normalize_timestamp(timestamp_value),
            "value": float(avg_hashrate) / 1e12,
        })
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for point in points:
        if point["timestamp"] in seen:
            continue
        seen.add(point["timestamp"])
        deduped.append(point)
    return deduped[-(7 * 24 * 12):]


def get_node_count(settings: Settings) -> int | str:
    result = get_node_count_result(settings)
    return FALLBACK_NODE_COUNT if result is None else result.value


def get_node_count_result(settings: Settings) -> MetricValue | None:
    return _cached_for(
        "node_count",
        NODE_COUNT_TTL_SECONDS,
        lambda: _get_node_count_with_fallbacks(settings),
        "nodes/security refreshed",
    )


def _get_node_count_with_fallbacks(settings: Settings) -> int | str:
    providers = (
        _get_node_count_from_bitnodes,
        _get_node_count_from_mempool_lightning,
    )

    for provider in providers:
        try:
            node_count = provider(settings)
        except (DataSourceError, requests.RequestException, KeyError, TypeError, ValueError) as exc:
            logger.warning("%s failed: %s", provider.__name__, exc)
            continue
        if node_count is not None:
            return MetricValue(node_count, _provider_source(provider.__name__))
    return MetricValue(FALLBACK_NODE_COUNT, "fallback")


def _get_node_count_from_node(settings: Settings) -> int:
    network_info = rpc_call("getnetworkinfo", [], settings)
    if "connections" in network_info:
        return int(network_info["connections"])
    return int(network_info.get("connections_in", 0)) + int(network_info.get("connections_out", 0))


def _get_node_count_from_bitnodes(settings: Settings) -> int:
    data = _get_json(BITNODES_LATEST_SNAPSHOT_URL, settings)
    return int(data["total_nodes"])


def _get_node_count_from_mempool_lightning(settings: Settings) -> int:
    data = _get_json("https://mempool.space/api/v1/lightning/statistics/latest", settings)
    return int(data["latest"]["node_count"])


def get_btc_price(settings: Settings) -> float | None:
    result = get_btc_price_result(settings)
    return None if result is None else result.value


def get_btc_price_result(settings: Settings) -> MetricValue | None:
    return _cached_for(
        "btc_price",
        BTC_PRICE_TTL_SECONDS,
        lambda: _get_btc_price_with_fallbacks(settings),
        "BTC price refreshed",
    )


def _get_btc_price_with_fallbacks(settings: Settings) -> MetricValue | None:
    providers = (
        _get_btc_price_from_binance,
        _get_btc_price_from_coingecko,
        _get_btc_price_from_mempool,
    )
    for provider in providers:
        try:
            price = provider(settings)
        except (requests.RequestException, KeyError, TypeError, ValueError) as exc:
            logger.warning("%s failed: %s", provider.__name__, exc)
            continue
        if price is not None:
            if isinstance(price, MetricValue):
                return _apply_daily_price_baseline(price, settings)
            return _apply_daily_price_baseline(
                MetricValue(price, _provider_source(provider.__name__)),
                settings,
            )
    return None


def _get_btc_price_from_binance(settings: Settings) -> MetricValue:
    data = _get_json(BINANCE_BTC_TICKER_URL, settings)
    return MetricValue(
        value=float(data["lastPrice"]),
        source="binance",
        change_24h_usd=float(data["priceChange"]),
        change_24h_percent=float(data["priceChangePercent"]),
    )


def _get_btc_price_from_mempool(settings: Settings) -> float | None:
    data = _get_json("https://mempool.space/api/v1/prices", settings)
    usd_price = data.get("USD")
    if usd_price is None:
        return None
    return float(usd_price)


def _get_btc_price_from_coingecko(settings: Settings) -> float:
    data = _get_json(
        (
            "https://api.coingecko.com/api/v3/simple/price?"
            "ids=bitcoin&vs_currencies=usd&include_24hr_change=true"
        ),
        settings,
    )
    current_price = float(data["bitcoin"]["usd"])
    change_percent = data["bitcoin"].get("usd_24h_change")
    change_usd = None
    if change_percent is not None:
        percent = float(change_percent)
        previous_price = current_price / (1 + (percent / 100))
        change_usd = current_price - previous_price
        return MetricValue(current_price, "coingecko", change_usd, percent)
    return MetricValue(current_price, "coingecko")


def _apply_daily_price_baseline(metric: MetricValue, settings: Settings) -> MetricValue:
    baseline = _get_or_create_daily_price_baseline(settings.btc_price_baseline_path, metric.value)
    if baseline <= 0:
        return metric
    change_usd = round(float(metric.value) - baseline, 2)
    change_percent = round((change_usd / baseline) * 100, 4)
    return replace(
        metric,
        change_24h_usd=change_usd,
        change_24h_percent=change_percent,
    )


def _get_or_create_daily_price_baseline(path: Path, current_price: float) -> float:
    session_date = _bangkok_price_session_date(_utc_now_dt())
    baseline = _load_daily_price_baseline(path)
    if (
        baseline.get("session_date") == session_date
        and _to_float_or_none(baseline.get("baseline_price_usd")) is not None
    ):
        return float(baseline["baseline_price_usd"])

    price = float(current_price)
    _save_daily_price_baseline(path, session_date, price)
    return price


def _load_daily_price_baseline(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as file:
            payload = json.load(file)
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_daily_price_baseline(path: Path, session_date: str, price: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "session_date": session_date,
        "baseline_price_usd": round(float(price), 2),
        "locked_at": _utc_now_iso(),
        "timezone": "Asia/Bangkok",
        "baseline_hour": BANGKOK_PRICE_BASELINE_HOUR,
    }
    with path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2)
        file.write("\n")


def _bangkok_price_session_date(now_utc: datetime) -> str:
    bangkok_now = now_utc + timedelta(hours=BANGKOK_UTC_OFFSET_HOURS)
    if bangkok_now.hour < BANGKOK_PRICE_BASELINE_HOUR:
        bangkok_now -= timedelta(days=1)
    return bangkok_now.date().isoformat()


def get_etf_flow(settings: Settings) -> dict[str, Any]:
    return _cached_resource(
        "etf_cache",
        max(settings.etf_flow_ttl_seconds, 60 * 60),
        lambda: _get_etf_flow_with_fallback(settings),
        "[CACHE] ETF refreshed",
        "[ERROR] Using cached fallback",
        deepcopy(FALLBACK_ETF_FLOW),
    )


def _get_etf_flow_with_fallback(settings: Settings) -> dict[str, Any]:
    manual_data = _get_etf_flow_from_manual_file(settings)
    for loader in (
        _get_etf_flow_from_farside_latest,
        _get_etf_flow_from_farside,
        _get_etf_flow_from_farside_reader,
    ):
        farside_data = loader(settings)
        if farside_data["source"] != "fallback":
            return farside_data
    if settings.coinglass_api_key:
        coinglass_data = _get_etf_flow_from_coinglass(settings)
        if coinglass_data["source"] != "fallback":
            return coinglass_data
    if settings.sosovalue_api_key:
        soso_data = _get_etf_flow_from_sosovalue(settings)
        if soso_data["source"] != "fallback":
            return soso_data
    if manual_data["source"] != "fallback":
        return manual_data
    bitbo_data = _get_etf_flow_from_bitbo(settings)
    if bitbo_data["source"] != "fallback":
        return bitbo_data
    for loader in (_get_etf_flow_from_walletpilot, _get_etf_flow_from_globalcoinguide):
        fallback_data = loader(settings)
        if fallback_data["source"] != "fallback":
            return fallback_data
    return _get_seeded_etf_flow()


def _get_etf_flow_from_sosovalue(settings: Settings) -> dict[str, Any]:
    try:
        headers = {
            **API_HEADERS,
            "x-soso-api-key": settings.sosovalue_api_key,
            "Content-Type": "application/json",
        }
        body = _post_json_with_headers(
            SOSOVALUE_BTC_ETF_FLOW_URL,
            settings,
            headers,
            {"type": "us-btc-spot"},
        )
        if body.get("code") != 0:
            raise ValueError(body.get("msg") or "SoSoValue returned non-zero code")
        rows = (body.get("data") or {}).get("list") or []
        if not rows:
            raise ValueError("SoSoValue ETF response has no rows")
        history = []
        for row in rows[-30:]:
            history.append({
                "date": row.get("date"),
                "net_flow_usd": (
                    _first_number(row, ("totalNetInflow", "netInflow", "net_flow_usd")) or 0
                ),
                "close_price": None,
            })
        return _normalize_etf_payload(history, "sosovalue")
    except (requests.RequestException, KeyError, TypeError, ValueError) as exc:
        logger.warning("SoSoValue ETF flow request failed: %s", exc)
        fallback = FALLBACK_ETF_FLOW.copy()
        fallback["error"] = str(exc)
        return fallback


def _drop_unconfirmed_latest_zero_etf_row(
    history: list[dict[str, Any]],
    source_label: str,
) -> list[dict[str, Any]]:
    if len(history) < 2:
        return history
    sorted_history = sorted(history, key=_etf_sort_key)
    latest = sorted_history[-1]
    latest_date = _parse_etf_date(str(latest.get("date") or ""))
    latest_flow = _to_float_or_none(latest.get("net_flow_usd"))
    if latest_date is None or latest_flow != 0:
        return history
    has_confirmed_previous_flow = any(
        (_to_float_or_none(row.get("net_flow_usd")) or 0) != 0 for row in sorted_history[:-1]
    )
    if not has_confirmed_previous_flow:
        return history
    age_days = (_utc_now_dt().date() - latest_date).days
    if 0 <= age_days <= 1:
        logger.warning(
            "%s ETF flow latest row is zero for %s; treating it as an unconfirmed placeholder",
            source_label,
            latest_date.isoformat(),
        )
        return sorted_history[:-1]
    return history


def _get_etf_flow_from_coinglass(settings: Settings) -> dict[str, Any]:
    try:
        headers = {**API_HEADERS, "CG-API-KEY": settings.coinglass_api_key}
        data = _get_json_with_headers(COINGLASS_BTC_ETF_FLOW_URL, settings, headers)
        rows = data.get("data", data)
        if not isinstance(rows, list) or not rows:
            raise ValueError("CoinGlass ETF flow response has no rows")

        history = []
        for row in rows[-30:]:
            net_flow = _first_number(row, ("changeUsd", "netFlowUsd", "net_flow_usd", "flowUsd"))
            history.append({
                "date": row.get("date") or row.get("time") or row.get("timestamp"),
                "net_flow_usd": net_flow if net_flow is not None else "N/A",
                "close_price": _first_number(row, ("price", "closePrice", "close_price")),
            })

        return _normalize_etf_payload(history, "coinglass")
    except (requests.RequestException, KeyError, TypeError, ValueError) as exc:
        logger.warning("CoinGlass ETF flow request failed: %s", exc)
        fallback = FALLBACK_ETF_FLOW.copy()
        fallback["error"] = str(exc)
        return fallback


def _get_etf_flow_from_farside(settings: Settings) -> dict[str, Any]:
    try:
        html = _get_browser_text(FARSIDE_BTC_ETF_FLOW_URL, settings)
        rows = _parse_farside_etf_rows(html) or _parse_farside_etf_rows_from_text(html)
        if not rows:
            raise ValueError("Farside ETF flow table has no parsable rows")
        return _normalize_etf_payload(rows[-30:], "farside")
    except Exception as exc:
        logger.warning("Farside ETF flow request failed: %s", exc)
        fallback = FALLBACK_ETF_FLOW.copy()
        fallback["error"] = str(exc)
        return fallback


def _get_etf_flow_from_farside_latest(settings: Settings) -> dict[str, Any]:
    try:
        html = _get_browser_text(FARSIDE_BTC_ETF_LATEST_URL, settings)
        rows = _parse_farside_latest_rows(html) or _parse_farside_etf_rows(html)
        if not rows:
            raise ValueError("Farside latest ETF page has no parsable rows")
        return _normalize_etf_payload(rows[-30:], "farside-latest")
    except Exception as exc:
        logger.warning("Farside latest ETF request failed: %s", exc)
        fallback = FALLBACK_ETF_FLOW.copy()
        fallback["error"] = str(exc)
        return fallback


def _get_etf_flow_from_farside_reader(settings: Settings) -> dict[str, Any]:
    last_error = ""
    for url in FARSIDE_BTC_ETF_READER_URLS:
        try:
            text = _get_browser_text(url, settings)
            rows = _parse_farside_etf_rows_from_text(text) or _parse_farside_latest_rows(text)
            if not rows:
                raise ValueError(
                    "Farside reader page has no parsable rows; "
                    f"snippet={text[:160]!r}",
                )
            return _normalize_etf_payload(rows[-30:], "farside-reader")
        except Exception as exc:
            last_error = str(exc)
            logger.warning("Farside reader ETF flow request failed: %s", exc)
    fallback = FALLBACK_ETF_FLOW.copy()
    fallback["error"] = last_error or "Farside reader ETF flow request failed"
    return fallback


def _get_etf_flow_from_bitbo(settings: Settings) -> dict[str, Any]:
    try:
        html = _get_browser_text(BITBO_BTC_ETF_FLOW_URL, settings)
        rows = _parse_bitbo_etf_rows(html)
        if not rows:
            raise ValueError("Bitbo ETF flow table has no parsable rows")
        return _normalize_etf_payload(rows[-30:], "bitbo")
    except Exception as exc:
        logger.warning("Bitbo ETF flow request failed: %s", exc)
        fallback = FALLBACK_ETF_FLOW.copy()
        fallback["error"] = str(exc)
        return fallback


def _get_etf_flow_from_manual_file(settings: Settings) -> dict[str, Any]:
    path = settings.etf_flow_path
    _sync_manual_etf_file_if_needed(path)
    if not path.exists():
        return deepcopy(FALLBACK_ETF_FLOW)

    try:
        with path.open(encoding="utf-8") as file:
            data = json.load(file)
        history = data.get("flow_history")
        if history in (None, []):
            return deepcopy(FALLBACK_ETF_FLOW)
        if not isinstance(history, list):
            raise ValueError("manual ETF flow file has no rows")
        rows = []
        for row in history:
            if not isinstance(row, dict):
                raise ValueError("manual ETF flow row must be an object")
            rows.append({
                "date": row.get("date"),
                "net_flow_usd": row.get("net_flow_usd"),
                "close_price": row.get("close_price"),
            })
        payload = _normalize_etf_payload(rows, "manual", allow_stale=True)
        payload["manual_updated_at"] = str(data.get("updated_at") or "")
        return payload
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.warning("Manual ETF flow file invalid: %s", exc)
        fallback = deepcopy(FALLBACK_ETF_FLOW)
        fallback["error"] = str(exc)
        return fallback


def update_manual_etf_flow_file(settings: Settings, data: dict[str, Any]) -> dict[str, Any]:
    validated = _merge_manual_etf_update_payload(settings.etf_flow_path, data)
    _write_manual_etf_json_atomic(settings.etf_flow_path, validated)
    clear_etf_cache()
    payload = _get_etf_flow_from_manual_file(settings)
    if payload["source"] == "fallback":
        raise ValueError(payload.get("error") or "manual ETF flow update failed")
    return payload


def _merge_manual_etf_update_payload(path: Path, data: dict[str, Any]) -> dict[str, Any]:
    current_history = []
    if path.exists():
        try:
            current_history = _load_manual_etf_json(path).get("flow_history") or []
        except (OSError, json.JSONDecodeError, TypeError):
            current_history = []
    merged = {
        **data,
        "flow_history": [
            *(row for row in current_history if isinstance(row, dict)),
            *(row for row in data.get("flow_history", []) if isinstance(row, dict)),
        ],
    }
    return _validate_manual_etf_update_payload(merged)


def _validate_manual_etf_update_payload(data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("ETF update payload must be an object")
    if data.get("source") != "manual":
        raise ValueError("ETF update source must be manual")

    updated_at = str(data.get("updated_at") or "").strip()
    _parse_iso_timestamp(updated_at)

    history = data.get("flow_history")
    if not isinstance(history, list) or not history:
        raise ValueError("ETF update flow_history must be a non-empty list")
    if len(history) > 120:
        raise ValueError("ETF update flow_history must contain 120 rows or fewer")

    rows_by_date = {}
    for row in history:
        if not isinstance(row, dict):
            raise ValueError("ETF update row must be an object")
        unexpected_fields = set(row) - {"date", "net_flow_usd", "close_price"}
        if unexpected_fields:
            raise ValueError("ETF update row contains unsupported fields")
        date = str(row.get("date") or "").strip()
        if _parse_etf_date(date) is None:
            raise ValueError(f"ETF update row has invalid date: {date}")
        try:
            net_flow_usd = float(row.get("net_flow_usd"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"ETF update row has invalid net_flow_usd for {date}") from exc
        clean_row = {"date": date, "net_flow_usd": net_flow_usd}
        if "close_price" in row:
            clean_row["close_price"] = _first_number(row, ("close_price",)) or 0
        rows_by_date[date] = clean_row

    normalized = _normalize_etf_payload(list(rows_by_date.values()), "manual", allow_stale=True)
    return {
        "source": "manual",
        "updated_at": updated_at,
        "flow_history": normalized["flow_history"],
    }


def _parse_iso_timestamp(value: str) -> datetime:
    if not value:
        raise ValueError("ETF update updated_at is required")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("ETF update updated_at must be an ISO timestamp") from exc


def _write_manual_etf_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    _write_manual_etf_json(temp_path, data)
    temp_path.replace(path)


def _sync_manual_etf_file_if_needed(path: Path) -> None:
    if not _should_sync_manual_etf_file(path):
        return
    try:
        bundled_data = _load_manual_etf_json(BUNDLED_ETF_FLOW_PATH)
        if not _manual_etf_history_latest_date(bundled_data):
            return
        if path.exists():
            current_data = _load_manual_etf_json(path)
            current_latest = _manual_etf_history_latest_date(current_data)
            bundled_latest = _manual_etf_history_latest_date(bundled_data)
            if current_latest is not None and current_latest >= bundled_latest:
                return
        _write_manual_etf_json(path, bundled_data)
        logger.info("Synced manual ETF flow file at %s from bundled data", path)
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        logger.warning("Unable to sync manual ETF flow file at %s: %s", path, exc)


def _load_manual_etf_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise TypeError("manual ETF flow file must be an object")
    return data


def _write_manual_etf_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=2)
        file.write("\n")


def _manual_etf_history_latest_date(data: dict[str, Any]) -> datetime.date | None:
    history = data.get("flow_history")
    if not isinstance(history, list) or not history:
        return None
    latest_dates = [
        parsed
        for row in history
        if isinstance(row, dict)
        and (parsed := _parse_etf_date(str(row.get("date") or ""))) is not None
    ]
    return max(latest_dates) if latest_dates else None


def _should_sync_manual_etf_file(path: Path) -> bool:
    return path.as_posix() == "/data/etf_flows.json"


def _get_etf_flow_from_walletpilot(settings: Settings) -> dict[str, Any]:
    try:
        html = _get_text(WALLETPILOT_BTC_ETF_URL, settings)
        text = _clean_page_text(html)
        embedded_rows = _parse_walletpilot_embedded_flow_rows(html)
        if embedded_rows:
            payload = _normalize_etf_payload(embedded_rows[-30:], "walletpilot")
            latest_parsed = _parse_etf_date(str(payload.get("latest_date") or ""))
            if latest_parsed:
                payload["latest_date"] = latest_parsed.isoformat()
            return payload
        latest_date = _extract_walletpilot_date(text)
        latest_flow = _extract_millions_flow(text, "1-Day Net Flows")
        seven_day_flow = _extract_millions_flow(text, "7-Day Net Flows")
        if latest_flow is None and seven_day_flow is None:
            raise ValueError("WalletPilot ETF page has no parsable flow values")
        history = [{
            "date": latest_date or _utc_now_iso().split("T", 1)[0],
            "net_flow_usd": latest_flow if latest_flow is not None else 0,
            "close_price": None,
        }]
        payload = _normalize_etf_payload(history, "walletpilot")
        payload["7d_flow"] = seven_day_flow if seven_day_flow is not None else payload["7d_flow"]
        payload["latest_date"] = latest_date or payload["latest_date"]
        return payload
    except Exception as exc:
        logger.warning("WalletPilot ETF flow request failed: %s", exc)
        fallback = FALLBACK_ETF_FLOW.copy()
        fallback["error"] = str(exc)
        return fallback


def _get_etf_flow_from_globalcoinguide(settings: Settings) -> dict[str, Any]:
    try:
        html = _get_text(GLOBALCOINGUIDE_BTC_ETF_URL, settings)
        text = _clean_page_text(html)
        latest_date = _extract_globalcoinguide_date(text)
        latest_flow = _extract_millions_flow(text, "Today's Net Flow")
        seven_day_flow = _extract_millions_flow(text, "Weekly Net Flow")
        if latest_flow is None and seven_day_flow is None:
            raise ValueError("GlobalCoinGuide ETF page has no parsable flow values")
        history = [{
            "date": latest_date or _utc_now_iso().split("T", 1)[0],
            "net_flow_usd": latest_flow if latest_flow is not None else 0,
            "close_price": None,
        }]
        payload = _normalize_etf_payload(history, "globalcoinguide")
        payload["7d_flow"] = seven_day_flow if seven_day_flow is not None else payload["7d_flow"]
        payload["latest_date"] = latest_date or payload["latest_date"]
        return payload
    except Exception as exc:
        logger.warning("GlobalCoinGuide ETF flow request failed: %s", exc)
        fallback = FALLBACK_ETF_FLOW.copy()
        fallback["error"] = str(exc)
        return fallback


def _get_seeded_etf_flow() -> dict[str, Any]:
    history = [
        {
            "date": date,
            "net_flow_usd": round(flow_millions * 1_000_000, 2),
            "close_price": 0,
        }
        for date, flow_millions in SEEDED_ETF_FLOW_MILLIONS
    ]
    today = _utc_now_dt().date()
    fallback_history = [
        row
        for row in history
        if (parsed_date := _parse_etf_date(str(row.get("date") or ""))) is not None
        and parsed_date <= today
    ][-5:]
    if not fallback_history:
        raise ValueError("No seeded ETF flow history available")
    payload = _normalize_etf_payload(
        fallback_history,
        "seeded-fallback",
        allow_stale=True,
        is_fallback=True,
    )
    payload["status"] = "stale"
    payload["error"] = "Live ETF flow sources unavailable; using seeded fallback data"
    return payload


def _normalize_etf_payload(
    history: list[dict[str, Any]],
    source: str,
    *,
    allow_stale: bool = False,
    is_fallback: bool = False,
) -> dict[str, Any]:
    if not history:
        raise ValueError("ETF history is empty")
    history = sorted(history, key=_etf_sort_key)
    if not is_fallback and source != "manual":
        history = _drop_unconfirmed_latest_zero_etf_row(history, source)
    latest_row = history[-1]
    latest_flow = float(latest_row.get("net_flow_usd", 0) or 0)
    latest_date = str(latest_row.get("date") or "")
    is_stale = not _etf_date_is_recent(latest_date)
    if is_stale and not allow_stale:
        raise ValueError(f"ETF data is stale: {latest_date}")
    normalized_history = [
        {
            "date": str(row.get("date") or ""),
            "net_flow_usd": float(row.get("net_flow_usd", 0) or 0),
            "close_price": _first_number(row, ("close_price", "closePrice", "price")) or 0,
        }
        for row in history
    ]
    recent_rows = normalized_history[-7:]
    seven_day_flow = round(sum(float(row.get("net_flow_usd", 0) or 0) for row in recent_rows), 2)
    trend = "neutral"
    if latest_flow > 0:
        trend = "inflow"
    elif latest_flow < 0:
        trend = "outflow"
    source_label = _etf_source_label(source, is_fallback)
    data_note = _etf_data_note(source, is_fallback)
    if is_stale:
        data_note = f"{data_note} ETF flow data is older than expected."
    return {
        "latest_date": latest_date,
        "latest_net_flow_usd": latest_flow,
        "7d_flow": seven_day_flow,
        "trend": trend,
        "flow_history": normalized_history,
        "source": source,
        "is_fallback": is_fallback,
        "is_stale": is_stale,
        "source_label": source_label,
        "data_note": data_note,
        "error": "",
    }


def _etf_source_label(source: str, is_fallback: bool) -> str:
    if is_fallback:
        return "Fallback estimate"
    return {
        "bitbo": "Bitbo",
        "coinglass": "CoinGlass",
        "farside-reader": "Live",
        "manual": "Manual",
        "seeded-fallback": "Fallback estimate",
    }.get(source, "Live")


def _etf_data_note(source: str, is_fallback: bool) -> str:
    if is_fallback:
        return "ETF flow history is using fallback estimate data. Live data unavailable."
    return {
        "bitbo": "ETF flow data loaded from Bitbo public table.",
        "coinglass": "ETF flow data loaded from CoinGlass.",
        "farside-reader": "ETF flow data loaded from Farside via reader fallback.",
        "manual": "ETF flow data loaded from local manual file.",
    }.get(source, "ETF flow history is using live source data.")


def _etf_date_is_recent(value: str, max_age_days: int = ETF_MAX_AGE_DAYS) -> bool:
    parsed = _parse_etf_date(value)
    if parsed is None:
        return False
    today = _utc_now_dt().date()
    age_days = (today - parsed).days
    return 0 <= age_days <= max_age_days


def _etf_sort_key(row: dict[str, Any]) -> datetime.date:
    return _parse_etf_date(str(row.get("date") or "")) or datetime.min.date()


def _parse_etf_date(value: str) -> datetime.date | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    for fmt in ("%b %d", "%B %d"):
        try:
            parsed = datetime.strptime(text, fmt).date()
            return parsed.replace(year=_utc_now_dt().year)
        except ValueError:
            continue
    return None


def _parse_farside_etf_rows(html: str) -> list[dict[str, Any]]:
    parsed_rows = []
    for row_html in re.findall(r"<tr[^>]*>(.*?)</tr>", html, flags=re.IGNORECASE | re.DOTALL):
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, flags=re.IGNORECASE | re.DOTALL)
        if len(cells) < 2:
            continue
        values = [_clean_html_cell(cell) for cell in cells]
        if values[0].lower() == "date":
            continue
        total_millions = _parse_farside_number(values[-1])
        if total_millions is None:
            continue
        parsed_rows.append({
            "date": values[0],
            "net_flow_usd": total_millions * 1_000_000,
            "close_price": None,
        })
    return parsed_rows


def _parse_farside_etf_rows_from_text(text: str) -> list[dict[str, Any]]:
    parsed_rows = []
    for line in text.splitlines():
        if "|" not in line:
            continue
        parts = [part.strip() for part in line.split("|")]
        while parts and not parts[0]:
            parts.pop(0)
        while parts and not parts[-1]:
            parts.pop()
        if len(parts) < 2:
            continue
        date_value = parts[0]
        if not re.match(r"^\d{2} [A-Z][a-z]{2} \d{4}$", date_value):
            continue
        total_millions = _parse_farside_number(parts[-1])
        if total_millions is None:
            continue
        parsed_rows.append({
            "date": date_value,
            "net_flow_usd": total_millions * 1_000_000,
            "close_price": None,
        })
    return parsed_rows


def _parse_farside_latest_rows(html: str) -> list[dict[str, Any]]:
    text = _clean_page_text(html)
    pattern = re.compile(
        r"(\d{2}\s+[A-Z][a-z]{2}\s+\d{4})\s+"
        r"([()0-9.,-]+)(?=\s+[()0-9.,-]+|\s+\d{2}\s+[A-Z][a-z]{2}\s+\d{4}|\s+Total|\s+Average)",
    )
    rows = []
    for match in pattern.finditer(text):
        total_millions = _parse_farside_number(match.group(2))
        if total_millions is None:
            continue
        rows.append({
            "date": match.group(1),
            "net_flow_usd": total_millions * 1_000_000,
            "close_price": 0,
        })
    return rows


def _clean_page_text(value: str) -> str:
    value = re.sub(r"<script[^>]*>.*?</script>", " ", value, flags=re.IGNORECASE | re.DOTALL)
    value = re.sub(r"<style[^>]*>.*?</style>", " ", value, flags=re.IGNORECASE | re.DOTALL)
    value = re.sub(r"<[^>]+>", " ", value)
    value = unescape(value).replace("\xa0", " ")
    return re.sub(r"\s+", " ", value).strip()


def _extract_walletpilot_date(text: str) -> str:
    match = re.search(r"Holdings as of market close:\s*([0-9]{1,2}\s+[A-Za-z]+\s+[0-9]{4})", text)
    return match.group(1) if match else ""


def _extract_globalcoinguide_date(text: str) -> str:
    match = re.search(r"Last updated:\s*([A-Za-z]{3}\s+[0-9]{1,2}(?:,\s*[0-9]{4})?)", text)
    return match.group(1) if match else ""


def _parse_walletpilot_embedded_flow_rows(html: str) -> list[dict[str, Any]]:
    grouped: dict[str, float] = {}
    pattern = re.compile(
        r"netFlows1d\s*:\s*(-?\d+(?:\.\d+)?)\b.*?"
        r"lastFlowDate\s*:\s*\"(\d{4}-\d{2}-\d{2})T",
        flags=re.DOTALL,
    )
    for match in pattern.finditer(html):
        flow_millions = float(match.group(1))
        date_value = match.group(2)
        grouped[date_value] = grouped.get(date_value, 0.0) + flow_millions
    return [
        {
            "date": date_value,
            "net_flow_usd": round(flow_millions * 1_000_000, 2),
            "close_price": None,
        }
        for date_value, flow_millions in sorted(grouped.items())
    ]


def _parse_bitbo_etf_rows(html: str) -> list[dict[str, Any]]:
    text = _clean_page_text(html)
    pattern = re.compile(
        r"((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+"
        r"\d{1,2},\s+\d{4})\s+(.*?)(?=(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|"
        r"Nov|Dec)[a-z]*\s+\d{1,2},\s+\d{4}|$)",
        flags=re.IGNORECASE,
    )
    rows = []
    for match in pattern.finditer(text):
        values = re.findall(r"-?\d+(?:\.\d+)?", match.group(2))
        if not values:
            continue
        total_millions = _parse_farside_number(values[-1])
        if total_millions is None:
            continue
        rows.append({
            "date": match.group(1),
            "net_flow_usd": total_millions * 1_000_000,
            "close_price": None,
        })
    return rows


def _extract_millions_flow(text: str, label: str) -> float | None:
    pattern = rf"{re.escape(label)}\s*([+\-]?\$[0-9,]+(?:\.[0-9]+)?[MB])"
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return None
    return _parse_abbreviated_usd(match.group(1))


def _parse_abbreviated_usd(value: str) -> float | None:
    cleaned = value.strip().replace(",", "").replace("$", "")
    sign = -1 if cleaned.startswith("-") else 1
    cleaned = cleaned.lstrip("+-")
    if cleaned.endswith("B"):
        multiplier = 1_000_000_000
        cleaned = cleaned[:-1]
    elif cleaned.endswith("M"):
        multiplier = 1_000_000
        cleaned = cleaned[:-1]
    else:
        return None
    try:
        return float(cleaned) * multiplier * sign
    except ValueError:
        return None


def _normalize_timestamp(value: Any) -> str:
    if isinstance(value, (int, float)):
        if value > 1_000_000_000_000:
            dt_value = datetime.fromtimestamp(value / 1000, UTC)
        else:
            dt_value = datetime.fromtimestamp(value, UTC)
        return dt_value.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    text = str(value).strip()
    if text.endswith("Z"):
        return text
    return text


def _clean_html_cell(value: str) -> str:
    value = re.sub(r"<[^>]+>", "", value)
    return unescape(value).replace("\xa0", " ").strip()


def _parse_farside_number(value: str) -> float | None:
    cleaned = value.strip()
    if not cleaned or cleaned == "-":
        return None
    is_negative = cleaned.startswith("(") and cleaned.endswith(")")
    cleaned = cleaned.strip("()").replace(",", "")
    try:
        number = float(cleaned)
    except ValueError:
        return None
    return -number if is_negative else number


def get_btc_treasury_holdings(settings: Settings) -> dict[str, Any]:
    payload = _cached_resource(
        "treasury_cache",
        TREASURY_TTL_SECONDS,
        lambda: _get_btc_treasury_with_fallback(settings),
        "[CACHE] Treasury refreshed",
        "[CACHE] Treasury fallback used",
        deepcopy(FALLBACK_BTC_TREASURY),
    )
    _set_persistent_cache("institutional_cache", {
        "total_btc_held": payload.get("total_btc_held", 0),
        "treasury_dominance_percent": payload.get("treasury_dominance_percent", 0),
        "top_holders": deepcopy(payload.get("top_holders", [])),
        "status": payload.get("status", "ok"),
        "updated_at": payload.get("updated_at"),
        "source": payload.get("source"),
        "source_label": payload.get("source_label"),
        "data_note": payload.get("data_note"),
    }, payload.get("status", "ok"))
    return payload


def _get_btc_treasury_with_fallback(settings: Settings) -> dict[str, Any]:
    headers = API_HEADERS.copy()
    if settings.coingecko_demo_api_key:
        headers["x-cg-demo-api-key"] = settings.coingecko_demo_api_key

    providers = (
        ("coingecko-company-treasury", COINGECKO_TREASURY_URLS[0]),
    )
    errors: list[str] = []

    for source_name, url in providers:
        try:
            data = _get_json_with_headers_retry(url, settings, headers, attempts=1)
            payload = _normalize_treasury_payload(data, source_name)
            if _treasury_payload_is_valid(payload):
                return _remember_successful_treasury(payload)
            errors.append(f"{source_name}: invalid treasury payload")
        except (requests.RequestException, KeyError, TypeError, ValueError) as exc:
            logger.warning("%s treasury request failed: %s", source_name, exc)
            errors.append(f"{source_name}: {exc}")
            continue
    error = " | ".join(errors) if errors else "treasury source unavailable"
    with _treasury_cache_lock:
        if _last_successful_treasury is not None:
            stale = deepcopy(_last_successful_treasury)
            stale["status"] = "stale"
            stale["error"] = error
            stale["source_label"] = _treasury_source_label(stale.get("source"), "stale")
            stale["data_note"] = "Treasury data is cached because the live source is unavailable."
            return stale
    return _estimated_btc_treasury(error)


def _estimated_btc_treasury(error: str) -> dict[str, Any]:
    payload = deepcopy(ESTIMATED_BTC_TREASURY)
    payload["error"] = f"{payload['error']}: {error}"
    return payload


def _normalize_treasury_payload(data: dict[str, Any], source: str) -> dict[str, Any]:
    holders = data.get("companies") or data.get("entities") or []
    top_holders = []
    for holder in holders:
        btc_held = _first_number(holder, ("total_holdings", "amount"))
        if btc_held is None:
            continue
        top_holders.append({
            "name": holder.get("name", "Unknown"),
            "symbol": holder.get("symbol"),
            "btc_held": btc_held,
            "supply_percent": _first_number(
                holder,
                ("percentage_of_total_supply", "supply_percent"),
            ),
        })
    top_holders = sorted(
        top_holders,
        key=lambda holder: _to_float_or_none(holder.get("btc_held")) or 0,
        reverse=True,
    )[:10]
    return _treasury_payload(
        total_btc_held=data.get("total_holdings", "N/A"),
        treasury_dominance_percent=data.get("market_cap_dominance", "N/A"),
        top_holders=top_holders,
        source=source,
        status="ok",
        updated_at=_utc_now_iso(),
        error="",
    )


def _treasury_payload(
    total_btc_held: Any,
    treasury_dominance_percent: Any,
    top_holders: list[dict[str, Any]],
    source: str,
    status: str,
    updated_at: str | None,
    error: str | None,
) -> dict[str, Any]:
    return {
        "total_btc_held": total_btc_held,
        "treasury_dominance_percent": treasury_dominance_percent,
        "top_holders": top_holders,
        "source": source,
        "source_label": _treasury_source_label(source, status),
        "status": status,
        "updated_at": updated_at,
        "error": error,
        "data_note": _treasury_data_note(source, status),
    }


def _treasury_source_label(source: Any, status: str) -> str:
    source_text = str(source or "unknown")
    if status == "stale":
        return "CoinGecko | Stale" if source_text.startswith("coingecko") else "Cached | Stale"
    if status == "fallback":
        return "CoinGecko estimate"
    if source_text.startswith("coingecko"):
        return "CoinGecko | Live"
    if source_text == "fallback":
        return "Treasury unavailable"
    return source_text


def _treasury_data_note(source: Any, status: str) -> str:
    if status == "stale":
        return "Treasury data is cached because the live source is unavailable."
    if status == "fallback":
        return "Treasury data is using checked public estimate data from CoinGecko."
    if str(source or "").startswith("coingecko"):
        return "Treasury data loaded from CoinGecko public treasury data."
    return "Treasury data source is limited."


def _treasury_payload_is_valid(payload: dict[str, Any]) -> bool:
    return (
        _to_float_or_none(payload.get("total_btc_held")) is not None
        and isinstance(payload.get("top_holders"), list)
        and len(payload["top_holders"]) > 0
    )


def _remember_successful_treasury(payload: dict[str, Any]) -> dict[str, Any]:
    global _last_successful_treasury

    normalized = _treasury_payload(
        total_btc_held=payload.get("total_btc_held", "N/A"),
        treasury_dominance_percent=payload.get("treasury_dominance_percent", "N/A"),
        top_holders=deepcopy(payload.get("top_holders", [])),
        source=str(payload.get("source", "unknown")),
        status="ok",
        updated_at=payload.get("updated_at") or _utc_now_iso(),
        error="",
    )
    with _treasury_cache_lock:
        _last_successful_treasury = deepcopy(normalized)
    return normalized


def get_fear_greed_index(settings: Settings) -> dict[str, Any]:
    return _cached_resource(
        "fear_greed_cache",
        FEAR_GREED_TTL_SECONDS,
        lambda: _get_fear_greed_from_alternative(settings),
        "Fear & Greed index refreshed",
        "Fear & Greed index fallback served",
        FALLBACK_FEAR_GREED,
    )


def _get_fear_greed_from_alternative(settings: Settings) -> dict[str, Any]:
    data = _get_json(ALTERNATIVE_FEAR_GREED_URL, settings)
    rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not rows:
        raise DataSourceError("Alternative.me Fear & Greed payload missing data")
    latest = _normalize_fear_greed_row(rows[0])
    historical = {
        "now": latest,
        "yesterday": _normalize_fear_greed_row(rows[1]) if len(rows) > 1 else {},
        "last_week": _normalize_fear_greed_row(rows[7]) if len(rows) > 7 else {},
        "last_month": _normalize_fear_greed_row(rows[29]) if len(rows) > 29 else {},
    }
    return {
        "value": latest["value"],
        "classification": latest["classification"],
        "historical": historical,
        "source": "alternative.me",
        "source_label": "Alternative.me",
        "status": "ok",
        "updated_at": "",
        "data_timestamp": latest["data_timestamp"],
        "data_note": "Crypto Fear & Greed Index loaded from Alternative.me.",
        "error": "",
    }


def _normalize_fear_greed_row(row: Any) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise DataSourceError("Alternative.me Fear & Greed row is invalid")

    value = _to_float_or_none(row.get("value"))
    if value is None:
        raise DataSourceError("Alternative.me Fear & Greed value is invalid")

    return {
        "value": int(value) if value.is_integer() else round(value, 1),
        "classification": str(row.get("value_classification") or "N/A").strip() or "N/A",
        "data_timestamp": _parse_unix_timestamp(row.get("timestamp")),
    }


def _parse_unix_timestamp(value: Any) -> str:
    numeric = _to_float_or_none(value)
    if numeric is None or numeric <= 0:
        return ""
    return datetime.fromtimestamp(numeric, UTC).replace(microsecond=0).isoformat().replace(
        "+00:00",
        "Z",
    )


def _utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _utc_now_dt() -> datetime:
    return datetime.now(UTC)


def _persistent_cache_is_fresh(cache_name: str, ttl_seconds: int) -> bool:
    with _persistent_cache_lock:
        cache = _persistent_caches[cache_name]
        return (
            cache.last_updated is not None
            and (_utc_now_dt() - cache.last_updated) < timedelta(seconds=ttl_seconds)
        )


def _persistent_cache_value(cache_name: str) -> Any:
    with _persistent_cache_lock:
        return deepcopy(_persistent_caches[cache_name].data)


def cached_dashboard_resource(cache_name: str) -> Any:
    return _persistent_cache_value(cache_name)


def _persistent_cache_updated_at(cache_name: str) -> str | None:
    with _persistent_cache_lock:
        ts = _persistent_caches[cache_name].last_updated
    return ts.isoformat().replace("+00:00", "Z") if ts else None


def _set_persistent_cache(cache_name: str, data: Any, status: str = "ok") -> Any:
    with _persistent_cache_lock:
        _persistent_caches[cache_name] = PersistentCache(
            data=deepcopy(data),
            last_updated=_utc_now_dt(),
            status=status,
        )
    return deepcopy(data)


def _cached_resource(
    cache_name: str,
    ttl_seconds: int,
    refresh_fn,
    refresh_log: str,
    fallback_log: str,
    safe_fallback: dict[str, Any],
) -> dict[str, Any]:
    if _persistent_cache_is_fresh(cache_name, ttl_seconds):
        cached = _persistent_cache_value(cache_name)
        cached.setdefault("updated_at", _persistent_cache_updated_at(cache_name))
        cached.setdefault("status", "ok")
        logger.debug("cache served key=%s", cache_name)
        return cached

    stale = _persistent_cache_value(cache_name)
    with _persistent_cache_lock:
        if cache_name in _persistent_cache_refreshing:
            if not _cache_value_is_empty(stale):
                logger.debug("cache refresh skipped key=%s reason=already_running", cache_name)
                return stale
            raise RuntimeError(f"{cache_name} refresh already running")
        _persistent_cache_refreshing.add(cache_name)
    try:
        refreshed = refresh_fn()
        if _cache_value_is_empty(refreshed) and not _cache_value_is_empty(stale):
            logger.warning("cache refresh kept stale key=%s reason=empty_refresh", cache_name)
            stale["status"] = "stale"
            stale["updated_at"] = stale.get("updated_at") or _persistent_cache_updated_at(
                cache_name,
            )
            return stale
        refreshed["updated_at"] = _utc_now_iso()
        refreshed["status"] = refreshed.get("status") or "ok"
        _set_persistent_cache(cache_name, refreshed, refreshed["status"])
        logger.info(refresh_log)
        return deepcopy(refreshed)
    except Exception as exc:
        logger.warning("[ERROR] Using cached fallback: %s", exc)
        cached = _persistent_cache_value(cache_name)
        if cached and _persistent_caches[cache_name].last_updated is not None:
            cached["status"] = "stale"
            cached["updated_at"] = cached.get("updated_at") or _persistent_cache_updated_at(
                cache_name,
            )
            cached["error"] = str(exc)
            logger.info(fallback_log)
            return cached
        fallback = deepcopy(safe_fallback)
        fallback["status"] = "error"
        fallback["updated_at"] = fallback.get("updated_at", "")
        fallback["error"] = str(exc)
        logger.info(fallback_log)
        return fallback
    finally:
        with _persistent_cache_lock:
            _persistent_cache_refreshing.discard(cache_name)


def append_metric_point(kind: str, value: float | None, timestamp: str | None = None) -> None:
    if value is None:
        return
    stamp = timestamp or _utc_now_iso()
    with state.lock:
        points = state.price_points if kind == "price" else state.hashrate_points
        if points and points[-1]["timestamp"] == stamp:
            points[-1]["value"] = value
        else:
            points.append({"timestamp": stamp, "value": value})
        state.metric_timestamps[kind] = stamp


def safe_security_payload() -> dict[str, Any]:
    return {
        "double_spend": {
            "orphan_count": 0,
            "orphans": [],
            "active_height": 0,
            "risk_level": SAFE_SECURITY_RISK,
        },
        "attack_51": {
            "pools": [],
            "top_pool_share": 0,
            "risk_level": SAFE_SECURITY_RISK,
            "status": "safe fallback",
            "error": "",
        },
        "invalid_blocks": {
            "invalid_count": 0,
            "invalid_chains": [],
            "risk_level": SAFE_SECURITY_RISK,
        },
        "reorgs": {
            "reorg_count": 0,
            "reorgs": [],
            "current_height": 0,
            "max_branch_length": 0,
            "risk_level": SAFE_SECURITY_RISK,
        },
        "updated_at": "",
        "status": "error",
    }


def get_btc_supply_ownership(settings: Settings) -> dict[str, Any]:
    return _cached_resource(
        "ownership_cache",
        INSTITUTIONAL_TTL_SECONDS,
        lambda: _get_btc_supply_ownership(settings),
        "[CACHE] Ownership refreshed",
        "[CACHE] Ownership fallback used",
        deepcopy(FALLBACK_SUPPLY_OWNERSHIP),
    )


def _get_btc_supply_ownership(settings: Settings) -> dict[str, Any]:
    treasury = get_btc_treasury_holdings(settings)
    treasury_btc = _to_float_or_none(treasury.get("total_btc_held"))
    circulating_supply = _resolve_circulating_supply(settings)
    if circulating_supply is None:
        raise RuntimeError("circulating supply unavailable")
    return _build_ownership_payload(treasury, treasury_btc, circulating_supply, "ok", "")


def _build_ownership_payload(
    treasury: dict[str, Any],
    treasury_btc: float | None,
    circulating_supply: float,
    status: str,
    error: str | None,
) -> dict[str, Any]:
    remaining_to_mine = max(BITCOIN_MAX_SUPPLY_BTC - circulating_supply, 0)
    percent_mined = (circulating_supply / BITCOIN_MAX_SUPPLY_BTC) * 100
    liquid_low = max(circulating_supply - LOST_BTC_ESTIMATE_RANGE["high"], 0)
    liquid_high = max(circulating_supply - LOST_BTC_ESTIMATE_RANGE["low"], 0)

    categories = [
        _ownership_category(
            "Satoshi Nakamoto estimate",
            SATOSHI_ESTIMATED_BTC,
            circulating_supply,
            "Research estimate",
            "medium",
            True,
        ),
        _ownership_category(
            "ETFs / funds",
            ETF_FUNDS_ESTIMATED_BTC,
            circulating_supply,
            "Approximate ETF/fund custody estimate",
            "low",
            True,
            approximate=True,
        ),
        _ownership_category(
            "Public companies / treasuries",
            treasury_btc,
            circulating_supply,
            "Live" if treasury_btc is not None else "Limited visibility",
            "high" if treasury_btc is not None else "low",
            treasury_btc is None,
            approximate=treasury_btc is None,
        ),
        _ownership_category(
            "Governments / seized BTC",
            GOVERNMENTS_ESTIMATED_BTC,
            circulating_supply,
            "Approximate public/seized BTC estimate",
            "low",
            True,
            approximate=True,
        ),
        _ownership_category(
            "Exchanges / custodians",
            EXCHANGES_ESTIMATED_BTC,
            circulating_supply,
            "On-chain estimate",
            "low",
            True,
            approximate=True,
        ),
        _ownership_category(
            "Miners",
            MINERS_ESTIMATED_BTC,
            circulating_supply,
            "On-chain estimate",
            "low",
            True,
            approximate=True,
        ),
        _ownership_category(
            "Lost coins estimate",
            None,
            circulating_supply,
            "Research estimate",
            "low",
            True,
            btc_range=LOST_BTC_ESTIMATE_RANGE,
        ),
        _ownership_category(
            "Retail / unattributed supply",
            _round_estimate(
                max(
                    circulating_supply
                    - SATOSHI_ESTIMATED_BTC
                    - LOST_BTC_ESTIMATE_RANGE["high"]
                    - ETF_FUNDS_ESTIMATED_BTC
                    - GOVERNMENTS_ESTIMATED_BTC
                    - EXCHANGES_ESTIMATED_BTC
                    - MINERS_ESTIMATED_BTC
                    - (treasury_btc or 0),
                    0,
                )
            ),
            circulating_supply,
            "Limited visibility",
            "low",
            True,
            approximate=True,
        ),
    ]

    insights = _ownership_insights(
        remaining_to_mine,
        liquid_low,
        liquid_high,
        treasury_btc,
        categories,
    )

    return {
        "circulating_supply": round(circulating_supply, 8),
        "max_supply": BITCOIN_MAX_SUPPLY_BTC,
        "remaining_to_mine": round(remaining_to_mine, 8),
        "percent_mined": round(percent_mined, 2),
        "estimated_lost_btc": deepcopy(LOST_BTC_ESTIMATE_RANGE),
        "effective_liquid_supply": {
            "low": round(liquid_low, 2),
            "high": round(liquid_high, 2),
        },
        "categories": categories,
        "chart_categories": _ownership_chart_categories(categories),
        "insights": insights,
        "updated_at": _utc_now_iso(),
        "status": status,
        "error": error or "",
        "note": "Bitcoin ownership is estimated because addresses are pseudonymous.",
        "top_holders": treasury.get("top_holders", []),
        "source": "coingecko + research estimates + transparent unavailable categories",
        "max_supply_btc": BITCOIN_MAX_SUPPLY_BTC,
        "circulating_supply_btc": round(circulating_supply, 8),
        "ownership": categories,
    }


def _ownership_category(
    name: str,
    btc: float | None,
    circulating_supply: float,
    source_type: str,
    confidence: str,
    estimated: bool,
    btc_range: dict[str, int] | None = None,
    approximate: bool = False,
) -> dict[str, Any]:
    percent = None if btc is None else round((float(btc) / circulating_supply) * 100, 2)
    return {
        "name": name,
        "label": name,
        "btc": None if btc is None else round(float(btc), 2),
        "btc_range": btc_range,
        "percent": percent,
        "percent_of_circulating_supply": percent,
        "source_type": source_type,
        "source": source_type,
        "confidence": _confidence_label(confidence),
        "confidence_level": confidence,
        "estimated": estimated,
        "approximate": approximate,
        "status_label": _status_label(source_type, estimated),
        "display_btc": _display_btc(btc, btc_range, approximate),
    }


def _ownership_insights(
    remaining_to_mine: float,
    liquid_low: float,
    liquid_high: float,
    treasury_btc: float | None,
    categories: list[dict[str, Any]],
) -> list[str]:
    visible_categories = [
        row
        for row in categories
        if row.get("btc") is not None and row["name"] != "Retail / unattributed supply"
    ]
    largest = max(visible_categories, key=lambda row: float(row["btc"]), default=None)
    insights = [
        f"Mining scarcity: about {_format_btc_compact(remaining_to_mine)} BTC remain.",
        (
            "Lost-coin research implies effective liquid supply could be "
            f"~{_format_btc_compact(liquid_low)} to ~{_format_btc_compact(liquid_high)} BTC."
        ),
    ]
    if treasury_btc is None:
        insights.append("Public treasury totals are estimating until live filings data refreshes.")
    else:
        insights.append(
            f"Public treasuries report {_format_btc_compact(treasury_btc)} BTC in visible custody."
        )
    if largest:
        insights.append(
            f"{largest['name']} is the largest non-retail ownership bucket currently shown."
        )
    return insights


def _confidence_label(confidence: str) -> str:
    labels = {
        "high": "verified/public filings",
        "medium": "research estimate",
        "low": "approximate",
    }
    return labels.get(confidence, "approximate")


def _display_btc(
    btc: float | None,
    btc_range: dict[str, int] | None,
    approximate: bool,
) -> str:
    if btc_range:
        low = _format_btc_compact(float(btc_range["low"]))
        high = _format_btc_compact(float(btc_range["high"]))
        return f"~{low} - ~{high} BTC"
    if btc is None:
        return "Limited visibility"
    prefix = "~" if approximate else ""
    return f"{prefix}{_format_btc_compact(float(btc))} BTC"


def _ownership_chart_categories(categories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row
        for row in categories
        if row["name"] != "Retail / unattributed supply" and row.get("btc") is not None
    ]


def _resolve_circulating_supply(settings: Settings) -> float | None:
    circulating_supply = _to_float_or_none(_get_circulating_supply(settings))
    if circulating_supply is not None:
        return circulating_supply
    cached = _persistent_cache_value("ownership_cache")
    return _to_float_or_none(
        cached.get("circulating_supply") or cached.get("circulating_supply_btc")
    )


def _round_estimate(value: float) -> float:
    return round(value / 100_000) * 100_000


def _status_label(source_type: str, estimated: bool) -> str:
    if source_type == "Live":
        return "Live"
    if "Cached" in source_type:
        return "Cached"
    if estimated:
        return "Estimated"
    return source_type


def _format_btc_compact(value: float) -> str:
    return f"{round(value):,}"


def _get_circulating_supply(settings: Settings) -> float | str:
    try:
        height = int(rpc_call("getblockcount", [], settings))
    except (DataSourceError, requests.RequestException, KeyError, TypeError, ValueError):
        try:
            blocks = _get_json("https://mempool.space/api/v1/blocks", settings)
            height = int(blocks[0]["height"])
        except (requests.RequestException, KeyError, TypeError, ValueError, IndexError) as exc:
            logger.warning("circulating supply height lookup failed: %s", exc)
            return "N/A"
    return round(_issued_btc_at_height(height), 8)


def _issued_btc_at_height(height: int) -> float:
    issued = 0.0
    subsidy = 50.0
    remaining_blocks = height + 1
    while remaining_blocks > 0 and subsidy > 0:
        blocks = min(remaining_blocks, 210_000)
        issued += blocks * subsidy
        remaining_blocks -= blocks
        subsidy /= 2
    return min(issued, BITCOIN_MAX_SUPPLY_BTC)


def _to_float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_number(data: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = data.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _provider_source(provider_name: str) -> str:
    if "bitnodes" in provider_name:
        return "bitnodes"
    if "mempool" in provider_name:
        return "mempool.space"
    if "coingecko" in provider_name:
        return "coingecko"
    if "node" in provider_name:
        return "bitcoin-core"
    return provider_name


def format_hashrate(value: float | None) -> str:
    if value is None:
        return "N/A"
    if value > 1e6:
        return f"{value / 1e6:.2f} EH/s"
    if value > 1e3:
        return f"{value / 1e3:.2f} PH/s"
    return f"{value:.2f} TH/s"


def is_fee_high_and_rising(df: pd.DataFrame | None, threshold: float) -> bool:
    if df is None or len(df.index) < 2:
        return False
    fees = pd.to_numeric(df["sat_per_vbyte"].tail(2), errors="coerce").dropna()
    if len(fees.index) < 2:
        return False
    previous_fee, latest_fee = fees.iloc[0], fees.iloc[1]
    return latest_fee > threshold and latest_fee > previous_fee


def fee_spike_alert(df: pd.DataFrame | None, threshold: float) -> dict[str, str] | None:
    if df is None or len(df.index) < 2:
        return None
    fee_data = df.tail(2).copy()
    fees = pd.to_numeric(fee_data["sat_per_vbyte"], errors="coerce")
    if fees.isna().any():
        return None
    previous_fee, latest_fee = fees.iloc[0], fees.iloc[1]
    if previous_fee > threshold or latest_fee <= threshold:
        return None
    latest_row = fee_data.iloc[-1]
    height_value = latest_row.get("height", "unknown")
    if isinstance(height_value, float) and height_value.is_integer():
        height_value = int(height_value)
    return {
        "type": "fee_spike",
        "severity": "high",
        "message": f"Fee Spike: {latest_fee:.2f} sat/vB crossed above {threshold:.2f}",
        "height": str(height_value),
        "fee": f"{latest_fee:.2f}",
        "threshold": f"{threshold:.2f}",
    }


def price_breakout_alert(prices: list[float], lookback: int) -> str | None:
    if len(prices) < lookback + 1:
        return None
    latest_price = prices[-1]
    previous_prices = prices[-(lookback + 1) : -1]
    previous_high = max(previous_prices)
    previous_low = min(previous_prices)
    if latest_price > previous_high:
        return f"Price Breakout: BTC broke above ${previous_high:,.2f} to ${latest_price:,.2f}"
    if latest_price < previous_low:
        return f"Price Breakdown: BTC broke below ${previous_low:,.2f} to ${latest_price:,.2f}"
    return None


def get_recent_whale_transactions(settings: Settings) -> list[dict[str, Any]]:
    return _cached_for(
        "whale_transactions",
        FEE_MEMPOOL_TTL_SECONDS,
        lambda: _get_recent_whale_transactions(settings),
        "fees/mempool refreshed",
    )


def _get_recent_whale_transactions(settings: Settings) -> list[dict[str, Any]]:
    rows = _get_json(MEMPOOL_RECENT_TX_URL, settings)
    if not isinstance(rows, list):
        raise ValueError("recent mempool transaction response is not a list")
    transactions = []
    for row in rows:
        value_btc = _transaction_value_btc(row)
        if value_btc is None:
            continue
        transactions.append({
            "txid": str(row.get("txid") or ""),
            "value_btc": round(value_btc, 8),
            "fee_sat": int(_first_number(row, ("fee",)) or 0),
            "vsize": int(_first_number(row, ("vsize", "size")) or 0),
        })
    return sorted(transactions, key=lambda tx: tx["value_btc"], reverse=True)


def _transaction_value_btc(row: dict[str, Any]) -> float | None:
    value = _first_number(row, ("value", "output_value", "total_output", "totalOutput"))
    if value is not None:
        return float(value) / SATS_PER_BTC
    outputs = row.get("vout") or row.get("outputs")
    if isinstance(outputs, list):
        total_sats = 0.0
        for output in outputs:
            amount = _first_number(output, ("value", "amount"))
            if amount is not None:
                total_sats += float(amount)
        if total_sats > 0:
            return total_sats / SATS_PER_BTC
    return None


def whale_transaction_alert(
    transactions: list[dict[str, Any]],
    threshold_btc: float,
) -> dict[str, str] | None:
    whales = [tx for tx in transactions if float(tx.get("value_btc", 0) or 0) >= threshold_btc]
    if not whales:
        return None
    largest = max(whales, key=lambda tx: float(tx.get("value_btc", 0) or 0))
    value_btc = float(largest.get("value_btc", 0) or 0)
    txid = str(largest.get("txid") or "")
    short_txid = f"{txid[:8]}...{txid[-8:]}" if len(txid) > 16 else txid
    return {
        "type": "whale_transaction",
        "severity": "high" if value_btc >= threshold_btc * 2 else "medium",
        "status": "red" if value_btc >= threshold_btc * 2 else "yellow",
        "message": f"Whale Transaction: {value_btc:,.2f} BTC moved in mempool",
        "action": f"Review transaction {short_txid}" if short_txid else "Monitor mempool activity",
        "txid": txid,
        "value_btc": f"{value_btc:.8f}",
        "threshold_btc": f"{threshold_btc:.2f}",
    }


def fee_trend_alert(df: pd.DataFrame | None, hashrate: float | None) -> dict[str, str] | None:
    """Detect fee trends and combined signals with hashrate."""
    if df is None or len(df.index) < 5:
        return None
    fees = pd.to_numeric(df["sat_per_vbyte"].tail(5), errors="coerce").dropna().tolist()
    if len(fees) < 4:
        return None

    avg_fee = sum(fees) / len(fees)
    latest_fee = fees[-1]
    is_rising = fees[-1] > fees[-2] > fees[-3]
    is_falling = latest_fee < avg_fee

    # Hashrate trend
    hashrate_rising = False
    try:
        h_history = list(state.hashrate_history)
        if len(h_history) >= 2:
            hashrate_rising = h_history[-1] > h_history[-2]
    except Exception:
        pass

    if is_rising and hashrate_rising:
        message = (
            f"Strong congestion incoming — fee {latest_fee:.2f} sat/vB "
            "rising with hashrate surge"
        )
        return {
            "type": "combined_congestion",
            "severity": "high",
            "status": "red",
            "message": message,
            "action": "Wait before sending any transactions",
        }

    if is_rising:
        message = (
            f"Congestion building — fee rising 3+ consecutive blocks "
            f"({latest_fee:.2f} sat/vB)"
        )
        return {
            "type": "fee_trend_rising",
            "severity": "medium",
            "status": "red",
            "message": message,
            "action": "Consider waiting for fees to stabilize",
        }

    if is_falling and not hashrate_rising:
        message = (
            f"Cheap transfer window — fee {latest_fee:.2f} sat/vB "
            f"below recent avg ({avg_fee:.2f})"
        )
        return {
            "type": "cheap_window",
            "severity": "low",
            "status": "green",
            "message": message,
            "action": "Good time to transfer now",
        }

    return None


def build_alerts(
    df: pd.DataFrame | None,
    prices: list[float],
    fee_spike_threshold: float,
    price_breakout_lookback: int,
    hashrate: float | None = None,
    whale_transactions: list[dict[str, Any]] | None = None,
    whale_alert_threshold_btc: float = 100,
) -> list[dict[str, str]]:
    alerts: list[dict[str, str]] = []

    # Fee spike
    fee_alert = fee_spike_alert(df, fee_spike_threshold)
    if fee_alert:
        alerts.append(fee_alert)

    # Price breakout
    price_alert = price_breakout_alert(prices, price_breakout_lookback)
    if price_alert:
        is_breakout = "above" in price_alert
        alerts.append({
            "type": "price_breakout",
            "severity": "medium",
            "status": "red" if is_breakout else "yellow",
            "message": price_alert,
            "action": "Review your position" if is_breakout else "Monitor price closely",
        })

    # Fee trend + combined signal
    trend_alert = fee_trend_alert(df, hashrate)
    if trend_alert:
        alerts.append(trend_alert)

    whale_alert = whale_transaction_alert(whale_transactions or [], whale_alert_threshold_btc)
    if whale_alert:
        alerts.append(whale_alert)

    return alerts


def get_security_overview(settings: Settings) -> dict[str, Any]:
    if _persistent_cache_is_fresh("security_cache", SECURITY_TTL_SECONDS):
        cached = _persistent_cache_value("security_cache")
        cached["updated_at"] = _persistent_cache_updated_at("security_cache")
        return cached

    stale = _persistent_cache_value("security_cache")
    with _persistent_cache_lock:
        if "security_cache" in _persistent_cache_refreshing:
            if not _cache_value_is_empty(stale):
                logger.debug("cache refresh skipped key=security_cache reason=already_running")
                return stale
            raise RuntimeError("security_cache refresh already running")
        _persistent_cache_refreshing.add("security_cache")
    try:
        from .security_services import (
            get_51_attack_risk,
            get_double_spend_attempts,
            get_invalid_block_attempts,
            get_reorg_events,
        )

        payload = {
            "double_spend": _sanitize_security_metric(
                get_double_spend_attempts(rpc_call, settings),
                {"orphan_count": 0, "orphans": [], "active_height": 0},
            ),
            "attack_51": _sanitize_attack_risk(get_51_attack_risk(settings)),
            "invalid_blocks": _sanitize_security_metric(
                get_invalid_block_attempts(rpc_call, settings),
                {"invalid_count": 0, "invalid_chains": []},
            ),
            "reorgs": _sanitize_security_metric(
                get_reorg_events(rpc_call, settings),
                {"reorg_count": 0, "reorgs": [], "current_height": 0, "max_branch_length": 0},
            ),
            "updated_at": _utc_now_iso(),
            "status": "ok",
        }
        _set_persistent_cache("security_cache", payload, "ok")
        logger.info("nodes/security refreshed")
        return payload
    except Exception as exc:
        logger.warning("[ERROR] Using cached fallback: %s", exc)
        cached = _persistent_cache_value("security_cache")
        if cached:
            cached["updated_at"] = _persistent_cache_updated_at("security_cache")
            cached["status"] = "stale"
            return cached
        fallback = safe_security_payload()
        fallback["updated_at"] = _utc_now_iso()
        return fallback
    finally:
        with _persistent_cache_lock:
            _persistent_cache_refreshing.discard("security_cache")


def _sanitize_security_metric(metric: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    payload = deepcopy(defaults)
    payload.update(metric or {})
    risk = normalize_risk_level(payload.get("risk_level"), SAFE_SECURITY_RISK)
    payload["risk_level"] = risk
    for key, value in list(payload.items()):
        if value is None:
            if key.endswith("_count") or key.endswith("_height") or key == "branch_len":
                payload[key] = 0
            elif isinstance(defaults.get(key), list):
                payload[key] = []
            else:
                payload[key] = 0
    return payload


def _sanitize_attack_risk(metric: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "pools": [],
        "top_pool_share": 0,
        "risk_level": SAFE_SECURITY_RISK,
        "period": "7 days",
    }
    payload.update(metric or {})
    top_share = _to_float_or_none(payload.get("top_pool_share")) or 0
    payload["top_pool_share"] = round(top_share, 2)
    payload["risk_level"] = attack_risk_from_share(top_share)
    return payload


def attack_risk_from_share(share: float) -> str:
    if share > 30:
        return "high"
    if share >= 20:
        return "medium"
    return "low"


def normalize_risk_level(level: Any, fallback: str = "low") -> str:
    normalized = str(level or "").strip().lower()
    if normalized in {"safe", "low", "medium", "high", "critical"}:
        return "low" if normalized == "safe" else normalized
    return fallback


def snapshot() -> dict[str, Any]:
    with state.lock:
        fee_data = None if state.fee_data is None else state.fee_data.copy()
        return {
            "fee_data": fee_data,
            "table_html": state.table_html,
            "hashrate": state.hashrate,
            "node_count": state.node_count,
            "btc_price": state.btc_price,
            "btc_change_24h_usd": state.btc_change_24h_usd,
            "btc_change_24h_percent": state.btc_change_24h_percent,
            "btc_price_source": state.btc_price_source,
            "btc_price_is_cached": state.btc_price_is_cached,
            "hashrate_history": list(state.hashrate_history),
            "price_history": list(state.price_history),
            "time_labels": list(state.time_labels),
            "price_points": list(state.price_points),
            "hashrate_points": list(state.hashrate_points),
            "metric_timestamps": dict(state.metric_timestamps),
        }
