from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pandas as pd
import requests

from btc_dashboard.config import Settings
from btc_dashboard.services import (
    BTC_PRICE_TTL_SECONDS,
    FEAR_GREED_TTL_SECONDS,
    FEE_MEMPOOL_TTL_SECONDS,
    HASHRATE_TTL_SECONDS,
    NODE_COUNT_TTL_SECONDS,
    SECURITY_TTL_SECONDS,
    TREASURY_TTL_SECONDS,
    _etf_date_is_recent,
    _extract_globalcoinguide_date,
    _extract_millions_flow,
    _extract_walletpilot_date,
    _normalize_etf_payload,
    _parse_bitbo_etf_rows,
    _parse_farside_etf_rows_from_text,
    _parse_farside_latest_rows,
    _parse_walletpilot_embedded_flow_rows,
    build_alerts,
    clear_cache,
    fee_spike_alert,
    format_hashrate,
    get_btc_price,
    get_btc_price_result,
    get_btc_supply_ownership,
    get_btc_treasury_holdings,
    get_etf_flow,
    get_fear_greed_index,
    get_fee_data,
    get_hashrate,
    get_hashrate_chart_points,
    get_hashrate_result,
    get_node_count,
    get_node_count_result,
    get_recent_whale_transactions,
    get_security_overview,
    get_viewer_analytics,
    get_viewer_stats,
    increment_total_views,
    load_recent_alerts,
    load_total_views,
    price_breakout_alert,
    record_alert_history,
    record_view,
    save_total_views,
    update_manual_etf_flow_file,
    whale_transaction_alert,
)


def test_format_hashrate_handles_missing_value() -> None:
    assert format_hashrate(None) == "N/A"


def test_format_hashrate_scales_units() -> None:
    assert format_hashrate(1_500) == "1.50 PH/s"
    assert format_hashrate(2_000_000) == "2.00 EH/s"


def test_price_breakout_alert_detects_new_high() -> None:
    prices = [100.0, 101.0, 99.0, 102.0]

    assert price_breakout_alert(prices, lookback=3) == (
        "Price Breakout: BTC broke above $101.00 to $102.00"
    )


def test_build_alerts_detects_rising_fee_spike() -> None:
    df = pd.DataFrame({"height": [100, 101], "sat_per_vbyte": [4.5, 6.0]})

    alerts = build_alerts(
        df,
        prices=[],
        fee_spike_threshold=5,
        price_breakout_lookback=10,
    )

    assert alerts == [
        {
            "type": "fee_spike",
            "severity": "high",
            "message": "Fee Spike: 6.00 sat/vB crossed above 5.00",
            "height": "101",
            "fee": "6.00",
            "threshold": "5.00",
        }
    ]


def test_alert_history_records_recent_alerts_without_duplicates(tmp_path) -> None:
    settings = _settings(tmp_path)
    alerts = [
        {
            "type": "fee_spike",
            "severity": "high",
            "status": "red",
            "message": "Fee Spike: 10 sat/vB",
            "action": "Wait if not urgent",
            "height": "100",
        }
    ]

    first = record_alert_history(settings, alerts)
    second = record_alert_history(settings, alerts)

    assert len(first) == 1
    assert len(second) == 1
    assert load_recent_alerts(settings)[0]["message"] == "Fee Spike: 10 sat/vB"


def test_whale_transaction_alert_detects_large_mempool_transaction() -> None:
    alert = whale_transaction_alert(
        [
            {"txid": "a" * 64, "value_btc": 25},
            {"txid": "b" * 64, "value_btc": 250.5},
        ],
        threshold_btc=100,
    )

    assert alert is not None
    assert alert["type"] == "whale_transaction"
    assert alert["severity"] == "high"
    assert alert["status"] == "red"
    assert alert["value_btc"] == "250.50000000"


def test_build_alerts_includes_whale_transaction_alert() -> None:
    alerts = build_alerts(
        None,
        prices=[],
        fee_spike_threshold=5,
        price_breakout_lookback=10,
        whale_transactions=[{"txid": "abc", "value_btc": 120}],
        whale_alert_threshold_btc=100,
    )

    assert alerts == [
        {
            "type": "whale_transaction",
            "severity": "medium",
            "status": "yellow",
            "message": "Whale Transaction: 120.00 BTC moved in mempool",
            "action": "Review transaction abc",
            "txid": "abc",
            "value_btc": "120.00000000",
            "threshold_btc": "100.00",
        }
    ]


def test_get_recent_whale_transactions_sorts_public_mempool_values(monkeypatch, tmp_path) -> None:
    def fake_get(url: str, **kwargs) -> FakeResponse:
        return FakeResponse([
            {"txid": "small", "value": 5_000_000_000, "fee": 1000, "vsize": 250},
            {"txid": "large", "value": 15_000_000_000, "fee": 2000, "vsize": 300},
        ])

    monkeypatch.setattr("btc_dashboard.services.session.get", fake_get)

    transactions = get_recent_whale_transactions(_settings(tmp_path))

    assert [tx["txid"] for tx in transactions] == ["large", "small"]
    assert transactions[0]["value_btc"] == 150


def test_fee_spike_alert_requires_threshold_crossing() -> None:
    already_high = pd.DataFrame({"height": [100, 101], "sat_per_vbyte": [5.5, 6.0]})

    assert fee_spike_alert(already_high, threshold=5) is None


def test_fee_spike_alert_detects_threshold_crossing() -> None:
    df = pd.DataFrame({"height": [100, 101], "sat_per_vbyte": [4.9, 5.1]})

    assert fee_spike_alert(df, threshold=5) == {
        "type": "fee_spike",
        "severity": "high",
        "message": "Fee Spike: 5.10 sat/vB crossed above 5.00",
        "height": "101",
        "fee": "5.10",
        "threshold": "5.00",
    }


class FakeResponse:
    def __init__(self, payload=None, text: str = "", status_code: int = 200) -> None:
        self.payload = payload
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


def _settings(tmp_path, **overrides) -> Settings:
    clear_cache()
    values = {
        "secret_key": "test",
        "fee_csv_path": tmp_path / "fees.csv",
        "viewer_stats_path": tmp_path / "viewer_stats.json",
        "viewer_analytics_path": tmp_path / "viewer_analytics.json",
        "view_counter_path": tmp_path / "view_counter.json",
        "alerts_history_path": tmp_path / "alerts_history.json",
        "etf_flow_path": tmp_path / "etf_flows.json",
        "btc_price_baseline_path": tmp_path / "btc_price_baseline.json",
        "etf_flow_ttl_seconds": 12 * 60 * 60,
        "view_counter_initial_total": 0,
        "viewer_stats_initial_unique": 0,
        "start_worker": False,
        "bitcoin_rpc_password": "test",
        "cache_ttl_seconds": 30,
        "node_block_count": 2,
    }
    values.update(overrides)
    return Settings(
        **values,
    )


def _write_price_baseline(path, session_date: str, price: float) -> None:
    path.write_text(
        json.dumps({
            "session_date": session_date,
            "baseline_price_usd": price,
            "locked_at": f"{session_date}T00:00:00Z",
            "timezone": "Asia/Bangkok",
            "baseline_hour": 7,
        }),
        encoding="utf-8",
    )


def test_record_view_updates_total_and_unique_counts(tmp_path) -> None:
    settings = _settings(tmp_path)

    first = record_view(settings, "127.0.0.1", "BrowserA")
    second = record_view(settings, "127.0.0.1", "BrowserA")
    third = record_view(settings, "127.0.0.2", "BrowserB")

    assert first["total_views"] == 1
    assert second["total_views"] == 1
    assert second["suppressed_views"] == 1
    assert third["total_views"] == 2
    assert third["unique_visitors"] == 2
    assert third["last_viewed_at"] is not None


def test_record_view_updates_aggregate_viewer_analytics(tmp_path) -> None:
    settings = _settings(tmp_path)

    record_view(
        settings,
        "203.0.113.9",
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) AppleWebKit/605.1.15 Safari/604.1",
        "https://x.com/BitcoinWindow/status/123",
        "/",
        "th",
    )
    record_view(
        settings,
        "203.0.113.10",
        "Googlebot/2.1",
        None,
        "/api/price",
        None,
    )

    analytics = get_viewer_analytics(settings)

    assert analytics["total_events"] == 2
    assert analytics["sources"] == {"x": 1, "direct": 1}
    assert analytics["referrers"]["x.com"] == 1
    assert analytics["devices"] == {"mobile": 1, "bot": 1}
    assert analytics["browsers"] == {"safari": 1, "bot": 1}
    assert analytics["countries"] == {"TH": 1, "unknown": 1}
    assert analytics["paths"] == {"/": 1, "/api/price": 1}
    assert "IP addresses are not stored" in analytics["privacy"]
    assert "203.0.113" not in json.dumps(analytics)


def test_record_view_groups_youtube_and_tiktok_referrers(tmp_path) -> None:
    settings = _settings(tmp_path)

    record_view(settings, "203.0.113.21", "Chrome", "https://youtu.be/abc123", "/", "TH")
    record_view(
        settings,
        "203.0.113.22",
        "Chrome",
        "https://m.youtube.com/watch?v=abc123",
        "/",
        "TH",
    )
    record_view(
        settings,
        "203.0.113.23",
        "Chrome",
        "https://vm.tiktok.com/ZMabc123/",
        "/",
        "TH",
    )
    record_view(settings, "203.0.113.24", "Chrome", "https://notyoutube.com/", "/", "TH")

    analytics = get_viewer_analytics(settings)

    assert analytics["sources"] == {"youtube": 2, "tiktok": 1, "other": 1}
    assert analytics["referrers"]["youtu.be"] == 1
    assert analytics["referrers"]["m.youtube.com"] == 1
    assert analytics["referrers"]["vm.tiktok.com"] == 1


def test_viewer_analytics_suppresses_duplicate_events_within_dedupe_window(
    monkeypatch,
    tmp_path,
) -> None:
    clock = {"now": 1_000.0}
    monkeypatch.setattr("btc_dashboard.services.time.time", lambda: clock["now"])
    settings = _settings(tmp_path)

    first = record_view(settings, "203.0.113.9", "Chrome", None, "/", None)
    second = record_view(settings, "203.0.113.9", "Chrome", None, "/", None)
    analytics = get_viewer_analytics(settings)

    assert first["total_views"] == 1
    assert second["total_views"] == 1
    assert second["suppressed_views"] == 1
    assert analytics["total_events"] == 1
    assert analytics["suppressed_events"] == 1
    assert analytics["dedupe_window_seconds"] == 60
    assert analytics["sources"] == {"direct": 1}

    clock["now"] += 61
    record_view(settings, "203.0.113.9", "Chrome", None, "/", None)

    analytics = get_viewer_analytics(settings)
    stats = get_viewer_stats(settings)
    assert stats["total_views"] == 2
    assert stats["suppressed_views"] == 1
    assert analytics["total_events"] == 2
    assert analytics["suppressed_events"] == 1


def test_get_viewer_stats_returns_zeroes_when_file_is_missing(tmp_path) -> None:
    settings = _settings(tmp_path)

    assert get_viewer_stats(settings) == {
        "total_views": 0,
        "unique_visitors": 0,
        "last_viewed_at": None,
        "suppressed_views": 0,
        "dedupe_window_seconds": 60,
    }
    assert settings.view_counter_path.exists()


def test_view_counter_initial_total_seeds_missing_counter_file(tmp_path) -> None:
    settings = _settings(tmp_path, view_counter_initial_total=174)

    assert get_viewer_stats(settings)["total_views"] == 174
    assert record_view(settings, "127.0.0.1", "BrowserA")["total_views"] == 175
    assert load_total_views(settings.view_counter_path) == 175


def test_view_counter_initial_total_does_not_raise_existing_counter(tmp_path) -> None:
    settings = _settings(tmp_path, view_counter_initial_total=182)
    save_total_views(settings.view_counter_path, 174)

    assert get_viewer_stats(settings)["total_views"] == 174
    assert record_view(settings, "127.0.0.1", "BrowserA")["total_views"] == 175
    assert load_total_views(settings.view_counter_path) == 175


def test_view_counter_initial_total_does_not_lower_existing_counter(tmp_path) -> None:
    settings = _settings(tmp_path, view_counter_initial_total=182)
    save_total_views(settings.view_counter_path, 200)

    assert get_viewer_stats(settings)["total_views"] == 200
    assert record_view(settings, "127.0.0.1", "BrowserA")["total_views"] == 201


def test_viewer_stats_initial_unique_raises_lower_existing_unique_count(tmp_path) -> None:
    settings = _settings(tmp_path)

    for index in range(7):
        record_view(settings, f"127.0.0.{index}", "BrowserA")

    seeded_settings = _settings(
        tmp_path,
        viewer_stats_initial_unique=105,
        viewer_stats_path=settings.viewer_stats_path,
        view_counter_path=settings.view_counter_path,
    )
    stats = get_viewer_stats(seeded_settings)

    assert stats["unique_visitors"] == 105
    assert record_view(seeded_settings, "127.0.0.200", "BrowserA")["unique_visitors"] == 106


def test_viewer_stats_initial_unique_does_not_lower_existing_unique_count(tmp_path) -> None:
    settings = _settings(tmp_path)

    for index in range(3):
        record_view(settings, f"127.0.0.{index}", "BrowserA")

    seeded_settings = _settings(
        tmp_path,
        viewer_stats_initial_unique=2,
        viewer_stats_path=settings.viewer_stats_path,
        view_counter_path=settings.view_counter_path,
    )

    assert get_viewer_stats(seeded_settings)["unique_visitors"] == 3


def test_total_view_counter_persists_after_restart_simulation(tmp_path) -> None:
    counter_path = tmp_path / "view_counter.json"

    assert increment_total_views(counter_path) == 1
    assert increment_total_views(counter_path) == 2
    assert load_total_views(counter_path) == 2


def test_total_view_counter_recreates_missing_file(tmp_path) -> None:
    counter_path = tmp_path / "missing_view_counter.json"

    assert load_total_views(counter_path) == 0
    assert counter_path.exists()


def test_total_view_counter_handles_corrupted_file(tmp_path) -> None:
    counter_path = tmp_path / "view_counter.json"
    counter_path.write_text("{not-json", encoding="utf-8")

    assert load_total_views(counter_path) == 0
    assert load_total_views(counter_path) == 0


def test_save_total_views_sanitizes_negative_values(tmp_path) -> None:
    counter_path = tmp_path / "view_counter.json"

    save_total_views(counter_path, -5)

    assert load_total_views(counter_path) == 0


def test_get_btc_price_uses_binance(monkeypatch, tmp_path) -> None:
    def fake_get(url: str, **kwargs) -> FakeResponse:
        if "binance.com" in url:
            return FakeResponse({
                "lastPrice": "98765.43",
                "priceChange": "1234.56",
                "priceChangePercent": "1.57",
            })
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr("btc_dashboard.services.session.get", fake_get)

    assert get_btc_price(_settings(tmp_path)) == 98765.43


def test_get_btc_price_result_uses_daily_baseline_change(monkeypatch, tmp_path) -> None:
    def fake_get(url: str, **kwargs) -> FakeResponse:
        if "binance.com" in url:
            return FakeResponse({
                "lastPrice": "98765.43",
                "priceChange": "0",
                "priceChangePercent": "0",
            })
        raise AssertionError(f"unexpected url: {url}")

    baseline_path = tmp_path / "btc_price_baseline.json"
    _write_price_baseline(baseline_path, "2026-05-10", 97_500.00)
    monkeypatch.setattr("btc_dashboard.services.session.get", fake_get)
    monkeypatch.setattr(
        "btc_dashboard.services._utc_now_dt",
        lambda: datetime(2026, 5, 10, 8, tzinfo=UTC),
    )

    result = get_btc_price_result(_settings(tmp_path, btc_price_baseline_path=baseline_path))

    assert result is not None
    assert result.value == 98765.43
    assert result.source == "binance"
    assert result.change_24h_usd == 1265.43
    assert result.change_24h_percent == 1.2979
    assert result.is_cached is False


def test_get_btc_price_result_creates_daily_baseline_when_missing(
    monkeypatch,
    tmp_path,
) -> None:
    def fake_get(url: str, **kwargs) -> FakeResponse:
        if "binance.com" in url:
            return FakeResponse({
                "lastPrice": "98765.43",
                "priceChange": "1000",
                "priceChangePercent": "1",
            })
        raise AssertionError(f"unexpected url: {url}")

    baseline_path = tmp_path / "btc_price_baseline.json"
    monkeypatch.setattr("btc_dashboard.services.session.get", fake_get)
    monkeypatch.setattr(
        "btc_dashboard.services._utc_now_dt",
        lambda: datetime(2026, 5, 10, 0, 1, tzinfo=UTC),
    )
    monkeypatch.setattr("btc_dashboard.services._utc_now_iso", lambda: "2026-05-10T00:01:00Z")

    result = get_btc_price_result(_settings(tmp_path, btc_price_baseline_path=baseline_path))
    payload = json.loads(baseline_path.read_text(encoding="utf-8"))

    assert result is not None
    assert result.change_24h_usd == 0
    assert result.change_24h_percent == 0
    assert payload["session_date"] == "2026-05-10"
    assert payload["baseline_price_usd"] == 98765.43


def test_get_btc_price_result_rolls_baseline_at_seven_bangkok(
    monkeypatch,
    tmp_path,
) -> None:
    def fake_get(url: str, **kwargs) -> FakeResponse:
        if "binance.com" in url:
            return FakeResponse({
                "lastPrice": "100000",
                "priceChange": "1000",
                "priceChangePercent": "1",
            })
        raise AssertionError(f"unexpected url: {url}")

    baseline_path = tmp_path / "btc_price_baseline.json"
    _write_price_baseline(baseline_path, "2026-05-09", 95_000)
    monkeypatch.setattr("btc_dashboard.services.session.get", fake_get)
    monkeypatch.setattr(
        "btc_dashboard.services._utc_now_dt",
        lambda: datetime(2026, 5, 10, 0, 0, tzinfo=UTC),
    )

    result = get_btc_price_result(_settings(tmp_path, btc_price_baseline_path=baseline_path))
    payload = json.loads(baseline_path.read_text(encoding="utf-8"))

    assert result is not None
    assert result.change_24h_usd == 0
    assert payload["session_date"] == "2026-05-10"
    assert payload["baseline_price_usd"] == 100000


def test_get_btc_price_returns_none_when_all_sources_fail(monkeypatch, tmp_path) -> None:
    def fake_get(url: str, **kwargs) -> FakeResponse:
        return FakeResponse(status_code=503)

    monkeypatch.setattr("btc_dashboard.services.session.get", fake_get)

    assert get_btc_price(_settings(tmp_path)) is None


def test_get_btc_price_falls_back_to_coingecko(monkeypatch, tmp_path) -> None:
    def fake_get(url: str, **kwargs) -> FakeResponse:
        if "binance.com" in url:
            return FakeResponse(status_code=503)
        if "coingecko.com" in url:
            return FakeResponse({"bitcoin": {"usd": 87654.32, "usd_24h_change": 2.5}})
        raise AssertionError(f"unexpected url: {url}")

    baseline_path = tmp_path / "btc_price_baseline.json"
    _write_price_baseline(baseline_path, "2026-05-10", 85_516.41)
    monkeypatch.setattr("btc_dashboard.services.session.get", fake_get)
    monkeypatch.setattr(
        "btc_dashboard.services._utc_now_dt",
        lambda: datetime(2026, 5, 10, 8, tzinfo=UTC),
    )

    result = get_btc_price_result(_settings(tmp_path, btc_price_baseline_path=baseline_path))

    assert result is not None
    assert result.value == 87654.32
    assert result.source == "coingecko"
    assert result.change_24h_percent == 2.5


def test_btc_price_cache_uses_five_second_cadence(monkeypatch, tmp_path) -> None:
    assert BTC_PRICE_TTL_SECONDS == 5
    clock = {"now": 0.0}
    calls = {"count": 0}

    def fake_get(url: str, **kwargs) -> FakeResponse:
        calls["count"] += 1
        return FakeResponse({
            "lastPrice": str(90_000 + calls["count"]),
            "priceChange": "100",
            "priceChangePercent": "0.1",
        })

    monkeypatch.setattr("btc_dashboard.services.time.monotonic", lambda: clock["now"])
    monkeypatch.setattr("btc_dashboard.services.session.get", fake_get)
    settings = _settings(tmp_path)

    assert get_btc_price(settings) == 90_001
    assert get_btc_price(settings) == 90_001
    assert calls["count"] == 1

    clock["now"] = 5.1

    assert get_btc_price(settings) == 90_002
    assert calls["count"] == 2


def test_btc_price_preserves_valid_cached_value_when_binance_fails(monkeypatch, tmp_path) -> None:
    clock = {"now": 0.0}
    calls = {"count": 0}

    def fake_get(url: str, **kwargs) -> FakeResponse:
        calls["count"] += 1
        if calls["count"] == 1:
            return FakeResponse({
                "lastPrice": "90000",
                "priceChange": "1200",
                "priceChangePercent": "1.35",
            })
        return FakeResponse(status_code=503)

    baseline_path = tmp_path / "btc_price_baseline.json"
    _write_price_baseline(baseline_path, "2026-05-10", 88_800)
    monkeypatch.setattr("btc_dashboard.services.time.monotonic", lambda: clock["now"])
    monkeypatch.setattr("btc_dashboard.services.session.get", fake_get)
    monkeypatch.setattr(
        "btc_dashboard.services._utc_now_dt",
        lambda: datetime(2026, 5, 10, 8, tzinfo=UTC),
    )
    settings = _settings(tmp_path, btc_price_baseline_path=baseline_path)

    first = get_btc_price_result(settings)
    clock["now"] = BTC_PRICE_TTL_SECONDS + 0.1
    second = get_btc_price_result(settings)

    assert first is not None
    assert second is not None
    assert second.value == 90000
    assert second.change_24h_usd == 1200
    assert second.change_24h_percent == 1.3514
    assert second.is_cached is True


def test_get_hashrate_uses_node_first(monkeypatch, tmp_path) -> None:
    post_calls = []

    def fake_post(url: str, **kwargs) -> FakeResponse:
        post_calls.append(kwargs["json"]["method"])
        return FakeResponse({"result": 650_000_000_000_000_000_000})

    monkeypatch.setattr("btc_dashboard.services.session.post", fake_post)

    assert get_hashrate(_settings(tmp_path)) == 650_000_000.0
    assert post_calls == ["getnetworkhashps"]


def test_get_hashrate_falls_back_to_mempool_when_node_fails(monkeypatch, tmp_path) -> None:
    def fake_post(url: str, **kwargs) -> FakeResponse:
        return FakeResponse({"error": {"message": "node unavailable"}})

    def fake_get(url: str, **kwargs) -> FakeResponse:
        if "mempool.space" in url:
            return FakeResponse({"currentHashrate": 650_000_000_000_000_000_000})
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr("btc_dashboard.services.session.post", fake_post)
    monkeypatch.setattr("btc_dashboard.services.session.get", fake_get)

    assert get_hashrate(_settings(tmp_path)) == 650_000_000.0
    result = get_hashrate_result(_settings(tmp_path))
    assert result is not None
    assert result.source == "mempool.space"


def test_hashrate_cache_ttl_is_ten_minutes(monkeypatch, tmp_path) -> None:
    assert HASHRATE_TTL_SECONDS == 10 * 60
    clock = {"now": 0.0}
    calls = {"count": 0}

    def fake_post(url: str, **kwargs) -> FakeResponse:
        calls["count"] += 1
        return FakeResponse({"result": calls["count"] * 1_000_000_000_000})

    monkeypatch.setattr("btc_dashboard.services.time.monotonic", lambda: clock["now"])
    monkeypatch.setattr("btc_dashboard.services.session.post", fake_post)
    settings = _settings(tmp_path)

    assert get_hashrate(settings) == 1.0
    clock["now"] = HASHRATE_TTL_SECONDS - 1
    assert get_hashrate(settings) == 1.0
    assert calls["count"] == 1

    clock["now"] = HASHRATE_TTL_SECONDS + 0.1
    assert get_hashrate(settings) == 2.0
    assert calls["count"] == 2


def test_get_node_count_uses_bitnodes_reachable_nodes(monkeypatch, tmp_path) -> None:
    def fake_get(url: str, **kwargs) -> FakeResponse:
        if "bitnodes.io/api/v1/snapshots/latest" in url:
            return FakeResponse({"total_nodes": 17414})
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr("btc_dashboard.services.session.get", fake_get)

    assert get_node_count(_settings(tmp_path)) == 17414


def test_get_node_count_returns_fallback_on_node_failure(monkeypatch, tmp_path) -> None:
    def fake_get(url: str, **kwargs) -> FakeResponse:
        return FakeResponse(status_code=503)

    monkeypatch.setattr("btc_dashboard.services.session.get", fake_get)

    assert get_node_count(_settings(tmp_path)) == "N/A"


def test_get_node_count_falls_back_to_mempool_lightning(monkeypatch, tmp_path) -> None:
    def fake_get(url: str, **kwargs) -> FakeResponse:
        if "bitnodes.io/api/v1/snapshots/latest" in url:
            return FakeResponse(status_code=503)
        if "mempool.space/api/v1/lightning/statistics/latest" in url:
            return FakeResponse({"latest": {"node_count": 17425}})
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr("btc_dashboard.services.session.get", fake_get)

    assert get_node_count(_settings(tmp_path)) == 17425
    result = get_node_count_result(_settings(tmp_path))
    assert result is not None
    assert result.source == "mempool.space"


def test_node_count_cache_ttl_is_thirty_minutes(monkeypatch, tmp_path) -> None:
    assert NODE_COUNT_TTL_SECONDS == 30 * 60
    clock = {"now": 0.0}
    calls = {"count": 0}

    def fake_get(url: str, **kwargs) -> FakeResponse:
        calls["count"] += 1
        return FakeResponse({"total_nodes": 17_000 + calls["count"]})

    monkeypatch.setattr("btc_dashboard.services.time.monotonic", lambda: clock["now"])
    monkeypatch.setattr("btc_dashboard.services.session.get", fake_get)
    settings = _settings(tmp_path)

    assert get_node_count(settings) == 17_001
    clock["now"] = NODE_COUNT_TTL_SECONDS - 1
    assert get_node_count(settings) == 17_001
    assert calls["count"] == 1

    clock["now"] = NODE_COUNT_TTL_SECONDS + 0.1
    assert get_node_count(settings) == 17_002
    assert calls["count"] == 2


def test_get_fee_data_uses_node_blocks_first(monkeypatch, tmp_path) -> None:
    blocks = {
        "hash-2": {
            "height": 2,
            "tx": [{"vout": [{"value": 3.126}]}, {"vout": []}],
            "weight": 4000,
            "previousblockhash": "hash-1",
        },
        "hash-1": {
            "height": 1,
            "tx": [{"vout": [{"value": 3.127}]}, {"vout": []}, {"vout": []}],
            "weight": 8000,
        },
    }

    def fake_post(url: str, **kwargs) -> FakeResponse:
        method = kwargs["json"]["method"]
        params = kwargs["json"]["params"]
        if method == "getbestblockhash":
            return FakeResponse({"result": "hash-2"})
        if method == "getblock":
            return FakeResponse({"result": blocks[params[0]]})
        raise AssertionError(f"unexpected method: {method}")

    monkeypatch.setattr("btc_dashboard.services.session.post", fake_post)

    fee_data = get_fee_data(_settings(tmp_path))

    assert fee_data["height"].tolist() == [1, 2]
    assert fee_data["tx_count"].tolist() == [3, 2]
    assert fee_data["sat_per_vbyte"].round(2).tolist() == [100.0, 100.0]


def test_get_fee_data_falls_back_to_mempool_blocks(monkeypatch, tmp_path) -> None:
    def fake_post(url: str, **kwargs) -> FakeResponse:
        return FakeResponse({"error": {"message": "node unavailable"}})

    def fake_get(url: str, **kwargs) -> FakeResponse:
        return FakeResponse(
            [
                {
                    "height": 101,
                    "tx_count": 10,
                    "weight": 4000,
                    "extras": {"totalFees": 2000, "virtualSize": 1000},
                }
            ]
        )

    monkeypatch.setattr("btc_dashboard.services.session.post", fake_post)
    monkeypatch.setattr("btc_dashboard.services.session.get", fake_get)

    fee_data = get_fee_data(_settings(tmp_path))

    assert fee_data.to_dict("records") == [
        {
            "height": 101,
            "tx_count": 10,
            "total_fee_btc": 0.00002,
            "sat_per_vbyte": 2.0,
        }
    ]


def test_fee_data_cache_uses_thirty_second_cadence(monkeypatch, tmp_path) -> None:
    assert FEE_MEMPOOL_TTL_SECONDS == 30
    clock = {"now": 0.0}
    calls = {"best_hash": 0}

    def fake_post(url: str, **kwargs) -> FakeResponse:
        method = kwargs["json"]["method"]
        if method == "getbestblockhash":
            calls["best_hash"] += 1
            return FakeResponse({"result": f"hash-{calls['best_hash']}"})
        if method == "getblock":
            height = calls["best_hash"]
            return FakeResponse({
                "result": {
                    "height": height,
                    "tx": [{"vout": [{"value": 3.126}]}, {"vout": []}],
                    "weight": 4000,
                },
            })
        raise AssertionError(f"unexpected method: {method}")

    monkeypatch.setattr("btc_dashboard.services.time.monotonic", lambda: clock["now"])
    monkeypatch.setattr("btc_dashboard.services.session.post", fake_post)
    settings = _settings(tmp_path, node_block_count=1)

    assert get_fee_data(settings)["height"].tolist() == [1]
    clock["now"] = FEE_MEMPOOL_TTL_SECONDS - 1
    assert get_fee_data(settings)["height"].tolist() == [1]
    assert calls["best_hash"] == 1

    clock["now"] = FEE_MEMPOOL_TTL_SECONDS + 0.1
    assert get_fee_data(settings)["height"].tolist() == [2]
    assert calls["best_hash"] == 2


def test_get_btc_treasury_holdings_reads_coingecko_company_treasury(
    monkeypatch,
    tmp_path,
) -> None:
    calls = {"count": 0}

    def fake_get(url: str, **kwargs) -> FakeResponse:
        calls["count"] += 1
        assert url == "https://api.coingecko.com/api/v3/companies/public_treasury/bitcoin"
        return FakeResponse({
            "total_holdings": 123456.78,
            "market_cap_dominance": 0.59,
            "companies": [
                {
                    "name": "Strategy",
                    "symbol": "MSTR",
                    "total_holdings": 555555.0,
                    "percentage_of_total_supply": 2.64,
                }
            ],
        })

    monkeypatch.setattr("btc_dashboard.services.session.get", fake_get)

    payload = get_btc_treasury_holdings(_settings(tmp_path, cache_ttl_seconds=0))

    assert calls["count"] == 1
    assert payload["status"] == "ok"
    assert payload["source"] == "coingecko-company-treasury"
    assert payload["source_label"] == "CoinGecko | Live"
    assert payload["updated_at"] is not None
    assert payload["error"] == ""
    assert payload["top_holders"][0]["name"] == "Strategy"


def test_get_btc_treasury_holdings_preserves_last_successful_value(monkeypatch, tmp_path) -> None:
    responses = iter(
        [
            FakeResponse({
                "total_holdings": 111111.0,
                "market_cap_dominance": 0.42,
                "companies": [
                    {
                        "name": "Strategy",
                        "symbol": "MSTR",
                        "total_holdings": 555555.0,
                        "percentage_of_total_supply": 2.64,
                    }
                ],
            }),
            FakeResponse(status_code=503),
            FakeResponse(status_code=503),
            FakeResponse(status_code=503),
            FakeResponse(status_code=503),
            FakeResponse(status_code=503),
            FakeResponse(status_code=503),
        ]
    )

    def fake_get(url: str, **kwargs) -> FakeResponse:
        return next(responses)

    monkeypatch.setattr("btc_dashboard.services.session.get", fake_get)
    monkeypatch.setattr("btc_dashboard.services._persistent_cache_is_fresh", lambda *args: False)
    settings = _settings(tmp_path, cache_ttl_seconds=0)

    first = get_btc_treasury_holdings(settings)
    second = get_btc_treasury_holdings(settings)

    assert first["status"] == "ok"
    assert second["status"] == "stale"
    assert second["total_btc_held"] == first["total_btc_held"]
    assert second["treasury_dominance_percent"] == first["treasury_dominance_percent"]
    assert second["top_holders"] == first["top_holders"]
    assert second["updated_at"] == first["updated_at"]
    assert second["source_label"] == "CoinGecko | Stale"
    assert second["data_note"] == "Treasury data is cached because the live source is unavailable."
    assert "coingecko-company-treasury" in second["error"]


def test_treasury_ttl_is_twenty_four_hours() -> None:
    assert TREASURY_TTL_SECONDS == 24 * 60 * 60


def test_fear_greed_ttl_is_twenty_four_hours() -> None:
    assert FEAR_GREED_TTL_SECONDS == 24 * 60 * 60


def test_get_fear_greed_index_parses_alternative_me(monkeypatch, tmp_path) -> None:
    def fake_get(url: str, **kwargs) -> FakeResponse:
        assert "alternative.me/fng" in url
        return FakeResponse({
            "data": [
                {
                    "value": str(72 - index),
                    "value_classification": "Greed" if index < 10 else "Fear",
                    "timestamp": str(1779417600 - (index * 86400)),
                }
                for index in range(30)
            ]
        })

    monkeypatch.setattr("btc_dashboard.services.session.get", fake_get)

    payload = get_fear_greed_index(_settings(tmp_path, cache_ttl_seconds=0))

    assert payload["value"] == 72
    assert payload["classification"] == "Greed"
    assert payload["source_label"] == "Alternative.me"
    assert payload["status"] == "ok"
    assert payload["data_timestamp"].endswith("Z")
    assert payload["updated_at"]
    assert payload["historical"]["yesterday"]["value"] == 71
    assert payload["historical"]["last_week"]["value"] == 65
    assert payload["historical"]["last_month"]["value"] == 43


def test_get_fear_greed_index_returns_safe_fallback(monkeypatch, tmp_path) -> None:
    def fake_get(url: str, **kwargs) -> FakeResponse:
        return FakeResponse(status_code=503)

    monkeypatch.setattr("btc_dashboard.services.session.get", fake_get)

    payload = get_fear_greed_index(_settings(tmp_path, cache_ttl_seconds=0))

    assert payload["value"] == "N/A"
    assert payload["classification"] == "N/A"
    assert payload["source_label"] == "Alternative.me"
    assert payload["status"] == "error"
    assert "HTTP 503" in payload["error"]


def test_get_btc_treasury_holdings_returns_stable_error_payload(monkeypatch, tmp_path) -> None:
    def fake_get(url: str, **kwargs) -> FakeResponse:
        return FakeResponse(status_code=503)

    monkeypatch.setattr("btc_dashboard.services.session.get", fake_get)

    payload = get_btc_treasury_holdings(_settings(tmp_path, cache_ttl_seconds=0))

    assert payload["total_btc_held"] == 1_271_929
    assert payload["treasury_dominance_percent"] == 6.06
    assert payload["top_holders"][0]["name"] == "Strategy"
    assert len(payload["top_holders"]) == 10
    assert [holder["name"] for holder in payload["top_holders"]] == [
        "Strategy",
        "XXI",
        "Metaplanet",
        "MARA Holdings",
        "Bitcoin Standard Treasury Company",
        "Galaxy Digital Holdings Ltd",
        "Bullish",
        "SpaceX",
        "Riot Platforms",
        "Coinbase Global",
    ]
    assert [holder["btc_held"] for holder in payload["top_holders"]] == sorted(
        [holder["btc_held"] for holder in payload["top_holders"]],
        reverse=True,
    )
    assert all("confidence" not in holder for holder in payload["top_holders"])
    assert payload["source"] == "coingecko-treasury-estimate"
    assert payload["status"] == "fallback"
    assert payload["updated_at"]
    assert "coingecko-company-treasury: HTTP 503" in payload["error"]
    assert "checked public estimate" in payload["data_note"]


def test_ownership_endpoint_payload_calculates_scarcity_metrics(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "btc_dashboard.services.get_btc_treasury_holdings",
        lambda settings: {"total_btc_held": 500_000, "top_holders": [], "status": "ok"},
    )
    monkeypatch.setattr(
        "btc_dashboard.services._get_circulating_supply",
        lambda settings: 19_800_000,
    )

    payload = get_btc_supply_ownership(_settings(tmp_path, cache_ttl_seconds=0))

    assert payload["circulating_supply"] == 19_800_000
    assert payload["max_supply"] == 21_000_000
    assert payload["remaining_to_mine"] == 1_200_000
    assert payload["percent_mined"] == 94.29
    satoshi = next(
        row for row in payload["categories"] if row["name"] == "Satoshi Nakamoto estimate"
    )
    assert satoshi["percent"] == 5.56
    assert satoshi["source_type"] == "Research estimate"
    assert satoshi["confidence"] == "research estimate"
    assert satoshi["estimated"] is True
    assert payload["chart_categories"]
    assert all(row["name"] != "Retail / unattributed supply" for row in payload["chart_categories"])
    assert all("Only 1,200,000 BTC left to mine" not in insight for insight in payload["insights"])
    assert any(insight.startswith("Mining scarcity:") for insight in payload["insights"])


def test_ownership_fallback_categories_are_rounded_estimates(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(
        "btc_dashboard.services.get_btc_treasury_holdings",
        lambda settings: {"total_btc_held": "N/A", "top_holders": [], "status": "stale"},
    )
    monkeypatch.setattr(
        "btc_dashboard.services._get_circulating_supply",
        lambda settings: 19_800_000,
    )

    payload = get_btc_supply_ownership(_settings(tmp_path, cache_ttl_seconds=0))
    categories = {row["name"]: row for row in payload["categories"]}

    expected_estimates = {
        "ETFs / funds": 1_400_000,
        "Governments / seized BTC": 530_000,
        "Exchanges / custodians": 2_200_000,
        "Miners": 1_800_000,
    }
    for name, btc in expected_estimates.items():
        assert categories[name]["btc"] == btc
        assert categories[name]["estimated"] is True
        assert categories[name]["approximate"] is True
        assert categories[name]["confidence"] == "approximate"
        assert categories[name]["display_btc"].startswith("~")

    assert categories["Public companies / treasuries"]["display_btc"] == "Limited visibility"
    assert categories["Lost coins estimate"]["btc_range"] == {"low": 3_000_000, "high": 4_000_000}
    assert categories["Lost coins estimate"]["display_btc"] == "~3,000,000 - ~4,000,000 BTC"


def test_ownership_cached_value_preserved_when_live_source_fails(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path, cache_ttl_seconds=0)
    monkeypatch.setattr(
        "btc_dashboard.services.get_btc_treasury_holdings",
        lambda settings: {"total_btc_held": 500_000, "top_holders": [], "status": "ok"},
    )
    monkeypatch.setattr(
        "btc_dashboard.services._get_circulating_supply",
        lambda settings: 19_800_000,
    )
    first = get_btc_supply_ownership(settings)

    monkeypatch.setattr("btc_dashboard.services._persistent_cache_is_fresh", lambda *args: False)
    monkeypatch.setattr(
        "btc_dashboard.services.get_btc_treasury_holdings",
        lambda settings: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    second = get_btc_supply_ownership(settings)

    assert second["status"] == "stale"
    assert second["categories"] == first["categories"]
    assert second["circulating_supply"] == first["circulating_supply"]


def test_ownership_data_does_not_refresh_on_frequent_calls(monkeypatch, tmp_path) -> None:
    calls = {"treasury": 0, "supply": 0}

    def fake_treasury(settings):
        calls["treasury"] += 1
        return {"total_btc_held": 500_000, "top_holders": [], "status": "ok"}

    def fake_supply(settings):
        calls["supply"] += 1
        return 19_800_000

    monkeypatch.setattr("btc_dashboard.services.get_btc_treasury_holdings", fake_treasury)
    monkeypatch.setattr("btc_dashboard.services._get_circulating_supply", fake_supply)
    settings = _settings(tmp_path)

    first = get_btc_supply_ownership(settings)
    second = get_btc_supply_ownership(settings)

    assert first["categories"] == second["categories"]
    assert calls == {"treasury": 1, "supply": 1}


def test_static_ownership_estimates_do_not_call_external_apis(monkeypatch, tmp_path) -> None:
    def fail_external(*args, **kwargs):
        raise AssertionError("static fallback estimates should not call external APIs")

    monkeypatch.setattr("btc_dashboard.services.session.get", fail_external)
    monkeypatch.setattr("btc_dashboard.services.session.post", fail_external)
    monkeypatch.setattr(
        "btc_dashboard.services.get_btc_treasury_holdings",
        lambda settings: {"total_btc_held": "N/A", "top_holders": [], "status": "stale"},
    )
    monkeypatch.setattr(
        "btc_dashboard.services._get_circulating_supply",
        lambda settings: 19_800_000,
    )

    payload = get_btc_supply_ownership(_settings(tmp_path))
    categories = {row["name"]: row for row in payload["categories"]}

    assert categories["ETFs / funds"]["display_btc"] == "~1,400,000 BTC"
    assert categories["Governments / seized BTC"]["display_btc"] == "~530,000 BTC"


def test_ownership_estimates_are_labeled_transparently(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "btc_dashboard.services.get_btc_treasury_holdings",
        lambda settings: {"total_btc_held": None, "top_holders": [], "status": "stale"},
    )
    monkeypatch.setattr(
        "btc_dashboard.services._get_circulating_supply",
        lambda settings: 19_800_000,
    )

    payload = get_btc_supply_ownership(_settings(tmp_path, cache_ttl_seconds=0))
    categories = {row["name"]: row for row in payload["categories"]}

    assert categories["Satoshi Nakamoto estimate"]["estimated"] is True
    assert categories["Satoshi Nakamoto estimate"]["confidence"] == "research estimate"
    assert categories["Lost coins estimate"]["display_btc"].startswith("~")
    assert categories["Lost coins estimate"]["source_type"] == "Research estimate"
    assert categories["Public companies / treasuries"]["display_btc"] == "Limited visibility"
    assert categories["Public companies / treasuries"]["estimated"] is True
    assert "pseudonymous" in payload["note"]


def test_security_cache_ttl_is_thirty_minutes(monkeypatch, tmp_path) -> None:
    assert SECURITY_TTL_SECONDS == 30 * 60
    now = {"value": datetime(2026, 5, 6, tzinfo=UTC)}
    calls = {"attack": 0}

    monkeypatch.setattr("btc_dashboard.services._utc_now_dt", lambda: now["value"])
    monkeypatch.setattr(
        "btc_dashboard.security_services.get_double_spend_attempts",
        lambda rpc_call_fn, settings: {"orphan_count": 0, "orphans": [], "active_height": 1},
    )
    monkeypatch.setattr(
        "btc_dashboard.security_services.get_invalid_block_attempts",
        lambda rpc_call_fn, settings: {"invalid_count": 0, "invalid_chains": []},
    )
    monkeypatch.setattr(
        "btc_dashboard.security_services.get_reorg_events",
        lambda rpc_call_fn, settings: {"reorg_count": 0, "reorgs": []},
    )

    def fake_attack(settings):
        calls["attack"] += 1
        return {"pools": [], "top_pool_share": calls["attack"], "risk_level": "low"}

    monkeypatch.setattr("btc_dashboard.security_services.get_51_attack_risk", fake_attack)
    settings = _settings(tmp_path)

    assert get_security_overview(settings)["attack_51"]["top_pool_share"] == 1
    now["value"] = now["value"] + timedelta(seconds=SECURITY_TTL_SECONDS - 1)
    assert get_security_overview(settings)["attack_51"]["top_pool_share"] == 1
    assert calls["attack"] == 1

    now["value"] = now["value"] + timedelta(seconds=2)
    assert get_security_overview(settings)["attack_51"]["top_pool_share"] == 2
    assert calls["attack"] == 2


def test_parse_farside_etf_rows_from_text_handles_pipe_rows() -> None:
    rows = _parse_farside_etf_rows_from_text(
        "06 Apr 2026 | 181.9 | 147.3 | 3.8 | 118.8 | 471.4\n"
        "07 Apr 2026 | (10.0) | 0.0 | 0.0 | 0.0 | (10.0)\n"
        "| 22 May 2026 | - | (36.3) | 0.0 | 0.0 | (36.3) |\n"
    )

    assert rows == [
        {"date": "06 Apr 2026", "net_flow_usd": 471_400_000.0, "close_price": None},
        {"date": "07 Apr 2026", "net_flow_usd": -10_000_000.0, "close_price": None},
        {"date": "22 May 2026", "net_flow_usd": -36_300_000.0, "close_price": None},
    ]


def test_parse_farside_latest_rows_uses_first_total_value_and_skips_pending_rows() -> None:
    rows = _parse_farside_latest_rows(
        "01 May 2026 284.4 213.4 27.3 88.5 0.0 629.8 "
        "11 May 2026 (7.4) (3.6) 0.0 0.0 7.3 0.0 0.0 4.6 0.0 26.3 0.0 0.0 27.2 "
        "04 May 2026 - - - - - - 0.0 Total 65,502"
    )

    assert rows == [
        {"date": "01 May 2026", "net_flow_usd": 284_400_000.0, "close_price": 0},
        {"date": "11 May 2026", "net_flow_usd": -7_400_000.0, "close_price": 0},
    ]


def test_parse_bitbo_etf_rows_uses_totals_column() -> None:
    rows = _parse_bitbo_etf_rows(
        "Date IBIT FBTC GBTC Totals "
        "May 07, 2026 -99.0 -130.3 -17.5 -261.2 "
        "May 06, 2026 122.0 -38.8 -18.8 26.6"
    )

    assert rows == [
        {"date": "May 07, 2026", "net_flow_usd": -261_200_000.0, "close_price": None},
        {"date": "May 06, 2026", "net_flow_usd": 26_600_000.0, "close_price": None},
    ]


def test_extract_etf_scrape_values_from_public_pages() -> None:
    walletpilot_text = (
        "Holdings as of market close: 03 May 2026 "
        "1-Day Net Flows +$411M 7-Day Net Flows +$573M"
    )
    globalcoinguide_text = (
        "Today's Net Flow +$342.0M Last updated: May 03 "
        "Weekly Net Flow +$2.10B 7-day cumulative"
    )

    assert _extract_walletpilot_date(walletpilot_text) == "03 May 2026"
    assert _extract_globalcoinguide_date(globalcoinguide_text) == "May 03"
    assert _extract_millions_flow(walletpilot_text, "1-Day Net Flows") == 411_000_000.0
    assert _extract_millions_flow(walletpilot_text, "7-Day Net Flows") == 573_000_000.0
    assert _extract_millions_flow(globalcoinguide_text, "Today's Net Flow") == 342_000_000.0
    assert _extract_millions_flow(globalcoinguide_text, "Weekly Net Flow") == 2_100_000_000.0


def test_parse_walletpilot_embedded_flow_rows_groups_latest_etf_flows() -> None:
    rows = _parse_walletpilot_embedded_flow_rows(
        'etfs:[{ticker:"IBIT",netFlows1d:-103.64,netFlows7d:-1075.32,'
        'lastFlowDate:"2026-05-21T00:00:00.000Z"},'
        '{ticker:"ARKB",netFlows1d:2.83,netFlows7d:-159.29,'
        'lastFlowDate:"2026-05-21T00:00:00.000Z"},'
        '{ticker:"FBTC",netFlows1d:0,lastFlowDate:"2026-05-20T00:00:00.000Z"}]',
    )

    assert rows == [
        {"date": "2026-05-20", "net_flow_usd": 0.0, "close_price": None},
        {"date": "2026-05-21", "net_flow_usd": -100_810_000.0, "close_price": None},
    ]


def test_etf_date_freshness_rejects_stale_scrape_dates(monkeypatch) -> None:
    monkeypatch.setattr(
        "btc_dashboard.services._utc_now_dt",
        lambda: datetime(2026, 5, 4, tzinfo=UTC),
    )

    assert _etf_date_is_recent("May 03")
    assert _etf_date_is_recent("27 Apr 2026")
    assert not _etf_date_is_recent("26 Apr 2026")
    assert not _etf_date_is_recent("Mar 14")


def test_get_etf_flow_uses_walletpilot_public_fallback(monkeypatch, tmp_path) -> None:
    def fake_get(url: str, **kwargs) -> FakeResponse:
        if "farside.co.uk" in url:
            return FakeResponse(status_code=403)
        if "walletpilot.com/bitcoin-tracker/etfs" in url:
            return FakeResponse(
                text=(
                    "<div>Holdings as of market close: 03 May 2026</div>"
                    "<div>1-Day Net Flows +$411M</div>"
                    "<div>7-Day Net Flows +$573M</div>"
                )
            )
        return FakeResponse(status_code=503)

    monkeypatch.setattr("btc_dashboard.services.session.get", fake_get)
    monkeypatch.setattr(
        "btc_dashboard.services._utc_now_dt",
        lambda: datetime(2026, 5, 4, tzinfo=UTC),
    )

    payload = get_etf_flow(_settings(tmp_path))

    assert payload["source"] == "walletpilot"
    assert payload["latest_date"] == "03 May 2026"
    assert payload["latest_net_flow_usd"] == 411_000_000.0
    assert payload["7d_flow"] == 573_000_000.0
    assert payload["flow_history"][0]["close_price"] == 0


def test_get_etf_flow_uses_walletpilot_embedded_public_fallback(monkeypatch, tmp_path) -> None:
    def fake_get(url: str, **kwargs) -> FakeResponse:
        if "farside.co.uk" in url or "r.jina.ai" in url or "bitbo.io" in url:
            return FakeResponse(status_code=403)
        if "walletpilot.com/bitcoin-tracker/etfs" in url:
            return FakeResponse(
                text=(
                    'etfs:[{ticker:"IBIT",netFlows1d:-103.64,netFlows7d:-1075.32,'
                    'lastFlowDate:"2026-05-21T00:00:00.000Z"},'
                    '{ticker:"ARKB",netFlows1d:2.83,netFlows7d:-159.29,'
                    'lastFlowDate:"2026-05-21T00:00:00.000Z"}]'
                )
            )
        return FakeResponse(status_code=503)

    monkeypatch.setattr("btc_dashboard.services.session.get", fake_get)
    monkeypatch.setattr(
        "btc_dashboard.services._utc_now_dt",
        lambda: datetime(2026, 5, 22, tzinfo=UTC),
    )

    payload = get_etf_flow(_settings(tmp_path))

    assert payload["source"] == "walletpilot"
    assert payload["latest_date"] == "2026-05-21"
    assert payload["latest_net_flow_usd"] == -100_810_000.0


def test_get_etf_flow_uses_globalcoinguide_public_fallback(monkeypatch, tmp_path) -> None:
    def fake_get(url: str, **kwargs) -> FakeResponse:
        if "farside.co.uk" in url or "walletpilot.com/bitcoin-tracker/etfs" in url:
            return FakeResponse(status_code=403)
        if "globalcoinguide.com/research/data/etf-flows" in url:
            return FakeResponse(
                text=(
                    "<div>Today's Net Flow +$342.0M</div>"
                    "<div>Last updated: May 03</div>"
                    "<div>Weekly Net Flow +$2.10B</div>"
                )
            )
        return FakeResponse(status_code=503)

    monkeypatch.setattr("btc_dashboard.services.session.get", fake_get)
    monkeypatch.setattr(
        "btc_dashboard.services._utc_now_dt",
        lambda: datetime(2026, 5, 4, tzinfo=UTC),
    )

    payload = get_etf_flow(_settings(tmp_path))

    assert payload["source"] == "globalcoinguide"
    assert payload["latest_date"] == "May 03"
    assert payload["latest_net_flow_usd"] == 342_000_000.0
    assert payload["7d_flow"] == 2_100_000_000.0


def test_get_etf_flow_uses_bitbo_public_table(monkeypatch, tmp_path) -> None:
    def fake_get(url: str, **kwargs) -> FakeResponse:
        if "farside.co.uk" in url:
            return FakeResponse(status_code=403)
        if "bitbo.io/treasuries/etf-flows" in url:
            return FakeResponse(
                text=(
                    "May 07, 2026 -99.0 -130.3 -17.5 0.0 -12.7 0.0 -0.0 "
                    "0.0 -9.1 0.0 7.5 0.0 0.0 -261.2 "
                    "May 06, 2026 122.0 -38.8 -18.8 0.0 0.0 -25.0 -5.7 "
                    "0.0 0.0 -7.0 0.0 0.0 0.0 26.6"
                )
            )
        return FakeResponse(status_code=503)

    monkeypatch.setattr("btc_dashboard.services.session.get", fake_get)
    monkeypatch.setattr(
        "btc_dashboard.services._utc_now_dt",
        lambda: datetime(2026, 5, 8, tzinfo=UTC),
    )

    payload = get_etf_flow(_settings(tmp_path))

    assert payload["source"] == "bitbo"
    assert payload["source_label"] == "Bitbo"
    assert payload["is_fallback"] is False
    assert payload["is_stale"] is False
    assert payload["latest_date"] == "May 07, 2026"
    assert payload["latest_net_flow_usd"] == -261_200_000.0
    assert len(payload["flow_history"]) == 2


def test_get_etf_flow_uses_farside_reader_when_direct_farside_is_blocked(
    monkeypatch,
    tmp_path,
) -> None:
    def fake_get(url: str, **kwargs) -> FakeResponse:
        if "r.jina.ai" in url:
            return FakeResponse(
                text=(
                    "| 21 May 2026 | (103.7) | 0.0 | 0.0 | 2.8 | (100.9) |\n"
                    "| 22 May 2026 | - | (36.3) | 0.0 | 0.0 | (36.3) |"
                )
            )
        if "farside.co.uk" in url:
            return FakeResponse(status_code=403)
        return FakeResponse(status_code=503)

    monkeypatch.setattr("btc_dashboard.services.session.get", fake_get)
    monkeypatch.setattr(
        "btc_dashboard.services._utc_now_dt",
        lambda: datetime(2026, 5, 23, tzinfo=UTC),
    )

    payload = get_etf_flow(_settings(tmp_path))

    assert payload["source"] == "farside-reader"
    assert payload["source_label"] == "Live"
    assert payload["latest_date"] == "22 May 2026"
    assert payload["latest_net_flow_usd"] == -36_300_000.0
    assert len(payload["flow_history"]) == 2


def test_get_etf_flow_prefers_live_source_over_manual_json(
    monkeypatch,
    tmp_path,
) -> None:
    def fake_get(url: str, **kwargs) -> FakeResponse:
        if "farside.co.uk/btc/" in url:
            return FakeResponse(text="10 May 2026 321.0 0.0 0.0 0.0 321.0")
        return FakeResponse(status_code=503)

    manual_path = tmp_path / "etf_flows.json"
    manual_path.write_text(
        json.dumps({
            "source": "manual",
            "updated_at": "2026-05-10T00:00:00Z",
            "flow_history": [
                {"date": "2026-05-08", "net_flow_usd": -45_000_000},
                {"date": "2026-05-09", "net_flow_usd": 123_000_000},
            ],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr("btc_dashboard.services.session.get", fake_get)
    monkeypatch.setattr(
        "btc_dashboard.services._utc_now_dt",
        lambda: datetime(2026, 5, 10, tzinfo=UTC),
    )

    payload = get_etf_flow(_settings(tmp_path, etf_flow_path=manual_path))

    assert payload["source"] == "farside-latest"
    assert payload["source_label"] == "Live"
    assert payload["is_fallback"] is False
    assert payload["is_stale"] is False
    assert payload["latest_date"] == "10 May 2026"
    assert payload["latest_net_flow_usd"] == 321_000_000.0
    assert payload["data_note"] == "ETF flow history is using live source data."


def test_get_etf_flow_uses_manual_json_when_live_sources_fail(
    monkeypatch,
    tmp_path,
) -> None:
    def fake_get(url: str, **kwargs) -> FakeResponse:
        return FakeResponse(status_code=503)

    manual_path = tmp_path / "etf_flows.json"
    manual_path.write_text(
        json.dumps({
            "source": "manual",
            "updated_at": "2026-05-10T00:00:00Z",
            "flow_history": [
                {"date": "2026-05-08", "net_flow_usd": -45_000_000},
                {"date": "2026-05-09", "net_flow_usd": 123_000_000},
            ],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr("btc_dashboard.services.session.get", fake_get)
    monkeypatch.setattr(
        "btc_dashboard.services._utc_now_dt",
        lambda: datetime(2026, 5, 10, tzinfo=UTC),
    )

    payload = get_etf_flow(_settings(tmp_path, etf_flow_path=manual_path))

    assert payload["source"] == "manual"
    assert payload["source_label"] == "Manual"
    assert payload["is_fallback"] is False
    assert payload["is_stale"] is False
    assert payload["latest_date"] == "2026-05-09"
    assert payload["latest_net_flow_usd"] == 123_000_000.0
    assert payload["7d_flow"] == 78_000_000.0
    assert payload["data_note"] == "ETF flow data loaded from local manual file."


def test_get_etf_flow_uses_manual_json_before_public_fallbacks(
    monkeypatch,
    tmp_path,
) -> None:
    requested_urls = []

    def fake_get(url: str, **kwargs) -> FakeResponse:
        requested_urls.append(url)
        if "farside.co.uk" in url:
            return FakeResponse(status_code=503)
        if "bitbo.io/treasuries/etf-flows" in url:
            return FakeResponse(
                text="Date IBIT FBTC GBTC Totals May 10, 2026 200.0 0.0 0.0 200.0",
            )
        return FakeResponse(status_code=503)

    manual_path = tmp_path / "etf_flows.json"
    manual_path.write_text(
        json.dumps({
            "source": "manual",
            "updated_at": "2026-05-10T00:00:00Z",
            "flow_history": [{"date": "2026-05-09", "net_flow_usd": 123_000_000}],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr("btc_dashboard.services.session.get", fake_get)
    monkeypatch.setattr(
        "btc_dashboard.services._utc_now_dt",
        lambda: datetime(2026, 5, 10, tzinfo=UTC),
    )

    payload = get_etf_flow(_settings(tmp_path, etf_flow_path=manual_path))

    assert payload["source"] == "manual"
    assert payload["latest_net_flow_usd"] == 123_000_000.0
    assert not any("bitbo.io" in url for url in requested_urls)


def test_get_etf_flow_seeds_railway_volume_manual_file(
    monkeypatch,
    tmp_path,
) -> None:
    def fake_get(url: str, **kwargs) -> FakeResponse:
        return FakeResponse(status_code=503)

    bundled_path = tmp_path / "bundled_etf_flows.json"
    bundled_path.write_text(
        json.dumps({
            "source": "manual",
            "updated_at": "2026-05-13T00:00:00Z",
            "flow_history": [{"date": "2026-05-12", "net_flow_usd": -233_200_000}],
        }),
        encoding="utf-8",
    )
    volume_path = tmp_path / "data" / "etf_flows.json"
    monkeypatch.setattr("btc_dashboard.services.BUNDLED_ETF_FLOW_PATH", bundled_path)
    monkeypatch.setattr("btc_dashboard.services._should_sync_manual_etf_file", lambda path: True)
    monkeypatch.setattr("btc_dashboard.services.session.get", fake_get)
    monkeypatch.setattr(
        "btc_dashboard.services._utc_now_dt",
        lambda: datetime(2026, 5, 13, tzinfo=UTC),
    )

    payload = get_etf_flow(_settings(tmp_path, etf_flow_path=volume_path))

    assert volume_path.exists()
    assert payload["source"] == "manual"
    assert payload["latest_date"] == "2026-05-12"
    assert payload["latest_net_flow_usd"] == -233_200_000.0


def test_get_etf_flow_updates_railway_volume_manual_file_when_bundled_is_newer(
    monkeypatch,
    tmp_path,
) -> None:
    def fake_get(url: str, **kwargs) -> FakeResponse:
        return FakeResponse(status_code=503)

    bundled_path = tmp_path / "bundled_etf_flows.json"
    bundled_path.write_text(
        json.dumps({
            "source": "manual",
            "updated_at": "2026-05-15T00:00:00Z",
            "flow_history": [
                {"date": "2026-05-12", "net_flow_usd": -233_200_000},
                {"date": "2026-05-14", "net_flow_usd": 131_300_000},
            ],
        }),
        encoding="utf-8",
    )
    volume_path = tmp_path / "data" / "etf_flows.json"
    volume_path.parent.mkdir(parents=True)
    volume_path.write_text(
        json.dumps({
            "source": "manual",
            "updated_at": "2026-05-13T00:00:00Z",
            "flow_history": [{"date": "2026-05-12", "net_flow_usd": -233_200_000}],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr("btc_dashboard.services.BUNDLED_ETF_FLOW_PATH", bundled_path)
    monkeypatch.setattr("btc_dashboard.services._should_sync_manual_etf_file", lambda path: True)
    monkeypatch.setattr("btc_dashboard.services.session.get", fake_get)
    monkeypatch.setattr(
        "btc_dashboard.services._utc_now_dt",
        lambda: datetime(2026, 5, 15, tzinfo=UTC),
    )

    payload = get_etf_flow(_settings(tmp_path, etf_flow_path=volume_path))

    saved_data = json.loads(volume_path.read_text(encoding="utf-8"))
    assert saved_data["updated_at"] == "2026-05-15T00:00:00Z"
    assert payload["source"] == "manual"
    assert payload["latest_date"] == "2026-05-14"
    assert payload["latest_net_flow_usd"] == 131_300_000.0


def test_update_manual_etf_flow_file_merges_payload_and_clears_cache(
    monkeypatch,
    tmp_path,
) -> None:
    def fake_get(url: str, **kwargs) -> FakeResponse:
        return FakeResponse(status_code=503)

    monkeypatch.setattr("btc_dashboard.services.session.get", fake_get)
    monkeypatch.setattr(
        "btc_dashboard.services._utc_now_dt",
        lambda: datetime(2026, 5, 17, tzinfo=UTC),
    )
    settings = _settings(tmp_path, etf_flow_path=tmp_path / "etf_flows.json")
    initial_payload = {
        "source": "manual",
        "updated_at": "2026-05-15T00:00:00Z",
        "flow_history": [{"date": "2026-05-15", "net_flow_usd": -290_400_000}],
    }
    updated_payload = {
        "source": "manual",
        "updated_at": "2026-05-16T00:00:00Z",
        "flow_history": [{"date": "2026-05-16", "net_flow_usd": 260_000_000}],
    }

    first = update_manual_etf_flow_file(settings, initial_payload)
    cached = get_etf_flow(settings)
    second = update_manual_etf_flow_file(settings, updated_payload)

    saved_data = json.loads(settings.etf_flow_path.read_text(encoding="utf-8"))
    assert first["latest_date"] == "2026-05-15"
    assert cached["latest_date"] == "2026-05-15"
    assert second["latest_date"] == "2026-05-16"
    assert get_etf_flow(settings)["latest_date"] == "2026-05-16"
    assert saved_data["flow_history"] == [
        {"date": "2026-05-15", "net_flow_usd": -290_400_000.0, "close_price": 0},
        {"date": "2026-05-16", "net_flow_usd": 260_000_000.0, "close_price": 0},
    ]


def test_update_manual_etf_flow_file_replaces_existing_date(
    monkeypatch,
    tmp_path,
) -> None:
    def fake_get(url: str, **kwargs) -> FakeResponse:
        return FakeResponse(status_code=503)

    monkeypatch.setattr("btc_dashboard.services.session.get", fake_get)
    monkeypatch.setattr(
        "btc_dashboard.services._utc_now_dt",
        lambda: datetime(2026, 5, 17, tzinfo=UTC),
    )
    settings = _settings(tmp_path, etf_flow_path=tmp_path / "etf_flows.json")
    settings.etf_flow_path.write_text(
        json.dumps({
            "source": "manual",
            "updated_at": "2026-05-15T00:00:00Z",
            "flow_history": [
                {"date": "2026-05-14", "net_flow_usd": 131_300_000},
                {"date": "2026-05-15", "net_flow_usd": -1},
            ],
        }),
        encoding="utf-8",
    )

    payload = update_manual_etf_flow_file(settings, {
        "source": "manual",
        "updated_at": "2026-05-17T00:00:00Z",
        "flow_history": [{"date": "2026-05-15", "net_flow_usd": -290_400_000}],
    })

    saved_data = json.loads(settings.etf_flow_path.read_text(encoding="utf-8"))
    assert payload["latest_date"] == "2026-05-15"
    assert saved_data["flow_history"] == [
        {"date": "2026-05-14", "net_flow_usd": 131_300_000.0, "close_price": 0},
        {"date": "2026-05-15", "net_flow_usd": -290_400_000.0, "close_price": 0},
    ]


def test_get_etf_flow_empty_manual_file_skips_to_farside_without_warning(
    monkeypatch,
    tmp_path,
    caplog,
) -> None:
    def fake_get(url: str, **kwargs) -> FakeResponse:
        if "farside.co.uk/btc/" in url:
            return FakeResponse(text="09 May 2026 123.0 0.0 0.0 0.0 123.0")
        return FakeResponse(status_code=503)

    manual_path = tmp_path / "etf_flows.json"
    manual_path.write_text(
        json.dumps({
            "source": "manual",
            "updated_at": "",
            "flow_history": [],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr("btc_dashboard.services.session.get", fake_get)
    monkeypatch.setattr(
        "btc_dashboard.services._utc_now_dt",
        lambda: datetime(2026, 5, 10, tzinfo=UTC),
    )

    with caplog.at_level("WARNING"):
        payload = get_etf_flow(_settings(tmp_path, etf_flow_path=manual_path))

    assert payload["source"] == "farside-latest"
    assert "manual ETF flow file has no rows" not in caplog.text


def test_get_etf_flow_missing_coinglass_key_is_skipped_cleanly(
    monkeypatch,
    tmp_path,
) -> None:
    requested_urls = []

    def fake_get(url: str, **kwargs) -> FakeResponse:
        requested_urls.append(url)
        if "farside.co.uk" in url:
            return FakeResponse(status_code=403)
        return FakeResponse(status_code=503)

    monkeypatch.setattr("btc_dashboard.services.session.get", fake_get)
    monkeypatch.setattr(
        "btc_dashboard.services._utc_now_dt",
        lambda: datetime(2026, 5, 4, tzinfo=UTC),
    )

    payload = get_etf_flow(_settings(tmp_path))

    assert payload["source"] == "seeded-fallback"
    assert not any("coinglass" in url.lower() for url in requested_urls)


def test_get_etf_flow_ttl_is_clamped_to_one_hour(monkeypatch, tmp_path) -> None:
    requests_made = 0

    def fake_get(url: str, **kwargs) -> FakeResponse:
        nonlocal requests_made
        requests_made += 1
        return FakeResponse(status_code=503)

    now = 1_000.0
    monkeypatch.setattr("btc_dashboard.services.session.get", fake_get)
    monkeypatch.setattr("btc_dashboard.services.time.time", lambda: now)
    monkeypatch.setattr(
        "btc_dashboard.services._utc_now_dt",
        lambda: datetime(2026, 5, 4, tzinfo=UTC),
    )
    settings = _settings(tmp_path, etf_flow_ttl_seconds=5)

    first = get_etf_flow(settings)
    second = get_etf_flow(settings)

    assert first["source"] == "seeded-fallback"
    assert second["source"] == "seeded-fallback"
    assert requests_made > 0
    requests_after_first_refresh = requests_made
    get_etf_flow(settings)
    assert requests_made == requests_after_first_refresh


def test_get_etf_flow_invalid_manual_json_falls_back_safely(
    monkeypatch,
    tmp_path,
) -> None:
    def fake_get(url: str, **kwargs) -> FakeResponse:
        return FakeResponse(status_code=503)

    manual_path = tmp_path / "etf_flows.json"
    manual_path.write_text("{not-json", encoding="utf-8")
    monkeypatch.setattr("btc_dashboard.services.session.get", fake_get)
    monkeypatch.setattr(
        "btc_dashboard.services._utc_now_dt",
        lambda: datetime(2026, 5, 4, tzinfo=UTC),
    )

    payload = get_etf_flow(_settings(tmp_path, etf_flow_path=manual_path))

    assert payload["source"] == "seeded-fallback"
    assert payload["source_label"] == "Fallback estimate"
    assert payload["is_fallback"] is True


def test_get_etf_flow_stale_manual_json_is_marked_stale(
    monkeypatch,
    tmp_path,
) -> None:
    def fake_get(url: str, **kwargs) -> FakeResponse:
        return FakeResponse(status_code=503)

    manual_path = tmp_path / "etf_flows.json"
    manual_path.write_text(
        json.dumps({
            "source": "manual",
            "updated_at": "2026-05-10T00:00:00Z",
            "flow_history": [{"date": "2026-05-01", "net_flow_usd": 123_000_000}],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr("btc_dashboard.services.session.get", fake_get)
    monkeypatch.setattr(
        "btc_dashboard.services._utc_now_dt",
        lambda: datetime(2026, 5, 20, tzinfo=UTC),
    )

    payload = get_etf_flow(_settings(tmp_path, etf_flow_path=manual_path))

    assert payload["source"] == "manual"
    assert payload["source_label"] == "Manual"
    assert payload["is_fallback"] is False
    assert payload["is_stale"] is True
    assert "older than expected" in payload["data_note"]


def test_get_etf_flow_uses_recent_seeded_fallback(monkeypatch, tmp_path) -> None:
    def fake_get(url: str, **kwargs) -> FakeResponse:
        return FakeResponse(status_code=503)

    monkeypatch.setattr("btc_dashboard.services.session.get", fake_get)
    monkeypatch.setattr(
        "btc_dashboard.services._utc_now_dt",
        lambda: datetime(2026, 5, 4, tzinfo=UTC),
    )

    payload = get_etf_flow(_settings(tmp_path))

    assert payload["source"] == "seeded-fallback"
    assert payload["status"] == "stale"
    assert payload["latest_date"] == "04 May 2026"
    assert payload["latest_net_flow_usd"] == 532_300_000.0
    assert payload["7d_flow"] == 987_700_000.0
    assert len(payload["flow_history"]) == 5
    assert payload["is_fallback"] is True
    assert payload["is_stale"] is False
    assert payload["source_label"] == "Fallback estimate"
    assert "fallback estimate" in payload["data_note"]
    assert payload["flow_history"][0]["close_price"] == 0


def test_get_etf_flow_uses_stale_seeded_history_when_live_data_fails(
    monkeypatch,
    tmp_path,
) -> None:
    def fake_get(url: str, **kwargs) -> FakeResponse:
        return FakeResponse(status_code=503)

    monkeypatch.setattr("btc_dashboard.services.session.get", fake_get)
    monkeypatch.setattr(
        "btc_dashboard.services._utc_now_dt",
        lambda: datetime(2026, 5, 20, tzinfo=UTC),
    )

    payload = get_etf_flow(_settings(tmp_path))

    assert payload["source"] == "seeded-fallback"
    assert payload["status"] == "stale"
    assert len(payload["flow_history"]) == 5
    assert payload["is_fallback"] is True
    assert payload["is_stale"] is True
    assert payload["source_label"] == "Fallback estimate"
    assert payload["latest_date"] == "11 May 2026"


def test_live_etf_data_still_uses_freshness_filter(monkeypatch) -> None:
    monkeypatch.setattr(
        "btc_dashboard.services._utc_now_dt",
        lambda: datetime(2026, 5, 20, tzinfo=UTC),
    )

    try:
        _normalize_etf_payload(
            [{"date": "01 May 2026", "net_flow_usd": 118_900_000, "close_price": 0}],
            "live-test",
        )
    except ValueError as exc:
        assert "ETF data is stale" in str(exc)
    else:
        raise AssertionError("stale live ETF data should be rejected")


def test_get_hashrate_chart_points_normalizes_mempool_history(monkeypatch, tmp_path) -> None:
    def fake_get(url: str, **kwargs) -> FakeResponse:
        return FakeResponse({
            "hashrates": [
                {"timestamp": 1_710_000_000, "avgHashrate": 600_000_000_000_000_000_000},
                {"timestamp": 1_710_003_600, "avgHashrate": 650_000_000_000_000_000_000},
            ]
        })

    monkeypatch.setattr("btc_dashboard.services.session.get", fake_get)

    points = get_hashrate_chart_points(_settings(tmp_path))

    assert [point["value"] for point in points] == [600_000_000.0, 650_000_000.0]
    assert points[0]["timestamp"].endswith("Z")


def test_get_etf_flow_uses_farside_latest_text_fallback(monkeypatch, tmp_path) -> None:
    def fake_get(url: str, **kwargs) -> FakeResponse:
        if "farside.co.uk/btc/" in url:
            return FakeResponse(text="03 May 2026 181.9 147.3 3.8 118.8 471.4")
        if "farside.co.uk/bitcoin-etf-flow-all-data/" in url:
            return FakeResponse(text="")
        return FakeResponse(status_code=503)

    monkeypatch.setattr("btc_dashboard.services.session.get", fake_get)
    monkeypatch.setattr(
        "btc_dashboard.services._utc_now_dt",
        lambda: datetime(2026, 5, 4, tzinfo=UTC),
    )

    payload = get_etf_flow(_settings(tmp_path))

    assert payload["source"] == "farside-latest"
    assert payload["latest_date"] == "03 May 2026"
    assert payload["latest_net_flow_usd"] == 181_900_000.0
    assert payload["trend"] == "inflow"


def test_get_etf_flow_uses_sosovalue_when_api_key_is_set(monkeypatch, tmp_path) -> None:
    def fake_get(url: str, **kwargs) -> FakeResponse:
        if "farside.co.uk" in url:
            return FakeResponse(status_code=403)
        return FakeResponse(status_code=503)

    def fake_post(url: str, **kwargs) -> FakeResponse:
        return FakeResponse({
            "code": 0,
            "msg": None,
            "data": {
                "list": [
                    {"date": "2026-05-02", "totalNetInflow": 100000000.0},
                    {"date": "2026-05-03", "totalNetInflow": -25000000.0},
                ]
            },
        })

    monkeypatch.setattr("btc_dashboard.services.session.get", fake_get)
    monkeypatch.setattr("btc_dashboard.services.session.post", fake_post)
    monkeypatch.setattr(
        "btc_dashboard.services._utc_now_dt",
        lambda: datetime(2026, 5, 4, tzinfo=UTC),
    )

    payload = get_etf_flow(_settings(tmp_path, sosovalue_api_key="test-key"))

    assert payload["source"] == "sosovalue"
    assert payload["latest_date"] == "2026-05-03"
    assert payload["latest_net_flow_usd"] == -25_000_000.0
    assert payload["7d_flow"] == 75_000_000.0
    assert payload["trend"] == "outflow"
    assert payload["is_fallback"] is False
    assert payload["source_label"] == "Live"
