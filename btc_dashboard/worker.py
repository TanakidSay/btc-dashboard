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
        build_table_html,
        configure_state,
        fee_spike_alert,
        format_hashrate,
        get_btc_price_result,
        get_fee_data,
        get_hashrate_result,
        get_node_count_result,
        state,
    )
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from btc_dashboard.config import Settings
    from btc_dashboard.notifications import send_notification
    from btc_dashboard.services import (
        FALLBACK_NODE_COUNT,
        MetricValue,
        build_table_html,
        configure_state,
        fee_spike_alert,
        format_hashrate,
        get_btc_price_result,
        get_fee_data,
        get_hashrate_result,
        get_node_count_result,
        state,
    )

logger = logging.getLogger(__name__)

_worker_thread: threading.Thread | None = None
_stop_event = threading.Event()


# -------------------------
# CACHE WARMUP
# -------------------------
def warm_local_cache(settings: Settings) -> None:
    try:
        fee_data = get_fee_data(settings)
        table_html = build_table_html(fee_data, settings.max_table_rows)

        with state.lock:
            state.fee_data = fee_data.copy()
            state.table_html = table_html

        logger.info("✅ Cache warmed successfully")

    except Exception:
        logger.exception("❌ Failed to warm cache")


# -------------------------
# MAIN REFRESH
# -------------------------
def refresh_once(settings: Settings) -> None:
    try:
        fee_data = get_fee_data(settings)
        table_html = build_table_html(fee_data, settings.max_table_rows)

        btc_price = get_btc_price_result(settings)
        hashrate = get_hashrate_result(settings)
        node_count = get_node_count_result(settings)

        now = dt.datetime.now(dt.UTC).strftime("%H:%M:%S")

        with state.lock:
            price_val = _val(btc_price)
            hash_val = _val(hashrate)
            node_val = _val(node_count)

            # fallback safety
            state.btc_price = price_val or state.btc_price
            state.hashrate = hash_val or state.hashrate
            if node_val is not None and node_val != FALLBACK_NODE_COUNT:
                state.node_count = node_val

            state.fee_data = fee_data.copy()
            state.table_html = table_html

            state.time_labels.append(now)

            if state.hashrate is not None:
                state.hashrate_history.append(state.hashrate)
            if state.btc_price is not None:
                state.price_history.append(state.btc_price)

        logger.info(
            "Update OK | price=%s source=%s | hashrate=%s source=%s | nodes=%s source=%s",
            f"${price_val:,.2f}" if price_val is not None else "N/A",
            _source(btc_price),
            format_hashrate(hash_val),
            _source(hashrate),
            node_val if node_val is not None else "N/A",
            _source(node_count),
        )

        notify_fee_spike_if_needed(fee_data, settings)

    except Exception:
        logger.exception("refresh_once crashed")


# -------------------------
# HELPERS
# -------------------------
def _val(metric: MetricValue | None):
    return None if metric is None else metric.value


def _source(metric: MetricValue | None) -> str:
    return "fallback" if metric is None else metric.source


# -------------------------
# ALERTS
# -------------------------
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


# -------------------------
# WORKER LOOP
# -------------------------
def background_worker(settings: Settings) -> None:
    logger.info("🚀 Worker started (refresh=%ss)", settings.refresh_seconds)

    while not _stop_event.is_set():
        refresh_once(settings)
        time.sleep(settings.refresh_seconds)


# -------------------------
# START / STOP
# -------------------------
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

    logger.info("🟢 Worker thread launched")
    return _worker_thread


def stop_background_worker() -> None:
    _stop_event.set()
    logger.info("🛑 Worker stop signal sent")


# -------------------------
# RUN DIRECT
# -------------------------
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
