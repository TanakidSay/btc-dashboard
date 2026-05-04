from __future__ import annotations

import pandas as pd
from flask import Blueprint, current_app, jsonify, render_template

from .config import Settings
from .services import (
    build_alerts,
    format_hashrate,
    get_btc_supply_ownership,
    get_btc_treasury_holdings,
    get_etf_flow,
    snapshot,
)

api = Blueprint("api", __name__)


def _settings() -> Settings:
    return current_app.config["DASHBOARD_SETTINGS"]


@api.route("/")
def index():
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
        return jsonify({
            "time": data["time_labels"],
            "hashrate": data["hashrate_history"],
            "latest": format_hashrate(data["hashrate"]),
            "latest_raw": data["hashrate"],
        })
    except Exception as exc:
        current_app.logger.exception("/api/hashrate failed: %s", exc)
        return jsonify({"time": [], "hashrate": [], "latest": "N/A", "latest_raw": None})


@api.route("/api/price")
def api_price():
    try:
        data = snapshot()
        latest = data["btc_price"] if data["btc_price"] else "N/A"
        return jsonify({
            "time": data["time_labels"],
            "price": data["price_history"],
            "latest": latest,
            "price_usd": latest,
        })
    except Exception as exc:
        current_app.logger.exception("/api/price failed: %s", exc)
        return jsonify({"time": [], "price": [], "latest": "N/A", "price_usd": "N/A"})


@api.route("/api/network")
def api_network():
    try:
        data = snapshot()
        return jsonify({
            "hashrate": format_hashrate(data["hashrate"]),
            "hashrate_raw": data["hashrate"],
            "nodes": data["node_count"] if data["node_count"] else "N/A",
        })
    except Exception as exc:
        current_app.logger.exception("/api/network failed: %s", exc)
        return jsonify({"hashrate": "N/A", "hashrate_raw": None, "nodes": "N/A"})


@api.route("/api/etf")
def api_etf():
    try:
        return jsonify(get_etf_flow(_settings()))
    except Exception as exc:
        current_app.logger.exception("/api/etf failed: %s", exc)
        return jsonify({
            "latest_net_flow_usd": "N/A",
            "flow_history": [],
            "status": "neutral",
            "source": "fallback",
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
        })


@api.route("/api/supply-ownership")
def api_supply_ownership():
    try:
        return jsonify(get_btc_supply_ownership(_settings()))
    except Exception as exc:
        current_app.logger.exception("/api/supply-ownership failed: %s", exc)
        return jsonify({
            "max_supply_btc": 21_000_000,
            "circulating_supply_btc": "N/A",
            "known_btc": "N/A",
            "unknown_btc": "N/A",
            "ownership": [],
            "top_holders": [],
            "source": "fallback",
            "note": "Bitcoin addresses are pseudonymous, so owner attribution is estimated.",
        })


@api.route("/api/metrics")
def api_metrics():
    data = snapshot()
    return jsonify({
        "btc_price": data["btc_price"] if data["btc_price"] else "N/A",
        "hashrate": format_hashrate(data["hashrate"]),
        "hashrate_raw": data["hashrate"],
        "nodes": data["node_count"] if data["node_count"] else "N/A",
        "time": data["time_labels"][-1] if data["time_labels"] else None,
    })


@api.route("/api/alert")
def api_alert():
    try:
        settings = _settings()
        data = snapshot()
        alerts = build_alerts(
            data["fee_data"],
            data["price_history"],
            fee_spike_threshold=settings.fee_spike_threshold,
            price_breakout_lookback=settings.price_breakout_lookback,
            hashrate=data["hashrate"],
        )
        alert_text = " | ".join(alert["message"] for alert in alerts) if alerts else None
        return jsonify({"alert": alert_text, "alerts": alerts})
    except Exception as exc:
        current_app.logger.exception("/api/alert failed: %s", exc)
        return jsonify({"alert": None, "alerts": []})


@api.route("/api/security")
def api_security():
    try:
        from .security_services import (
            get_51_attack_risk,
            get_double_spend_attempts,
            get_invalid_block_attempts,
            get_reorg_events,
        )
        from .services import rpc_call
        settings = _settings()
        double_spend = get_double_spend_attempts(rpc_call, settings)
        attack_51 = get_51_attack_risk(settings)
        invalid_blocks = get_invalid_block_attempts(rpc_call, settings)
        reorgs = get_reorg_events(rpc_call, settings)
        return jsonify({
            "double_spend": double_spend,
            "attack_51": attack_51,
            "invalid_blocks": invalid_blocks,
            "reorgs": reorgs,
        })
    except Exception as exc:
        current_app.logger.exception("/api/security failed: %s", exc)
        return jsonify({
            "double_spend": {"orphan_count": 0, "orphans": [], "risk_level": "unknown"},
            "attack_51": {"pools": [], "top_pool_share": 0, "risk_level": "unknown"},
            "invalid_blocks": {"invalid_count": 0, "invalid_chains": [], "risk_level": "unknown"},
            "reorgs": {
                "reorg_count": 0,
                "reorgs": [],
                "max_branch_length": 0,
                "risk_level": "unknown",
            },
        })


@api.route("/health")
@api.route("/healthz")
def healthz():
    return jsonify({"status": "ok"})
