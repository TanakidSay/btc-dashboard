from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections import deque
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from html import unescape
from pathlib import Path
from threading import Lock
from typing import Any

import pandas as pd
import requests
from requests.auth import HTTPBasicAuth

from .config import Settings

session = requests.Session()
logger = logging.getLogger(__name__)

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
ETF_MAX_AGE_DAYS = 7
SATS_PER_BTC = 100_000_000
BITNODES_LATEST_SNAPSHOT_URL = "https://bitnodes.io/api/v1/snapshots/latest/"
MEMPOOL_RECENT_TX_URL = "https://mempool.space/api/mempool/recent"
COINGLASS_BTC_ETF_FLOW_URL = "https://open-api-v4.coinglass.com/api/etf/bitcoin/flow-history"
SOSOVALUE_BTC_ETF_FLOW_URL = "https://api.sosovalue.xyz/openapi/v2/etf/historicalInflowChart"
FARSIDE_BTC_ETF_FLOW_URL = "https://farside.co.uk/bitcoin-etf-flow-all-data/"
FARSIDE_BTC_ETF_LATEST_URL = "https://farside.co.uk/btc/"
WALLETPILOT_BTC_ETF_URL = "https://www.walletpilot.com/bitcoin-tracker/etfs"
GLOBALCOINGUIDE_BTC_ETF_URL = "https://globalcoinguide.com/research/data/etf-flows"
COINGECKO_TREASURY_URLS = (
    "https://api.coingecko.com/api/v3/entities/public_treasury/bitcoin",
    "https://api.coingecko.com/api/v3/companies/public_treasury/bitcoin",
)
FALLBACK_ETF_FLOW = {
    "latest_date": "",
    "latest_net_flow_usd": 0,
    "7d_flow": 0,
    "trend": "neutral",
    "flow_history": [],
    "source": "fallback",
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
]
FALLBACK_BTC_TREASURY = {
    "total_btc_held": "N/A",
    "treasury_dominance_percent": "N/A",
    "top_holders": [],
    "source": "fallback",
    "status": "error",
    "updated_at": None,
    "error": "",
}
FALLBACK_SUPPLY_OWNERSHIP = {
    "max_supply_btc": BITCOIN_MAX_SUPPLY_BTC,
    "circulating_supply_btc": 0,
    "known_btc": 0,
    "unknown_btc": BITCOIN_MAX_SUPPLY_BTC,
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
_cache_lock = Lock()
_viewer_lock = Lock()
_treasury_cache_lock = Lock()
_treasury_result_cache: CacheEntry | None = None
_last_successful_treasury: dict[str, Any] | None = None
_persistent_cache_lock = Lock()
_persistent_caches: dict[str, PersistentCache] = {
    "treasury_cache": PersistentCache(deepcopy(FALLBACK_BTC_TREASURY)),
    "ownership_cache": PersistentCache(deepcopy(FALLBACK_SUPPLY_OWNERSHIP)),
    "institutional_cache": PersistentCache({}),
    "etf_cache": PersistentCache(deepcopy(FALLBACK_ETF_FLOW)),
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
    return _public_viewer_stats(stats)


def record_view(
    settings: Settings,
    remote_addr: str | None,
    user_agent: str | None,
) -> dict[str, Any]:
    visitor_key = _viewer_key(remote_addr, user_agent)
    with _viewer_lock:
        stats = _load_viewer_stats(settings.viewer_stats_path)
        stats["total_views"] += 1
        if visitor_key not in stats["known_visitors"]:
            stats["known_visitors"].append(visitor_key)
        stats["unique_visitors"] = len(stats["known_visitors"])
        stats["last_viewed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _save_viewer_stats(settings.viewer_stats_path, stats)
    return _public_viewer_stats(stats)


def _viewer_key(remote_addr: str | None, user_agent: str | None) -> str:
    fingerprint = f"{remote_addr or 'unknown'}|{user_agent or 'unknown'}"
    return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()


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
    merged["unique_visitors"] = len(merged["known_visitors"])
    return merged


def _save_viewer_stats(path: Path, stats: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(stats, indent=2), encoding="utf-8")


def _public_viewer_stats(stats: dict[str, Any]) -> dict[str, Any]:
    return {
        "total_views": stats["total_views"],
        "unique_visitors": stats["unique_visitors"],
        "last_viewed_at": stats["last_viewed_at"],
    }


def _default_viewer_stats() -> dict[str, Any]:
    return {
        "total_views": 0,
        "unique_visitors": 0,
        "last_viewed_at": None,
        "known_visitors": [],
    }


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
        return entry.value


def _cache_set(key: str, value: Any, ttl_seconds: int) -> Any:
    with _cache_lock:
        _cache[key] = CacheEntry(value=value, expires_at=time.monotonic() + ttl_seconds)
    return value


def clear_cache() -> None:
    global _treasury_result_cache, _last_successful_treasury
    with _cache_lock:
        _cache.clear()
    with _treasury_cache_lock:
        _treasury_result_cache = None
        _last_successful_treasury = None
    with _persistent_cache_lock:
        _persistent_caches["treasury_cache"] = PersistentCache(deepcopy(FALLBACK_BTC_TREASURY))
        _persistent_caches["ownership_cache"] = PersistentCache(deepcopy(FALLBACK_SUPPLY_OWNERSHIP))
        _persistent_caches["institutional_cache"] = PersistentCache({})
        _persistent_caches["etf_cache"] = PersistentCache(deepcopy(FALLBACK_ETF_FLOW))
        _persistent_caches["security_cache"] = PersistentCache({})


def _cached(key: str, settings: Settings, loader):
    cached_value = _cache_get(key)
    if cached_value is not None:
        return cached_value
    return _cache_set(key, loader(), settings.cache_ttl_seconds)


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
    return _cached("fee_data", settings, lambda: _load_fee_data_with_fallbacks(settings)).copy()


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
    return _cached("hashrate", settings, lambda: _get_hashrate_with_fallbacks(settings))


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
    return _cached("node_count", settings, lambda: _get_node_count_with_fallbacks(settings))


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
    return _cached("btc_price", settings, lambda: _get_btc_price_with_fallbacks(settings))


def _get_btc_price_with_fallbacks(settings: Settings) -> float | None:
    providers = (
        _get_btc_price_from_mempool,
        _get_btc_price_from_coingecko,
    )
    for provider in providers:
        try:
            price = provider(settings)
        except (requests.RequestException, KeyError, TypeError, ValueError) as exc:
            logger.warning("%s failed: %s", provider.__name__, exc)
            continue
        if price is not None:
            return MetricValue(price, _provider_source(provider.__name__))
    return None


def _get_btc_price_from_mempool(settings: Settings) -> float | None:
    data = _get_json("https://mempool.space/api/v1/prices", settings)
    usd_price = data.get("USD")
    if usd_price is None:
        return None
    return float(usd_price)


def _get_btc_price_from_coingecko(settings: Settings) -> float:
    data = _get_json(
        "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd",
        settings,
    )
    return float(data["bitcoin"]["usd"])


def get_etf_flow(settings: Settings) -> dict[str, Any]:
    return _cached_resource(
        "etf_cache",
        300,
        lambda: _get_etf_flow_with_fallback(settings),
        "[CACHE] ETF refreshed",
        "[ERROR] Using cached fallback",
        deepcopy(FALLBACK_ETF_FLOW),
    )


def _get_etf_flow_with_fallback(settings: Settings) -> dict[str, Any]:
    if settings.sosovalue_api_key:
        soso_data = _get_etf_flow_from_sosovalue(settings)
        if soso_data["source"] != "fallback":
            return soso_data
    if settings.coinglass_api_key:
        coinglass_data = _get_etf_flow_from_coinglass(settings)
        if coinglass_data["source"] != "fallback":
            return coinglass_data
    for loader in (_get_etf_flow_from_farside_latest, _get_etf_flow_from_farside):
        farside_data = loader(settings)
        if farside_data["source"] != "fallback":
            return farside_data
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


def _get_etf_flow_from_walletpilot(settings: Settings) -> dict[str, Any]:
    try:
        html = _get_text(WALLETPILOT_BTC_ETF_URL, settings)
        text = _clean_page_text(html)
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
            "net_flow_usd": flow_millions * 1_000_000,
            "close_price": 0,
        }
        for date, flow_millions in SEEDED_ETF_FLOW_MILLIONS
    ]
    recent_history = [row for row in history if _etf_date_is_recent(row["date"])]
    if not recent_history:
        raise ValueError("No fresh ETF flow source available")
    payload = _normalize_etf_payload(recent_history[-7:], "seeded-fallback")
    payload["status"] = "stale"
    payload["error"] = "Live ETF flow sources unavailable; using seeded fallback data"
    return payload


def _normalize_etf_payload(history: list[dict[str, Any]], source: str) -> dict[str, Any]:
    if not history:
        raise ValueError("ETF history is empty")
    history = sorted(history, key=_etf_sort_key)
    latest_row = history[-1]
    latest_flow = float(latest_row.get("net_flow_usd", 0) or 0)
    latest_date = str(latest_row.get("date") or "")
    if not _etf_date_is_recent(latest_date):
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
    return {
        "latest_date": latest_date,
        "latest_net_flow_usd": latest_flow,
        "7d_flow": seven_day_flow,
        "trend": trend,
        "flow_history": normalized_history,
        "source": source,
        "error": "",
    }


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
        300,
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
    }, payload.get("status", "ok"))
    return payload


def _get_btc_treasury_with_fallback(settings: Settings) -> dict[str, Any]:
    headers = API_HEADERS.copy()
    if settings.coingecko_demo_api_key:
        headers["x-cg-demo-api-key"] = settings.coingecko_demo_api_key

    providers = (
        ("coingecko-public-treasury", COINGECKO_TREASURY_URLS[0]),
        ("coingecko-company-treasury", COINGECKO_TREASURY_URLS[1]),
    )
    errors: list[str] = []

    for source_name, url in providers:
        try:
            data = _get_json_with_headers_retry(url, settings, headers, attempts=3)
            payload = _normalize_treasury_payload(data, source_name)
            if _treasury_payload_is_valid(payload):
                return _remember_successful_treasury(payload)
            errors.append(f"{source_name}: invalid treasury payload")
        except (requests.RequestException, KeyError, TypeError, ValueError) as exc:
            logger.warning("%s treasury request failed: %s", source_name, exc)
            errors.append(f"{source_name}: {exc}")
            continue
    raise RuntimeError(" | ".join(errors) if errors else "treasury source unavailable")


def _normalize_treasury_payload(data: dict[str, Any], source: str) -> dict[str, Any]:
    holders = data.get("companies") or data.get("entities") or []
    top_holders = []
    for holder in holders[:5]:
        top_holders.append({
            "name": holder.get("name", "Unknown"),
            "symbol": holder.get("symbol"),
            "btc_held": _first_number(holder, ("total_holdings", "amount")),
            "supply_percent": _first_number(
                holder,
                ("percentage_of_total_supply", "supply_percent"),
            ),
        })
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
        "status": status,
        "updated_at": updated_at,
        "error": error,
    }


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
        return cached

    try:
        refreshed = refresh_fn()
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
        300,
        lambda: _get_btc_supply_ownership(settings),
        "[CACHE] Ownership refreshed",
        "[CACHE] Ownership fallback used",
        deepcopy(FALLBACK_SUPPLY_OWNERSHIP),
    )


def _get_btc_supply_ownership(settings: Settings) -> dict[str, Any]:
    try:
        treasury = get_btc_treasury_holdings(settings)
        treasury_btc = _to_float_or_none(treasury.get("total_btc_held"))
        circulating_supply = _get_circulating_supply(settings)

        ownership = []
        if treasury_btc is not None:
            ownership.append(_ownership_row(
                "Public companies and governments",
                treasury_btc,
                "coingecko",
                "reported",
            ))
        ownership.append(_ownership_row(
            "Satoshi Nakamoto estimate",
            SATOSHI_ESTIMATED_BTC,
            "research estimate",
            "estimated",
        ))

        known_btc = sum(row["btc"] for row in ownership)
        unknown_btc = max(BITCOIN_MAX_SUPPLY_BTC - known_btc, 0)
        ownership.append(_ownership_row(
            "Unattributed wallets, exchanges, miners, lost coins and individuals",
            unknown_btc,
            "on-chain unattributed",
            "estimated",
        ))

        return {
            "max_supply_btc": BITCOIN_MAX_SUPPLY_BTC,
            "circulating_supply_btc": circulating_supply,
            "known_btc": round(known_btc, 2),
            "unknown_btc": round(unknown_btc, 2),
            "ownership": ownership,
            "top_holders": treasury.get("top_holders", []),
            "source": "coingecko + estimates",
            "error": None,
            "note": "Bitcoin ownership is estimated because addresses are pseudonymous.",
        }
    except Exception as exc:
        logger.warning("BTC supply ownership failed: %s", exc)
        fallback = FALLBACK_SUPPLY_OWNERSHIP.copy()
        fallback["error"] = str(exc)
        return fallback


def _ownership_row(label: str, btc: float, source: str, confidence: str) -> dict[str, Any]:
    return {
        "label": label,
        "btc": round(float(btc), 2),
        "percent_of_max_supply": round((float(btc) / BITCOIN_MAX_SUPPLY_BTC) * 100, 2),
        "source": source,
        "confidence": confidence,
    }


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
    return _cached("whale_transactions", settings, lambda: _get_recent_whale_transactions(settings))


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
    if _persistent_cache_is_fresh("security_cache", settings.cache_ttl_seconds):
        cached = _persistent_cache_value("security_cache")
        cached["updated_at"] = _persistent_cache_updated_at("security_cache")
        return cached

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
            "hashrate_history": list(state.hashrate_history),
            "price_history": list(state.price_history),
            "time_labels": list(state.time_labels),
            "price_points": list(state.price_points),
            "hashrate_points": list(state.hashrate_points),
            "metric_timestamps": dict(state.metric_timestamps),
        }
