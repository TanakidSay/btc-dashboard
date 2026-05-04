from __future__ import annotations

import pandas as pd
from flask import Blueprint, current_app, jsonify, render_template, request

from .config import Settings
from .services import (
    build_alerts,
    format_hashrate,
    get_btc_supply_ownership,
    get_btc_treasury_holdings,
    get_etf_flow,
    get_recent_whale_transactions,
    get_security_overview,
    get_viewer_stats,
    record_view,
    snapshot,
)

api = Blueprint("api", __name__)


def _settings() -> Settings:
    return current_app.config["DASHBOARD_SETTINGS"]


@api.route("/")
def index():
    record_view(
        _settings(),
        request.headers.get("X-Forwarded-For", request.remote_addr),
        request.headers.get("User-Agent"),
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
        return jsonify({"time": [], "hashrate": [], "latest": "N/A", "latest_raw": 0, "updated_at": None})


@api.route("/api/price")
def api_price():
    try:
        data = snapshot()
        points = data.get("price_points", [])
        latest = data["btc_price"] if data["btc_price"] else "N/A"
        return jsonify({
            "time": [point["timestamp"] for point in points],
            "price": [point["value"] for point in points],
            "latest": latest,
            "price_usd": latest,
            "updated_at": data.get("metric_timestamps", {}).get("price"),
        })
    except Exception as exc:
        current_app.logger.exception("/api/price failed: %s", exc)
        return jsonify({"time": [], "price": [], "latest": "N/A", "price_usd": "N/A", "updated_at": None})


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


@api.route("/api/supply-ownership")
def api_supply_ownership():
    try:
        return jsonify(get_btc_supply_ownership(_settings()))
    except Exception as exc:
        current_app.logger.exception("/api/supply-ownership failed: %s", exc)
        return jsonify({
            "max_supply_btc": 21_000_000,
            "circulating_supply_btc": 0,
            "known_btc": 0,
            "unknown_btc": 21_000_000,
            "ownership": [],
            "top_holders": [],
            "source": "fallback",
            "status": "error",
            "updated_at": None,
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
            "double_spend": {"orphan_count": 0, "orphans": [], "active_height": 0, "risk_level": "low"},
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
