from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import Settings
from .services import (
    build_alerts,
    get_btc_treasury_holdings,
    get_etf_flow,
    get_recent_whale_transactions,
    get_security_overview,
    snapshot,
)
from .x_poster import post_to_x, record_x_block

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
    "mining_pool_concentration",
]
AUTO_POST_BLOCKED_SIGNAL_TYPES = [
    "fee_trend_rising",
    "combined_congestion",
    "low_fee_window",
    "cheap_window",
    "hashrate_spike",
    "hashrate_drop",
    "etf_inflow",
    "etf_outflow",
    "treasury_holdings",
]


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

    for alert in alerts:
        alert_type = alert.get("type", "")
        if alert_type in {"fee_spike", "fee_trend_rising", "combined_congestion"}:
            signals.append(_fee_signal(alert, now_iso))
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
    results = []
    for signal in detect_signals(settings):
        result = _process_signal(signal, settings, state)
        results.append(result)
    _save_post_state(settings.x_signal_state_path, state)
    return results


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
            "strong_fee_spike_type": "fee_spike",
            "security_severity": "critical",
            "pool_concentration_severity": "critical",
        },
        "cooldown_minutes": NORMAL_COOLDOWN_SECONDS // 60,
        "max_posts_per_day": 12,
        "blocked_signal_types": AUTO_POST_BLOCKED_SIGNAL_TYPES,
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
        )

    if signal.signal_type == "whale_alert":
        amount_btc = _signal_amount_btc(signal)
        allowed = amount_btc is not None and amount_btc >= WHALE_BTC
        return _policy_result(
            allowed,
            "whale_threshold_met" if allowed else "whale_below_500_btc",
            True,
            severity,
        )

    if signal.signal_type == "fee_spike":
        allowed = severity == "high"
        return _policy_result(
            allowed,
            "strong_fee_spike" if allowed else "fee_spike_not_strong",
            True,
            severity,
        )

    if signal.signal_type == "security_event":
        allowed = severity == "critical"
        return _policy_result(
            allowed,
            "security_critical" if allowed else "security_not_critical",
            False if allowed else cooldown_applied,
            severity,
        )

    if signal.signal_type == "mining_pool_concentration":
        allowed = severity == "critical"
        return _policy_result(
            allowed,
            "pool_concentration_critical" if allowed else "pool_concentration_not_critical",
            True,
            severity,
        )

    return _policy_result(False, "signal_type_not_auto_postable", cooldown_applied, severity)


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
        signal_type="low_fee_window",
        severity="low",
        message=message,
        post_text=_fit_post(f"Low BTC fee window detected. {message}. Live view: {DASHBOARD_URL}"),
        duplicate_key=_bucket_key("low_fee_window", now_iso),
        immediate=False,
        detected_at=now_iso,
        source=alert,
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
        return {"posted_keys": [], "last_normal_posted_at": 0, "last_post": None}


def _save_post_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def _public_post_state(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "posted_key_count": len(state.get("posted_keys", [])),
        "last_normal_posted_at": state.get("last_normal_posted_at"),
        "last_post": state.get("last_post"),
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
) -> dict[str, Any]:
    return {
        "allowed": allowed,
        "reason": reason,
        "cooldown_applied": cooldown_applied,
        "severity": severity,
    }
