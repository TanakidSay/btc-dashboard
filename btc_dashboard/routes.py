from __future__ import annotations

import datetime as dt
from hmac import compare_digest
from zoneinfo import ZoneInfo

import pandas as pd
from flask import Blueprint, Response, current_app, jsonify, render_template, request, url_for

from .config import Settings
from .services import (
    append_metric_point,
    bitcoin_age_days,
    build_alerts,
    estimate_market_cap_usd,
    format_hashrate,
    get_btc_price_result,
    get_btc_supply_ownership,
    get_btc_treasury_holdings,
    get_btc_trend_zone,
    get_current_block_height,
    get_etf_flow,
    get_fear_greed_index,
    get_mvrv_history,
    get_mvrv_summary,
    get_privacy_safe_visitor_key,
    get_recent_whale_transactions,
    get_security_overview,
    get_viewer_analytics,
    get_viewer_stats,
    halving_countdown,
    load_recent_alerts,
    record_alert_history,
    record_analytics_event,
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


def _canonical_origin() -> str:
    settings = _settings()
    host = (settings.canonical_host or request.host).strip().rstrip("/")
    return f"https://{host}"


@api.route("/")
def index():
    record_view(
        _settings(),
        request.headers.get("X-Forwarded-For", request.remote_addr),
        request.headers.get("User-Agent"),
        request.headers.get("Referer"),
        request.path,
        request.headers.get("CF-IPCountry") or request.headers.get("X-Country-Code"),
        request.headers.get("Accept-Language"),
        get_privacy_safe_visitor_key(request),
        request.args.get("utm_source") or request.args.get("source"),
    )
    data = snapshot()
    origin = _canonical_origin()
    return render_template(
        "dashboard.html",
        canonical_url=f"{origin}/",
        og_image_url=f"{origin}{url_for('static', filename='generational-mascot.webp')}",
        table=data["table_html"],
    )


@api.route("/robots.txt")
def robots_txt():
    body = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /api/\n"
        f"Sitemap: {_canonical_origin()}/sitemap.xml\n"
    )
    return Response(body, mimetype="text/plain")


@api.route("/sitemap.xml")
def sitemap_xml():
    today = dt.datetime.now(dt.UTC).date().isoformat()
    body = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>{_canonical_origin()}/</loc>
    <lastmod>{today}</lastmod>
    <changefreq>hourly</changefreq>
    <priority>1.0</priority>
  </url>
</urlset>
"""
    return Response(body, mimetype="application/xml")


@api.route("/private/daily-snapshot")
def private_daily_snapshot_page():
    return render_template("private_daily_snapshot.html")



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


def _latest_cached_block_height(fee_data: pd.DataFrame | None) -> int | None:
    if fee_data is None or fee_data.empty or "height" not in fee_data:
        return None
    heights = pd.to_numeric(fee_data["height"], errors="coerce").dropna()
    if heights.empty:
        return None
    return int(heights.max())


def _json_number(value, *, digits: int | None = None):
    if value in (None, "", "N/A"):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if digits is not None:
        numeric = round(numeric, digits)
    return int(numeric) if numeric.is_integer() else numeric


def _json_text(value):
    if value in (None, "", "N/A"):
        return None
    text = str(value).strip()
    return text or None


def _daily_snapshot_authorized(settings: Settings) -> bool:
    expected = settings.btcwindow_private_api_key or ""
    provided = request.headers.get("X-BTCWINDOW-KEY", "")
    return bool(expected and provided and compare_digest(provided, expected))


def _is_us_etf_market_open(now: dt.datetime | None = None) -> bool:
    current = now or dt.datetime.now(dt.UTC)
    eastern = current.astimezone(ZoneInfo("America/New_York"))
    if eastern.weekday() >= 5:
        return False
    market_open = eastern.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = eastern.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= eastern < market_close


def _fee_level(value) -> str | None:
    fee = _json_number(value)
    if fee is None:
        return None
    if fee <= 2:
        return "Very Low"
    if fee <= 5:
        return "Low"
    if fee <= 15:
        return "Medium"
    if fee <= 30:
        return "High"
    return "Extreme"


def _risk_label(value) -> str | None:
    text = _json_text(value)
    return text.title() if text else None


def _network_health(risk_level: str | None) -> str | None:
    normalized = (risk_level or "").lower()
    if normalized == "low":
        return "Healthy"
    if normalized == "medium":
        return "Watch"
    if normalized == "high":
        return "Risk Elevated"
    if normalized == "critical":
        return "Critical"
    return None


def _safe_call(default, callback):
    try:
        return callback()
    except Exception as exc:
        current_app.logger.warning("daily snapshot field unavailable: %s", exc)
        return default


def _latest_fee_recommendation(fee_data):
    if fee_data is None or fee_data.empty or "sat_per_vbyte" not in fee_data:
        return _empty_fee_response()
    fees = fee_data.tail(_settings().max_chart_rows)["sat_per_vbyte"].tolist()
    return _fee_recommendation_from_history(fees)


def _daily_snapshot_alerts(fear_greed, mvrv, fees, etf, market_open: bool) -> list[str]:
    alerts: list[str] = []
    fg_classification = _json_text(fear_greed.get("classification"))
    fg_value = _json_number(fear_greed.get("value"))
    if fg_classification and "Fear" in fg_classification:
        alerts.append(f"Fear remains in {fg_classification} zone")
    elif fg_classification and "Greed" in fg_classification:
        alerts.append(f"Greed remains in {fg_classification} zone")
    elif fg_value is not None:
        alerts.append("Fear & Greed is neutral")

    mvrv_zone_value = _json_text(mvrv.get("zone"))
    if mvrv_zone_value:
        alerts.append(f"MVRV remains in {mvrv_zone_value.lower()} territory")

    fee_level = _json_text(fees.get("level"))
    if fee_level:
        alerts.append(f"Fees remain {fee_level.lower()}")

    if market_open:
        etf_status = _json_text(etf.get("status"))
        if etf_status:
            alerts.append(f"ETF flow is showing {etf_status.lower()}")
    return alerts[:5]


def _build_snapshot_text(snapshot_payload: dict) -> str:
    lines = ["⚡ Daily Bitcoin Check", ""]
    price = snapshot_payload.get("btc_price")
    if price is not None:
        lines.append(f"₿ BTC: ${price:,.0f}")
    fear_greed = snapshot_payload.get("fear_greed") or {}
    if fear_greed.get("value") is not None:
        label = fear_greed.get("classification") or "sentiment"
        lines.append(f"😱 Fear & Greed: {fear_greed['value']} ({label})")
    mvrv = snapshot_payload.get("mvrv") or {}
    if mvrv.get("value") is not None:
        zone = mvrv.get("zone") or "cycle signal"
        lines.append(f"📊 MVRV: {mvrv['value']} ({zone})")
    fees = snapshot_payload.get("fees") or {}
    if fees.get("level") is not None:
        lines.append(f"💸 Fees: {fees['level']}")
    etf = snapshot_payload.get("etf") or {}
    if etf.get("market_open") and etf.get("flow_usd_m") is not None:
        lines.append(f"🏦 ETF Flow: ${etf['flow_usd_m']:,.2f}M {etf.get('status') or ''}".strip())

    lines.extend([
        "",
        _daily_snapshot_takeaway(snapshot_payload),
        "",
        "Built for Generational Wealth.",
        "",
        "btcwindow.uk",
        "",
        "#Bitcoin #BTC #BTCWindow",
    ])
    return "\n".join(lines)


def _daily_snapshot_takeaway(snapshot_payload: dict) -> str:
    fear_value = _json_number((snapshot_payload.get("fear_greed") or {}).get("value"))
    mvrv_zone_value = _json_text((snapshot_payload.get("mvrv") or {}).get("zone"))
    fee_level = _json_text((snapshot_payload.get("fees") or {}).get("level"))
    if fear_value is not None and fear_value <= 25 and mvrv_zone_value == "Accumulation":
        return "The timeline feels bearish. The data does not."
    if fee_level in {"Very Low", "Low"}:
        return "Bitcoin network conditions remain easy to use today."
    return "Bitcoin market data is mixed, but the signal is worth watching."


def _build_daily_snapshot(settings: Settings) -> dict:
    data = snapshot()
    fee_data = data.get("fee_data")
    block_height = get_current_block_height(settings, _latest_cached_block_height(fee_data))
    fees = _latest_fee_recommendation(fee_data)
    security = _safe_call({}, lambda: get_security_overview(settings))
    attack_51 = security.get("attack_51") or {}
    fear_greed = _safe_call({}, lambda: get_fear_greed_index(settings))
    mvrv = _safe_call({}, lambda: get_mvrv_summary(settings))
    etf_payload = _safe_call({}, lambda: get_etf_flow(settings))
    treasury = _safe_call({}, lambda: get_btc_treasury_holdings(settings))
    top_holder = (treasury.get("top_holders") or [{}])[0] if isinstance(treasury, dict) else {}
    halving = halving_countdown(block_height)
    market_open = _is_us_etf_market_open()
    hashrate_value = _json_number(data.get("hashrate"))
    etf_flow_usd = _json_number(etf_payload.get("latest_net_flow_usd"))

    payload = {
        "date": dt.datetime.now(dt.UTC).date().isoformat(),
        "btc_price": _json_number(data.get("btc_price"), digits=2),
        "price_change_24h_pct": _json_number(data.get("btc_change_24h_percent"), digits=2),
        "fear_greed": {
            "value": _json_number(fear_greed.get("value")),
            "classification": _json_text(fear_greed.get("classification")),
        },
        "mvrv": {
            "value": _json_number(mvrv.get("value"), digits=2),
            "zone": _json_text(mvrv.get("zone")),
        },
        "network": {
            "hashrate_ehs": _json_number(
                (hashrate_value / 1_000_000) if hashrate_value is not None else None,
                digits=2,
            ),
            "nodes": _json_number(data.get("node_count")),
            "health": _network_health(_json_text(attack_51.get("risk_level"))),
            "attack_risk_percent": _json_number(attack_51.get("top_pool_share"), digits=2),
            "attack_risk_level": _risk_label(attack_51.get("risk_level")),
        },
        "fees": {
            "next_block_sat_vb": _json_number(fees.get("fastestFee"), digits=1),
            "thirty_min_sat_vb": _json_number(fees.get("halfHourFee"), digits=1),
            "one_hour_sat_vb": _json_number(fees.get("hourFee"), digits=1),
            "level": _fee_level(fees.get("fastestFee")),
        },
        "etf": {
            "flow_usd_m": _json_number(
                (etf_flow_usd / 1_000_000) if etf_flow_usd is not None else None,
                digits=2,
            ),
            "status": _risk_label(etf_payload.get("trend")),
            "latest_date": _json_text(etf_payload.get("latest_date")),
            "market_open": market_open,
        },
        "lightning": {
            "nodes": None,
            "channels": None,
            "capacity_btc": None,
            "capacity_usd_m": None,
        },
        "ownership": {
            "treasury_btc": _json_number(treasury.get("total_btc_held")),
            "top_holder": _json_text(top_holder.get("name")),
            "top_holder_btc": _json_number(top_holder.get("btc_held")),
        },
        "blockchain": {
            "latest_block": _json_number(block_height),
            "avg_block_time_min": None,
            "difficulty_change_estimate_pct": None,
            "retarget_blocks_left": (
                int(2016 - (block_height % 2016)) if isinstance(block_height, int) else None
            ),
            "next_halving_block": halving.get("next_halving_block"),
            "blocks_remaining": halving.get("blocks_remaining"),
            "halving_eta_days": halving.get("halving_eta_days"),
        },
    }
    payload["alerts"] = _daily_snapshot_alerts(
        payload["fear_greed"],
        payload["mvrv"],
        payload["fees"],
        payload["etf"],
        market_open,
    )
    payload["snapshot_text"] = _build_snapshot_text(payload)
    return payload


@api.route("/api/private/daily-snapshot")
def api_daily_snapshot():
    settings = _settings()
    if not _daily_snapshot_authorized(settings):
        return jsonify({"error": "Unauthorized"}), 401
    return jsonify(_build_daily_snapshot(settings))


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


@api.route("/api/btc-trend-zone")
def api_btc_trend_zone():
    timeframe = request.args.get("tf", "1d")
    try:
        return jsonify(get_btc_trend_zone(_settings(), timeframe))
    except Exception as exc:
        current_app.logger.exception(
            "/api/btc-trend-zone returning fallback timeframe=%s error=%s",
            timeframe,
            exc,
        )
        normalized = str(timeframe or "1d").lower()
        if normalized not in {"1h", "4h", "1d", "1w"}:
            normalized = "1d"
        return jsonify({
            "timeframe": normalized.upper(),
            "signal": "Unavailable",
            "zone": "unknown",
            "confidence": 0,
            "latest_price": None,
            "ema12": None,
            "ema26": None,
            "data": [],
            "status": "error",
            "source": "fallback",
            "updated_at": None,
            "error": "Trend data temporarily unavailable.",
        })


@api.route("/api/network")
def api_network():
    try:
        settings = _settings()
        data = snapshot()
        current_block_height = get_current_block_height(
            settings,
            _latest_cached_block_height(data.get("fee_data")),
        )
        return jsonify({
            "hashrate": format_hashrate(data["hashrate"]),
            "hashrate_raw": data["hashrate"] if data["hashrate"] is not None else 0,
            "nodes": data["node_count"] if data["node_count"] else "N/A",
            "current_block_height": current_block_height,
            "bitcoin_age_days": bitcoin_age_days(),
            "market_cap_usd": estimate_market_cap_usd(data.get("btc_price"), current_block_height),
            **halving_countdown(current_block_height),
            "updated_at": data.get("metric_timestamps", {}).get("network"),
        })
    except Exception as exc:
        current_app.logger.exception("/api/network failed: %s", exc)
        return jsonify({
            "hashrate": "N/A",
            "hashrate_raw": 0,
            "nodes": "N/A",
            "current_block_height": None,
            "bitcoin_age_days": bitcoin_age_days(),
            "market_cap_usd": None,
            **halving_countdown(None),
            "updated_at": None,
        })


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


@api.route("/api/fear-greed")
def api_fear_greed():
    try:
        return jsonify(get_fear_greed_index(_settings()))
    except Exception as exc:
        current_app.logger.exception("/api/fear-greed failed: %s", exc)
        return jsonify({
            "value": "N/A",
            "classification": "N/A",
            "historical": {},
            "source": "alternative.me",
            "source_label": "Alternative.me",
            "status": "error",
            "updated_at": "",
            "data_timestamp": "",
            "data_note": "Fear & Greed data is unavailable.",
            "error": str(exc),
        })


def _record_dashboard_event(event_name: str) -> bool:
    return record_analytics_event(
        _settings(),
        event_name,
        request.headers.get("X-Forwarded-For", request.remote_addr),
        request.headers.get("User-Agent"),
        request.headers.get("Referer"),
        request.headers.get("CF-IPCountry") or request.headers.get("X-Country-Code"),
        request.headers.get("Accept-Language"),
        get_privacy_safe_visitor_key(request),
    )


@api.route("/api/mvrv")
def api_mvrv():
    try:
        _record_dashboard_event("mvrv_card_view")
        return jsonify(get_mvrv_summary(_settings()))
    except Exception as exc:
        current_app.logger.exception("/api/mvrv failed: %s", exc)
        return jsonify({
            "value": "N/A",
            "zone": "N/A",
            "description": "MVRV data is temporarily unavailable.",
            "source": "CoinMetrics",
            "updated_at": None,
            "status": "error",
        })


@api.route("/api/mvrv/history")
def api_mvrv_history():
    try:
        return jsonify(get_mvrv_history(_settings()))
    except Exception as exc:
        current_app.logger.exception("/api/mvrv/history failed: %s", exc)
        return jsonify({
            "source": "CoinMetrics",
            "data": [],
            "status": "error",
        })


@api.route("/api/analytics/event", methods=["POST"])
def api_analytics_event():
    payload = request.get_json(silent=True) or {}
    event_name = str(payload.get("event") or "")
    recorded = _record_dashboard_event(event_name)
    status = 200 if recorded else 400
    return jsonify({"ok": recorded}), status


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
            "unique_today": 0,
            "unique_7d": 0,
            "returning_visitors": 0,
            "returning_rate": "0.0%",
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
        recent_alerts = record_alert_history(settings, alerts)
        alert_text = " | ".join(alert["message"] for alert in alerts) if alerts else None
        return jsonify({"alert": alert_text, "alerts": alerts, "recent_alerts": recent_alerts})
    except Exception as exc:
        current_app.logger.exception("/api/alert failed: %s", exc)
        try:
            recent_alerts = load_recent_alerts(_settings())
        except Exception:
            recent_alerts = []
        return jsonify({"alert": None, "alerts": [], "recent_alerts": recent_alerts})


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
