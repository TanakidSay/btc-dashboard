from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_frontend_has_five_second_price_card_polling() -> None:
    dashboard_js = (ROOT / "btc_dashboard/static/dashboard.js").read_text(encoding="utf-8")

    assert 'fetchJson("/api/price")' in dashboard_js
    assert 'startRefreshJob("btc-price-card", refreshBtcPriceCard, 5000)' in dashboard_js
    assert 'startRefreshJob("btc-price-chart", refreshPriceChart, 60000)' in dashboard_js
    assert "document.addEventListener(\"DOMContentLoaded\"" in dashboard_js
    assert "refreshBtcPriceMetrics" not in dashboard_js


def test_frontend_renders_24h_usd_and_percent_change() -> None:
    dashboard_js = (ROOT / "btc_dashboard/static/dashboard.js").read_text(encoding="utf-8")
    dashboard_html = (ROOT / "btc_dashboard/templates/dashboard.html").read_text(
        encoding="utf-8",
    )

    assert "data.change_24h_usd" in dashboard_js
    assert "data.change_24h_percent" in dashboard_js
    assert "formatSignedUsd(changeUsd)" in dashboard_js
    assert "formatSignedPercent(changePercent)" in dashboard_js
    assert "text-green-400" in dashboard_js
    assert "text-red-400" in dashboard_js
    assert "text-gray-500" in dashboard_js
    assert "24h Change:" in dashboard_html


def test_frontend_prevents_duplicate_price_polling_intervals() -> None:
    dashboard_js = (ROOT / "btc_dashboard/static/dashboard.js").read_text(encoding="utf-8")

    assert "const refreshJobs = new Map();" in dashboard_js
    assert "if (refreshJobs.has(name))" in dashboard_js
    assert "previous run still active" in dashboard_js


def test_frontend_renders_etf_source_label_and_note() -> None:
    dashboard_js = (ROOT / "btc_dashboard/static/dashboard.js").read_text(encoding="utf-8")
    dashboard_html = (ROOT / "btc_dashboard/templates/dashboard.html").read_text(
        encoding="utf-8",
    )

    assert "etfFlowSource" in dashboard_html
    assert "etfFlowNote" in dashboard_html
    assert "etfData.source_label" in dashboard_js
    assert "etfData.data_note" in dashboard_js
    assert "etfData.is_fallback || etfData.is_stale" in dashboard_js
