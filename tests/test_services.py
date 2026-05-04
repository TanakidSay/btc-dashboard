from __future__ import annotations

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
    get_fee_data,
    get_hashrate,
    get_hashrate_result,
    get_node_count,
    get_node_count_result,
    price_breakout_alert,
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


def _settings(tmp_path) -> Settings:
    clear_cache()
    return Settings(
        secret_key="test",
        fee_csv_path=tmp_path / "fees.csv",
        start_worker=False,
        bitcoin_rpc_password="test",
        cache_ttl_seconds=30,
        node_block_count=2,
    )


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
