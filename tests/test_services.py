from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import requests

from btc_dashboard.config import Settings
from btc_dashboard.services import (
    build_alerts,
    clear_cache,
    fee_spike_alert,
    format_hashrate,
    get_btc_price,
    get_btc_price_result,
    get_etf_flow,
    get_fee_data,
    get_hashrate,
    get_hashrate_chart_points,
    get_hashrate_result,
    get_node_count,
    get_node_count_result,
    get_btc_treasury_holdings,
    get_recent_whale_transactions,
    get_viewer_stats,
    _etf_date_is_recent,
    _extract_millions_flow,
    _extract_walletpilot_date,
    _extract_globalcoinguide_date,
    _parse_farside_etf_rows_from_text,
    _parse_farside_latest_rows,
    price_breakout_alert,
    record_view,
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
        "start_worker": False,
        "bitcoin_rpc_password": "test",
        "cache_ttl_seconds": 30,
        "node_block_count": 2,
    }
    values.update(overrides)
    return Settings(
        **values,
    )


def test_record_view_updates_total_and_unique_counts(tmp_path) -> None:
    settings = _settings(tmp_path)

    first = record_view(settings, "127.0.0.1", "BrowserA")
    second = record_view(settings, "127.0.0.1", "BrowserA")
    third = record_view(settings, "127.0.0.2", "BrowserB")

    assert first["total_views"] == 1
    assert second["total_views"] == 2
    assert third["total_views"] == 3
    assert third["unique_visitors"] == 2
    assert third["last_viewed_at"] is not None


def test_get_viewer_stats_returns_zeroes_when_file_is_missing(tmp_path) -> None:
    settings = _settings(tmp_path)

    assert get_viewer_stats(settings) == {
        "total_views": 0,
        "unique_visitors": 0,
        "last_viewed_at": None,
    }


def test_get_btc_price_uses_mempool(monkeypatch, tmp_path) -> None:
    def fake_get(url: str, **kwargs) -> FakeResponse:
        if "mempool.space" in url:
            return FakeResponse({"USD": 98765.43})
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr("btc_dashboard.services.session.get", fake_get)

    assert get_btc_price(_settings(tmp_path)) == 98765.43


def test_get_btc_price_result_includes_source(monkeypatch, tmp_path) -> None:
    def fake_get(url: str, **kwargs) -> FakeResponse:
        if "mempool.space" in url:
            return FakeResponse({"USD": 98765.43})
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr("btc_dashboard.services.session.get", fake_get)

    result = get_btc_price_result(_settings(tmp_path))

    assert result is not None
    assert result.value == 98765.43
    assert result.source == "mempool.space"


def test_get_btc_price_returns_none_when_mempool_fails(monkeypatch, tmp_path) -> None:
    def fake_get(url: str, **kwargs) -> FakeResponse:
        return FakeResponse(status_code=503)

    monkeypatch.setattr("btc_dashboard.services.session.get", fake_get)

    assert get_btc_price(_settings(tmp_path)) is None


def test_get_btc_price_falls_back_to_coingecko(monkeypatch, tmp_path) -> None:
    def fake_get(url: str, **kwargs) -> FakeResponse:
        if "mempool.space" in url:
            return FakeResponse(status_code=503)
        if "coingecko.com" in url:
            return FakeResponse({"bitcoin": {"usd": 87654.32}})
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr("btc_dashboard.services.session.get", fake_get)

    assert get_btc_price(_settings(tmp_path)) == 87654.32


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


def test_get_btc_treasury_holdings_retries_before_success(monkeypatch, tmp_path) -> None:
    calls = {"count": 0}

    def fake_get(url: str, **kwargs) -> FakeResponse:
        calls["count"] += 1
        if calls["count"] < 3:
            return FakeResponse(status_code=503)
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

    assert calls["count"] == 3
    assert payload["status"] == "ok"
    assert payload["source"] == "coingecko-public-treasury"
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
    assert "coingecko-public-treasury" in second["error"]


def test_get_btc_treasury_holdings_returns_stable_error_payload(monkeypatch, tmp_path) -> None:
    def fake_get(url: str, **kwargs) -> FakeResponse:
        return FakeResponse(status_code=503)

    monkeypatch.setattr("btc_dashboard.services.session.get", fake_get)

    payload = get_btc_treasury_holdings(_settings(tmp_path, cache_ttl_seconds=0))

    assert payload == {
        "total_btc_held": "N/A",
        "treasury_dominance_percent": "N/A",
        "top_holders": [],
        "source": "fallback",
        "status": "error",
        "updated_at": None,
        "error": (
            "coingecko-public-treasury: HTTP 503 | "
            "coingecko-company-treasury: HTTP 503"
        ),
    }


def test_parse_farside_etf_rows_from_text_handles_pipe_rows() -> None:
    rows = _parse_farside_etf_rows_from_text(
        "06 Apr 2026 | 181.9 | 147.3 | 3.8 | 118.8 | 471.4\n"
        "07 Apr 2026 | (10.0) | 0.0 | 0.0 | 0.0 | (10.0)\n"
    )

    assert rows == [
        {"date": "06 Apr 2026", "net_flow_usd": 471_400_000.0, "close_price": None},
        {"date": "07 Apr 2026", "net_flow_usd": -10_000_000.0, "close_price": None},
    ]


def test_parse_farside_latest_rows_uses_first_total_value_and_skips_pending_rows() -> None:
    rows = _parse_farside_latest_rows(
        "01 May 2026 284.4 213.4 27.3 88.5 0.0 629.8 "
        "04 May 2026 - - - - - - 0.0 Total 65,502"
    )

    assert rows == [
        {"date": "01 May 2026", "net_flow_usd": 284_400_000.0, "close_price": 0},
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


def test_etf_date_freshness_rejects_stale_scrape_dates(monkeypatch) -> None:
    monkeypatch.setattr("btc_dashboard.services._utc_now_dt", lambda: datetime(2026, 5, 4, tzinfo=UTC))

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
    monkeypatch.setattr("btc_dashboard.services._utc_now_dt", lambda: datetime(2026, 5, 4, tzinfo=UTC))

    payload = get_etf_flow(_settings(tmp_path))

    assert payload["source"] == "walletpilot"
    assert payload["latest_date"] == "03 May 2026"
    assert payload["latest_net_flow_usd"] == 411_000_000.0
    assert payload["7d_flow"] == 573_000_000.0
    assert payload["flow_history"][0]["close_price"] == 0


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
    monkeypatch.setattr("btc_dashboard.services._utc_now_dt", lambda: datetime(2026, 5, 4, tzinfo=UTC))

    payload = get_etf_flow(_settings(tmp_path))

    assert payload["source"] == "globalcoinguide"
    assert payload["latest_date"] == "May 03"
    assert payload["latest_net_flow_usd"] == 342_000_000.0
    assert payload["7d_flow"] == 2_100_000_000.0


def test_get_etf_flow_uses_recent_seeded_fallback_when_live_sources_fail(monkeypatch, tmp_path) -> None:
    def fake_get(url: str, **kwargs) -> FakeResponse:
        return FakeResponse(status_code=503)

    monkeypatch.setattr("btc_dashboard.services.session.get", fake_get)
    monkeypatch.setattr("btc_dashboard.services._utc_now_dt", lambda: datetime(2026, 5, 4, tzinfo=UTC))

    payload = get_etf_flow(_settings(tmp_path))

    assert payload["source"] == "seeded-fallback"
    assert payload["status"] == "stale"
    assert payload["latest_date"] == "01 May 2026"
    assert payload["latest_net_flow_usd"] == 118_900_000.0
    assert payload["7d_flow"] == 543_000_000.0
    assert payload["flow_history"][0]["close_price"] == 0


def test_get_etf_flow_rejects_seeded_fallback_when_seed_is_too_old(monkeypatch, tmp_path) -> None:
    def fake_get(url: str, **kwargs) -> FakeResponse:
        return FakeResponse(status_code=503)

    monkeypatch.setattr("btc_dashboard.services.session.get", fake_get)
    monkeypatch.setattr("btc_dashboard.services._utc_now_dt", lambda: datetime(2026, 5, 20, tzinfo=UTC))

    payload = get_etf_flow(_settings(tmp_path))

    assert payload["source"] == "fallback"
    assert payload["status"] == "error"
    assert payload["latest_date"] == ""
    assert "No fresh ETF flow source available" in payload["error"]


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
    monkeypatch.setattr("btc_dashboard.services._utc_now_dt", lambda: datetime(2026, 5, 4, tzinfo=UTC))

    payload = get_etf_flow(_settings(tmp_path))

    assert payload["source"] == "farside-latest"
    assert payload["latest_date"] == "03 May 2026"
    assert payload["latest_net_flow_usd"] == 181_900_000.0
    assert payload["trend"] == "inflow"


def test_get_etf_flow_uses_sosovalue_when_api_key_is_set(monkeypatch, tmp_path) -> None:
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

    monkeypatch.setattr("btc_dashboard.services.session.post", fake_post)
    monkeypatch.setattr("btc_dashboard.services._utc_now_dt", lambda: datetime(2026, 5, 4, tzinfo=UTC))

    payload = get_etf_flow(_settings(tmp_path, sosovalue_api_key="test-key"))

    assert payload["source"] == "sosovalue"
    assert payload["latest_date"] == "2026-05-03"
    assert payload["latest_net_flow_usd"] == -25_000_000.0
    assert payload["7d_flow"] == 75_000_000.0
    assert payload["trend"] == "outflow"
