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


def test_frontend_replaces_price_chart_with_btc_trend() -> None:
    dashboard_js = (ROOT / "btc_dashboard/static/dashboard.js").read_text(encoding="utf-8")
    dashboard_html = (ROOT / "btc_dashboard/templates/dashboard.html").read_text(
        encoding="utf-8",
    )

    assert "BTC Trend" in dashboard_html
    assert "btcTrendSignal" in dashboard_html
    assert "btcTrendConfidence" in dashboard_html
    assert "btcTrendTimeframe" in dashboard_html
    assert "btcTrendFallback" in dashboard_html
    assert 'data-timeframe="1h"' in dashboard_html
    assert 'data-timeframe="4h"' in dashboard_html
    assert 'data-timeframe="1d"' in dashboard_html
    assert 'data-timeframe="1w"' in dashboard_html
    assert "/api/btc-trend-zone?tf=" in dashboard_js
    assert "btcTrendTimeframe = \"1d\"" in dashboard_js
    assert "btcTrendChartData" in dashboard_js
    assert "btcTrendRibbonRows" in dashboard_js
    assert 'label: "Bullish EMA ribbon"' in dashboard_js
    assert 'label: "Bearish EMA ribbon"' in dashboard_js
    assert 'fill: "-1"' in dashboard_js
    assert "skipLegend: true" in dashboard_js
    assert "skipTooltip: true" in dashboard_js
    assert "chartData.datasets?.[item.datasetIndex]" in dashboard_js
    assert "btcTrendDatasetFromTooltipItem(item)" in dashboard_js
    assert "TradingView" not in dashboard_html
    assert "tradingview" not in dashboard_js.lower()
    assert "v='20260701-3'" in dashboard_html


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
    assert "etfFlow7d" in dashboard_html
    assert "etfFlow30d" in dashboard_html
    assert "etfFlowStreak" in dashboard_html
    assert "ETF Trend" not in dashboard_html
    assert "etfData.source_label" in dashboard_js
    assert "etfData.data_note" in dashboard_js
    assert "etfData.is_fallback || etfData.is_stale" in dashboard_js
    assert "renderEtfTrendSummary(etfData)" in dashboard_js
    assert "formatSignedCompactUsd" in dashboard_js
    assert "🟢" in dashboard_js
    assert "🔴" in dashboard_js
    assert "⚪ No Streak" in dashboard_js


def test_frontend_institutional_insight_uses_etf_trend() -> None:
    dashboard_js = (ROOT / "btc_dashboard/static/dashboard.js").read_text(encoding="utf-8")

    assert "const etfTrend = etfData?.trend ?? etfData?.status;" in dashboard_js
    assert 'if (etfTrend === "inflow" && priceRising)' in dashboard_js
    assert 'if (etfTrend === "outflow" && priceFalling)' in dashboard_js
    assert "ETF outflow is visible" in dashboard_js


def test_frontend_renders_treasury_holders_without_confidence_label() -> None:
    dashboard_js = (ROOT / "btc_dashboard/static/dashboard.js").read_text(encoding="utf-8")

    assert "holder.confidence" not in dashboard_js
    assert "holder.source_label" not in dashboard_js


def test_frontend_renders_treasury_source_and_last_checked() -> None:
    dashboard_js = (ROOT / "btc_dashboard/static/dashboard.js").read_text(encoding="utf-8")
    dashboard_html = (ROOT / "btc_dashboard/templates/dashboard.html").read_text(
        encoding="utf-8",
    )

    assert "treasurySource" in dashboard_html
    assert "treasuryData.source_label" in dashboard_js
    assert "Last checked:" in dashboard_js


def test_frontend_renders_lightweight_fear_greed_card() -> None:
    dashboard_js = (ROOT / "btc_dashboard/static/dashboard.js").read_text(encoding="utf-8")
    dashboard_html = (ROOT / "btc_dashboard/templates/dashboard.html").read_text(
        encoding="utf-8",
    )

    assert "Fear &amp; Greed" in dashboard_html
    assert "fearGreedValue" in dashboard_html
    assert "fearGreedMarker" in dashboard_html
    assert "fearGreedYesterday" in dashboard_html
    assert 'fetchJson("/api/fear-greed")' in dashboard_js
    assert "renderFearGreed" in dashboard_js
    assert "historical.last_week" in dashboard_js
    assert 'startRefreshJob("fear-greed", refreshFearGreed, 60 * 60 * 1000)' in dashboard_js
