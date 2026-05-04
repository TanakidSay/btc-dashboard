from __future__ import annotations

import datetime as dt
import logging
import os
import sys
import threading
import time
from pathlib import Path

import pandas as pd

if __package__:
    from .config import Settings
    from .notifications import send_notification
    from .services import (
        FALLBACK_NODE_COUNT,
        MetricValue,
        append_metric_point,
        build_table_html,
        configure_state,
        fee_spike_alert,
        format_hashrate,
        get_btc_price_result,
        get_btc_supply_ownership,
        get_btc_treasury_holdings,
        get_etf_flow,
        get_fee_data,
        get_hashrate_chart_points,
        get_hashrate_result,
        get_node_count_result,
        get_security_overview,
        state,
    )
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from btc_dashboard.config import Settings
    from btc_dashboard.notifications import send_notification
    from btc_dashboard.services import (
        FALLBACK_NODE_COUNT,
        MetricValue,
        append_metric_point,
        build_table_html,
        configure_state,
        fee_spike_alert,
        format_hashrate,
        get_btc_price_result,
        get_btc_supply_ownership,
        get_btc_treasury_holdings,
        get_etf_flow,
        get_fee_data,
        get_hashrate_chart_points,
        get_hashrate_result,
        get_node_count_result,
        get_security_overview,
        state,
    )

logger = logging.getLogger(__name__)

_worker_thread: threading.Thread | None = None
_stop_event = threading.Event()
_last_metric_refresh: dict[str, float] = {}
_METRIC_INTERVALS = {
    "price": 60,
    "hashrate": 300,
    "treasury": 300,
    "etf": 300,
    "network": 600,
    "security": 600,
}


def warm_local_cache(settings: Settings) -> None:
    try:
        fee_data = get_fee_data(settings)
        table_html = build_table_html(fee_data, settings.max_table_rows)
        price_metric = get_btc_price_result(settings)
        hashrate_metric = get_hashrate_result(settings)
        hashrate_points = get_hashrate_chart_points(settings)
        node_metric = get_node_count_result(settings)
        get_etf_flow(settings)
        get_btc_treasury_holdings(settings)
        get_btc_supply_ownership(settings)
        get_security_overview(settings)

        with state.lock:
            state.fee_data = fee_data.copy()
            state.table_html = table_html
            if _val(price_metric) is not None:
                state.btc_price = _val(price_metric)
            if _val(hashrate_metric) is not None:
                state.hashrate = _val(hashrate_metric)
            if hashrate_points:
                state.hashrate_points.clear()
                state.hashrate_points.extend(hashrate_points)
                state.hashrate_history.clear()
                state.hashrate_history.extend(point["value"] for point in hashrate_points[-settings.max_chart_rows :])
            if _val(node_metric) not in {None, FALLBACK_NODE_COUNT}:
                state.node_count = _val(node_metric)

        now_iso = _utc_now_iso()
        append_metric_point("price", state.btc_price, now_iso)
        if not hashrate_points:
            append_metric_point("hashrate", state.hashrate, now_iso)
        with state.lock:
            state.metric_timestamps["price"] = now_iso
            state.metric_timestamps["hashrate"] = now_iso
            state.metric_timestamps["network"] = now_iso

        logger.info("Cache warmed successfully")
    except Exception:
        logger.exception("Failed to warm cache")


def refresh_once(settings: Settings) -> None:
    try:
        fee_data = get_fee_data(settings)
        table_html = build_table_html(fee_data, settings.max_table_rows)
        now_monotonic = time.monotonic()
        now_iso = _utc_now_iso()

        with state.lock:
            force_price = state.btc_price is None
            force_hashrate = state.hashrate is None
            force_network = state.node_count in {None, FALLBACK_NODE_COUNT}

        btc_price = get_btc_price_result(settings) if _due("price", now_monotonic, force_price) else None
        hashrate = get_hashrate_result(settings) if _due("hashrate", now_monotonic, force_hashrate) else None
        node_count = get_node_count_result(settings) if _due("network", now_monotonic, force_network) else None

        if _due("etf", now_monotonic):
            get_etf_flow(settings)
            logger.info("[WORKER] ETF updated")
        if _due("treasury", now_monotonic):
            get_btc_treasury_holdings(settings)
            get_btc_supply_ownership(settings)
        if _due("security", now_monotonic):
            get_security_overview(settings)

        with state.lock:
            price_val = _val(btc_price)
            hash_val = _val(hashrate)
            node_val = _val(node_count)

            if price_val is not None:
                state.btc_price = price_val
                state.metric_timestamps["price"] = now_iso
            if hash_val is not None:
                state.hashrate = hash_val
                state.metric_timestamps["hashrate"] = now_iso
            if node_val is not None and node_val != FALLBACK_NODE_COUNT:
                state.node_count = node_val
                state.metric_timestamps["network"] = now_iso

            state.fee_data = fee_data.copy()
            state.table_html = table_html

            if state.hashrate is not None:
                state.hashrate_history.append(state.hashrate)
            if state.btc_price is not None:
                state.price_history.append(state.btc_price)

        if price_val is not None:
            append_metric_point("price", price_val, now_iso)
            logger.info("[WORKER] Price updated")
        if hash_val is not None:
            append_metric_point("hashrate", hash_val, now_iso)
            logger.info("[WORKER] Hashrate updated")

        logger.info(
            "Update OK | price=%s source=%s | hashrate=%s source=%s | nodes=%s source=%s",
            f"${state.btc_price:,.2f}" if state.btc_price is not None else "N/A",
            _source(btc_price) if btc_price is not None else "cached",
            format_hashrate(state.hashrate),
            _source(hashrate) if hashrate is not None else "cached",
            state.node_count if state.node_count is not None else "N/A",
            _source(node_count) if node_count is not None else "cached",
        )

        notify_fee_spike_if_needed(fee_data, settings)
    except Exception:
        logger.exception("refresh_once crashed")


def _utc_now_iso() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _val(metric: MetricValue | None):
    return None if metric is None else metric.value


def _source(metric: MetricValue | None) -> str:
    return "fallback" if metric is None else metric.source


def _due(metric: str, now_monotonic: float, force: bool = False) -> bool:
    last = _last_metric_refresh.get(metric)
    interval = _METRIC_INTERVALS[metric]
    if force or last is None or (now_monotonic - last) >= interval:
        _last_metric_refresh[metric] = now_monotonic
        return True
    return False


def notify_fee_spike_if_needed(fee_data: pd.DataFrame, settings: Settings) -> None:
    try:
        alert = fee_spike_alert(fee_data, settings.fee_spike_threshold)
        if not alert:
            return

        key = (
            alert.get("height"),
            round(float(alert.get("fee", 0)), 2),
            alert.get("threshold"),
        )

        now = time.monotonic()

        with state.lock:
            if state.last_fee_spike_notification_key == key:
                return
            if now - state.last_fee_spike_notification_ts < settings.notification_cooldown_seconds:
                return

        if send_notification(alert, settings):
            with state.lock:
                state.last_fee_spike_notification_key = key
                state.last_fee_spike_notification_ts = now
    except Exception:
        logger.exception("Alert system failed")


def background_worker(settings: Settings) -> None:
    logger.info("Worker started (refresh=%ss)", settings.refresh_seconds)
    while not _stop_event.is_set():
        refresh_once(settings)
        time.sleep(settings.refresh_seconds)


def start_background_worker(settings: Settings) -> threading.Thread:
    global _worker_thread

    configure_state(settings)
    warm_local_cache(settings)

    if _worker_thread and _worker_thread.is_alive():
        return _worker_thread

    _stop_event.clear()
    _worker_thread = threading.Thread(
        target=background_worker,
        args=(settings,),
        daemon=True,
    )
    _worker_thread.start()
    logger.info("Worker thread launched")
    return _worker_thread


def stop_background_worker() -> None:
    _stop_event.set()
    logger.info("Worker stop signal sent")


def run_worker() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())

    settings = Settings.from_env()
    configure_state(settings)

    logger.info("Starting dashboard worker; refresh_seconds=%s", settings.refresh_seconds)

    try:
        background_worker(settings)
    except KeyboardInterrupt:
        stop_background_worker()


if __name__ == "__main__":
    run_worker()
