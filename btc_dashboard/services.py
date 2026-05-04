from __future__ import annotations

import logging
import re
import time
from collections import deque
from dataclasses import dataclass, field
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
FALLBACK_NODE_COUNT = "N/A"
BITCOIN_MAX_SUPPLY_BTC = 21_000_000
SATOSHI_ESTIMATED_BTC = 1_100_000
BITNODES_LATEST_SNAPSHOT_URL = "https://bitnodes.io/api/v1/snapshots/latest/"
COINGLASS_BTC_ETF_FLOW_URL = "https://open-api-v4.coinglass.com/api/etf/bitcoin/flow-history"
FARSIDE_BTC_ETF_FLOW_URL = "https://farside.co.uk/bitcoin-etf-flow-all-data/"
COINGECKO_TREASURY_URLS = (
    "https://api.coingecko.com/api/v3/entities/public_treasury/bitcoin",
    "https://api.coingecko.com/api/v3/companies/public_treasury/bitcoin",
)
FALLBACK_ETF_FLOW = {
    "latest_net_flow_usd": "N/A",
    "flow_history": [],
    "status": "neutral",
    "source": "fallback",
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
]
FALLBACK_BTC_TREASURY = {
    "total_btc_held": "N/A",
    "treasury_dominance_percent": "N/A",
    "top_holders": [],
    "source": "fallback",
}
FALLBACK_SUPPLY_OWNERSHIP = {
    "max_supply_btc": BITCOIN_MAX_SUPPLY_BTC,
    "circulating_supply_btc": "N/A",
    "known_btc": "N/A",
    "unknown_btc": "N/A",
    "ownership": [],
    "top_holders": [],
    "source": "fallback",
    "note": "Bitcoin addresses are pseudonymous, so owner attribution is estimated.",
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
    last_fee_spike_notification_key: str | None = None
    last_fee_spike_notification_ts: float = 0


state = DashboardState()
_cache: dict[str, CacheEntry] = {}
_cache_lock = Lock()


def configure_state(settings: Settings) -> None:
    with state.lock:
        state.hashrate_history = deque(state.hashrate_history, maxlen=settings.max_chart_rows)
        state.price_history = deque(state.price_history, maxlen=settings.max_chart_rows)
        state.time_labels = deque(state.time_labels, maxlen=settings.max_chart_rows)


def load_fee_data(path: Path, max_rows: int) -> pd.DataFrame:
    path = Path(path)
    if not path.exists():
        return pd.DataFrame(columns=["height", "tx_count", "total_fee_btc", "sat_per_vbyte"])
    return pd.read_csv(path).tail(max_rows)


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
    with _cache_lock:
        _cache.clear()


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


def _get_text(url: str, settings: Settings) -> str:
    response = session.get(url, headers=API_HEADERS, timeout=settings.request_timeout)
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
    return _cached("etf_flow", settings, lambda: _get_etf_flow_with_fallback(settings))


def _get_etf_flow_with_fallback(settings: Settings) -> dict[str, Any]:
    if settings.coinglass_api_key:
        coinglass_data = _get_etf_flow_from_coinglass(settings)
        if coinglass_data["source"] != "fallback":
            return coinglass_data
    farside_data = _get_etf_flow_from_farside(settings)
    if farside_data["source"] != "fallback":
        return farside_data
    return _seeded_etf_flow_fallback()


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

        latest_flow = next(
            (item["net_flow_usd"] for item in reversed(history) if item["net_flow_usd"] != "N/A"),
            "N/A",
        )
        status = "neutral"
        if isinstance(latest_flow, int | float):
            if latest_flow > 0:
                status = "inflow"
            elif latest_flow < 0:
                status = "outflow"
        return {
            "latest_net_flow_usd": latest_flow,
            "flow_history": history,
            "status": status,
            "source": "coinglass",
        }
    except (requests.RequestException, KeyError, TypeError, ValueError) as exc:
        logger.warning("CoinGlass ETF flow request failed: %s", exc)
        fallback = FALLBACK_ETF_FLOW.copy()
        fallback["error"] = str(exc)
        return fallback


def _get_etf_flow_from_farside(settings: Settings) -> dict[str, Any]:
    try:
        html = _get_text(FARSIDE_BTC_ETF_FLOW_URL, settings)
        rows = _parse_farside_etf_rows(html)
        if not rows:
            raise ValueError("Farside ETF flow table has no parsable rows")
        latest_flow = rows[-1]["net_flow_usd"]
        status = "neutral"
        if latest_flow > 0:
            status = "inflow"
        elif latest_flow < 0:
            status = "outflow"
        return {
            "latest_net_flow_usd": latest_flow,
            "flow_history": rows[-30:],
            "status": status,
            "source": "farside",
        }
    except Exception as exc:
        logger.warning("Farside ETF flow request failed: %s", exc)
        fallback = FALLBACK_ETF_FLOW.copy()
        fallback["error"] = str(exc)
        return fallback


def _seeded_etf_flow_fallback() -> dict[str, Any]:
    history = [
        {
            "date": date,
            "net_flow_usd": flow_millions * 1_000_000,
            "close_price": None,
        }
        for date, flow_millions in SEEDED_ETF_FLOW_MILLIONS
    ]
    latest_flow = history[-1]["net_flow_usd"] if history else "N/A"
    status = "neutral"
    if isinstance(latest_flow, int | float):
        if latest_flow > 0:
            status = "inflow"
        elif latest_flow < 0:
            status = "outflow"
    return {
        "latest_net_flow_usd": latest_flow,
        "flow_history": history,
        "status": status,
        "source": "cached farside fallback",
    }


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
    return _cached("btc_treasury", settings, lambda: _get_btc_treasury_with_fallback(settings))


def _get_btc_treasury_with_fallback(settings: Settings) -> dict[str, Any]:
    headers = API_HEADERS.copy()
    if settings.coingecko_demo_api_key:
        headers["x-cg-demo-api-key"] = settings.coingecko_demo_api_key

    for url in COINGECKO_TREASURY_URLS:
        try:
            data = _get_json_with_headers(url, settings, headers)
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
            return {
                "total_btc_held": data.get("total_holdings", "N/A"),
                "treasury_dominance_percent": data.get("market_cap_dominance", "N/A"),
                "top_holders": top_holders,
                "source": "coingecko",
            }
        except (requests.RequestException, KeyError, TypeError, ValueError) as exc:
            logger.warning("CoinGecko treasury request failed: %s", exc)
            continue

    return FALLBACK_BTC_TREASURY.copy()


def get_btc_supply_ownership(settings: Settings) -> dict[str, Any]:
    return _cached("btc_supply_ownership", settings, lambda: _get_btc_supply_ownership(settings))


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
            "unknown",
        ))

        return {
            "max_supply_btc": BITCOIN_MAX_SUPPLY_BTC,
            "circulating_supply_btc": circulating_supply,
            "known_btc": round(known_btc, 2),
            "unknown_btc": round(unknown_btc, 2),
            "ownership": ownership,
            "top_holders": treasury.get("top_holders", []),
            "source": "coingecko + estimates",
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

    return alerts


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
        }
