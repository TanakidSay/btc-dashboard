from __future__ import annotations

import datetime as dt
from hmac import compare_digest

import pandas as pd
from flask import Blueprint, current_app, jsonify, render_template, request

from .config import Settings
from .services import (
    append_metric_point,
    build_alerts,
    format_hashrate,
    get_btc_price_result,
    get_btc_supply_ownership,
    get_btc_treasury_holdings,
    get_etf_flow,
    get_recent_whale_transactions,
    get_security_overview,
    get_viewer_analytics,
    get_viewer_stats,
    record_view,
    snapshot,
    state,
    update_manual_etf_flow_file,
)
from .signal_engine import latest_signals, pending_signal_status, signals_policy
from .x_poster import get_x_status, post_to_x

api = Blueprint("api", __name__)
X_TEST_POST_TEXT = (
    "BTC Window X posting test ✅ Signal engine is connected. "
    "https://btcwindow.up.railway.app/"
)


def _settings() -> Settings:
    return current_app.config["DASHBOARD_SETTINGS"]


def _utc_now_iso() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@api.route("/")
def index():
    record_view(
        _settings(),
        request.headers.get("X-Forwarded-For", request.remote_addr),
        request.headers.get("User-Agent"),
        request.headers.get("Referer"),
        request.path,
        request.headers.get("CF-IPCountry") or request.headers.get("X-Country-Code"),
    )
    data = snapshot()
    return render_template("dashboard.html", table=data["table_html"])


def _empty_fee_response():
    return {
        "height": [],
        "fee": [],
        "fastestFee": "N/A",
        "halfHourFee": "N/A",
        "hourFee": "N/A",
        "source": "fallback",
    }


def _fee_recommendation_from_history(fees):
    numeric_fees = pd.Series(fees, dtype="float64").dropna()
    if numeric_fees.empty:
        return {
            "fastestFee": "N/A",
            "halfHourFee": "N/A",
            "hourFee": "N/A",
            "source": "fallback",
        }
    fastest_fee = max(float(numeric_fees.quantile(0.75)), float(numeric_fees.iloc[-1]))
    return {
        "fastestFee": round(fastest_fee, 1),
        "halfHourFee": round(float(numeric_fees.median()), 1),
        "hourFee": round(float(numeric_fees.quantile(0.25)), 1),
        "source": "fee history estimate",
    }


@api.route("/api/fees")
def api_fees():
    try:
        settings = _settings()
        data = snapshot()
        fee_data = data["fee_data"]
        if fee_data is None or fee_data.empty:
            return jsonify(_empty_fee_response())
        chart_data = fee_data.tail(settings.max_chart_rows)
        fees = chart_data["sat_per_vbyte"].tolist()
        recommendation = _fee_recommendation_from_history(fees)
        return jsonify({
            "height": chart_data["height"].tolist(),
            "fee": fees,
            **recommendation,
        })
    except Exception as exc:
        current_app.logger.exception("/api/fees failed: %s", exc)
        return jsonify(_empty_fee_response())


@api.route("/api/transactions")
def api_transactions():
    try:
        settings = _settings()
        data = snapshot()
        fee_data = data["fee_data"]
        if fee_data is None or fee_data.empty:
            return jsonify({"height": [], "tx_count": []})
        chart_data = fee_data.tail(settings.max_chart_rows)
        return jsonify({
            "height": chart_data["height"].tolist(),
            "tx_count": chart_data["tx_count"].tolist(),
        })
    except Exception as exc:
        current_app.logger.exception("/api/transactions failed: %s", exc)
        return jsonify({"height": [], "tx_count": []})


@api.route("/api/hashrate")
def api_hashrate():
    try:
        data = snapshot()
        points = data.get("hashrate_points", [])
        return jsonify({
            "time": [point["timestamp"] for point in points],
            "hashrate": [point["value"] for point in points],
            "latest": format_hashrate(data["hashrate"]),
            "latest_raw": data["hashrate"] if data["hashrate"] is not None else 0,
            "updated_at": data.get("metric_timestamps", {}).get("hashrate"),
        })
    except Exception as exc:
        current_app.logger.exception("/api/hashrate failed: %s", exc)
        return jsonify({
            "time": [],
            "hashrate": [],
            "latest": "N/A",
            "latest_raw": 0,
            "updated_at": None,
        })


@api.route("/api/price")
def api_price():
    try:
        metric = get_btc_price_result(_settings())
        if metric is not None and metric.value is not None:
            price_value = float(metric.value)
            now_iso = _utc_now_iso()
            with state.lock:
                state.btc_price = price_value
                state.btc_change_24h_usd = metric.change_24h_usd
                state.btc_change_24h_percent = metric.change_24h_percent
                state.btc_price_source = metric.source
                state.btc_price_is_cached = metric.is_cached
                if not metric.is_cached:
                    state.metric_timestamps["price"] = now_iso
            if not metric.is_cached:
                append_metric_point("price", price_value, now_iso)

        data = snapshot()
        points = data.get("price_points", [])
        latest = data["btc_price"] if data["btc_price"] else "N/A"
        return jsonify({
            "time": [point["timestamp"] for point in points],
            "history": [point["value"] for point in points],
            "price": latest,
            "latest": latest,
            "price_usd": latest,
            "change_24h_usd": data.get("btc_change_24h_usd"),
            "change_24h_percent": data.get("btc_change_24h_percent"),
            "updated_at": data.get("metric_timestamps", {}).get("price"),
            "source": data.get("btc_price_source", "unknown"),
            "is_cached": data.get("btc_price_is_cached", True),
        })
    except Exception as exc:
        current_app.logger.exception("/api/price failed: %s", exc)
        return jsonify({
            "time": [],
            "history": [],
            "price": "N/A",
            "latest": "N/A",
            "price_usd": "N/A",
            "change_24h_usd": None,
            "change_24h_percent": None,
            "updated_at": None,
            "source": "fallback",
            "is_cached": True,
        })


@api.route("/api/network")
def api_network():
    try:
        data = snapshot()
        return jsonify({
            "hashrate": format_hashrate(data["hashrate"]),
            "hashrate_raw": data["hashrate"] if data["hashrate"] is not None else 0,
            "nodes": data["node_count"] if data["node_count"] else "N/A",
            "updated_at": data.get("metric_timestamps", {}).get("network"),
        })
    except Exception as exc:
        current_app.logger.exception("/api/network failed: %s", exc)
        return jsonify({"hashrate": "N/A", "hashrate_raw": 0, "nodes": "N/A", "updated_at": None})


@api.route("/api/etf")
def api_etf():
    try:
        return jsonify(get_etf_flow(_settings()))
    except Exception as exc:
        current_app.logger.exception("/api/etf failed: %s", exc)
        return jsonify({
            "latest_date": "",
            "latest_net_flow_usd": 0,
            "7d_flow": 0,
            "trend": "neutral",
            "flow_history": [],
            "source": "fallback",
            "updated_at": "",
            "status": "error",
            "error": str(exc),
        })


@api.route("/api/admin/etf-flows", methods=["POST"])
def api_admin_etf_flows():
    settings = _settings()
    if not _is_valid_etf_admin_request(settings):
        current_app.logger.warning("unauthorized ETF admin update")
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify({"ok": False, "error": "Invalid JSON body"}), 400

    try:
        updated = update_manual_etf_flow_file(settings, payload)
        return jsonify({
            "ok": True,
            "source": updated["source"],
            "source_label": updated["source_label"],
            "latest_date": updated["latest_date"],
            "latest_net_flow_usd": updated["latest_net_flow_usd"],
            "is_stale": updated["is_stale"],
            "data_note": updated["data_note"],
        })
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except OSError as exc:
        current_app.logger.exception("ETF admin update failed: %s", exc)
        return jsonify({"ok": False, "error": "ETF update write failed"}), 500


def _is_valid_etf_admin_request(settings: Settings) -> bool:
    if not settings.etf_admin_token:
        return False
    bearer_prefix = "Bearer "
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith(bearer_prefix):
        return False
    token = authorization.removeprefix(bearer_prefix).strip()
    return compare_digest(token, settings.etf_admin_token)


@api.route("/api/treasury")
def api_treasury():
    try:
        return jsonify(get_btc_treasury_holdings(_settings()))
    except Exception as exc:
        current_app.logger.exception("/api/treasury failed: %s", exc)
        return jsonify({
            "total_btc_held": "N/A",
            "treasury_dominance_percent": "N/A",
            "top_holders": [],
            "source": "fallback",
            "status": "error",
            "updated_at": None,
            "error": str(exc),
        })


@api.route("/api/ownership")
@api.route("/api/supply-ownership")
def api_supply_ownership():
    try:
        return jsonify(get_btc_supply_ownership(_settings()))
    except Exception as exc:
        current_app.logger.exception("/api/supply-ownership failed: %s", exc)
        return jsonify({
            "circulating_supply": "N/A",
            "max_supply": 21_000_000,
            "remaining_to_mine": "N/A",
            "percent_mined": "N/A",
            "estimated_lost_btc": {"low": 3_000_000, "high": 4_000_000},
            "effective_liquid_supply": {"low": "N/A", "high": "N/A"},
            "categories": [],
            "insights": [],
            "max_supply_btc": 21_000_000,
            "circulating_supply_btc": "N/A",
            "ownership": [],
            "top_holders": [],
            "source": "fallback",
            "status": "error",
            "updated_at": "",
            "error": str(exc),
            "note": "Bitcoin addresses are pseudonymous, so owner attribution is estimated.",
        })


@api.route("/api/metrics")
def api_metrics():
    data = snapshot()
    return jsonify({
        "btc_price": data["btc_price"] if data["btc_price"] else "N/A",
        "hashrate": format_hashrate(data["hashrate"]),
        "hashrate_raw": data["hashrate"] if data["hashrate"] is not None else 0,
        "nodes": data["node_count"] if data["node_count"] else "N/A",
        "time": data.get("metric_timestamps", {}).get("price"),
    })


@api.route("/api/viewers")
def api_viewers():
    try:
        return jsonify(get_viewer_stats(_settings()))
    except Exception as exc:
        current_app.logger.exception("/api/viewers failed: %s", exc)
        return jsonify({
            "total_views": 0,
            "unique_visitors": 0,
            "last_viewed_at": None,
            "suppressed_views": 0,
            "dedupe_window_seconds": 60,
        })


@api.route("/api/viewer-analytics")
def api_viewer_analytics():
    try:
        return jsonify(get_viewer_analytics(_settings()))
    except Exception as exc:
        current_app.logger.exception("/api/viewer-analytics failed: %s", exc)
        return jsonify({
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
            "privacy": "Aggregate only; IP addresses are not stored.",
            "error": str(exc),
        })


@api.route("/api/alert")
def api_alert():
    try:
        settings = _settings()
        data = snapshot()
        try:
            whale_transactions = get_recent_whale_transactions(settings)
        except Exception as exc:
            current_app.logger.warning("whale transaction lookup failed: %s", exc)
            whale_transactions = []
        alerts = build_alerts(
            data["fee_data"],
            data["price_history"],
            fee_spike_threshold=settings.fee_spike_threshold,
            price_breakout_lookback=settings.price_breakout_lookback,
            hashrate=data["hashrate"],
            whale_transactions=whale_transactions,
            whale_alert_threshold_btc=settings.whale_alert_threshold_btc,
        )
        alert_text = " | ".join(alert["message"] for alert in alerts) if alerts else None
        return jsonify({"alert": alert_text, "alerts": alerts})
    except Exception as exc:
        current_app.logger.exception("/api/alert failed: %s", exc)
        return jsonify({"alert": None, "alerts": []})


@api.route("/api/signals")
def api_signals():
    try:
        return jsonify(latest_signals(_settings()))
    except Exception as exc:
        current_app.logger.exception("/api/signals failed: %s", exc)
        return jsonify({
            "signals": [],
            "x_posting_enabled": False,
            "cooldown_seconds": 3600,
            "dashboard_url": "https://btcwindow.up.railway.app/",
            "post_state": {"posted_key_count": 0, "last_normal_posted_at": 0, "last_post": None},
            "error": str(exc),
        })


@api.route("/api/x-status")
def api_x_status():
    try:
        settings = _settings()
        status = get_x_status(settings)
        status.update(pending_signal_status(settings))
        return jsonify(status)
    except Exception as exc:
        current_app.logger.exception("/api/x-status failed: %s", exc)
        return jsonify({
            "enabled": False,
            "test_enabled": False,
            "credentials_configured": False,
            "last_post_time": None,
            "last_post_date": None,
            "last_error": str(exc),
            "cooldown_remaining_seconds": 0,
            "daily_post_count": 0,
            "daily_limit_remaining": 1,
            "last_block_reason": str(exc),
            "posted_events_count": 0,
        })


@api.route("/api/x-test-post", methods=["GET", "POST"])
def api_x_test_post():
    settings = _settings()
    if not settings.enable_x_test_post:
        return jsonify({
            "ok": False,
            "mode": "error",
            "text": X_TEST_POST_TEXT,
            "last_post_time": get_x_status(settings)["last_post_time"],
            "last_error": "X test posting is disabled",
        }), 403

    result = post_to_x(
        X_TEST_POST_TEXT,
        settings,
        event_id="manual:x-test-post",
        signal_type="manual_test",
        bypass_cooldown=True,
    )
    status = get_x_status(settings)
    if result.posted:
        mode = "posted"
    elif result.reason == "x_posting_disabled":
        mode = "preview"
    else:
        mode = "error"
    return jsonify({
        "ok": result.posted or mode == "preview",
        "mode": mode,
        "text": X_TEST_POST_TEXT,
        "last_post_time": status["last_post_time"],
        "last_error": status["last_error"],
    })


@api.route("/api/signals-policy")
def api_signals_policy():
    return jsonify(signals_policy())


@api.route("/api/security")
def api_security():
    try:
        payload = get_security_overview(_settings())
        payload["double_spend"]["active_height"] = payload["double_spend"].get("active_height") or 0
        payload["reorgs"]["current_height"] = payload["reorgs"].get("current_height") or 0
        payload["updated_at"] = payload.get("updated_at") or ""
        return jsonify(payload)
    except Exception as exc:
        current_app.logger.exception("/api/security failed: %s", exc)
        return jsonify({
            "double_spend": {
                "orphan_count": 0,
                "orphans": [],
                "active_height": 0,
                "risk_level": "low",
            },
            "attack_51": {"pools": [], "top_pool_share": 0, "risk_level": "low"},
            "invalid_blocks": {"invalid_count": 0, "invalid_chains": [], "risk_level": "low"},
            "reorgs": {
                "reorg_count": 0,
                "reorgs": [],
                "current_height": 0,
                "max_branch_length": 0,
                "risk_level": "low",
            },
            "updated_at": "",
            "status": "error",
        })


@api.route("/health")
@api.route("/healthz")
def healthz():
    return jsonify({"status": "ok"})
