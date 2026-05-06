from __future__ import annotations

from pathlib import Path

import pandas as pd

from btc_dashboard.config import Settings
from btc_dashboard.services import MetricValue, state
from btc_dashboard.worker import (
    _last_metric_refresh,
    notify_fee_spike_if_needed,
    refresh_once,
    run_worker,
    warm_local_cache,
)


def _settings() -> Settings:
    return Settings(
        secret_key="test",
        fee_csv_path=Path("unused.csv"),
        fee_spike_threshold=5,
        notification_webhook_url="https://example.com/webhook",
        notification_cooldown_seconds=300,
        start_worker=False,
    )


def _stub_slow_refreshes(monkeypatch) -> None:
    monkeypatch.setattr("btc_dashboard.worker.get_etf_flow", lambda settings: {})
    monkeypatch.setattr("btc_dashboard.worker.get_btc_treasury_holdings", lambda settings: {})
    monkeypatch.setattr("btc_dashboard.worker.get_btc_supply_ownership", lambda settings: {})
    monkeypatch.setattr("btc_dashboard.worker.get_security_overview", lambda settings: {})
    monkeypatch.setattr("btc_dashboard.worker.process_signals", lambda settings: [])


def test_notify_fee_spike_sends_once(monkeypatch) -> None:
    sent_alerts: list[dict[str, str]] = []

    def fake_send_notification(alert: dict[str, str], settings: Settings) -> bool:
        sent_alerts.append(alert)
        return True

    monkeypatch.setattr("btc_dashboard.worker.send_notification", fake_send_notification)

    with state.lock:
        state.last_fee_spike_notification_key = None
        state.last_fee_spike_notification_ts = 0

    fee_data = pd.DataFrame({"height": [100, 101], "sat_per_vbyte": [4.9, 5.1]})

    notify_fee_spike_if_needed(fee_data, _settings())
    notify_fee_spike_if_needed(fee_data, _settings())

    assert len(sent_alerts) == 1
    assert sent_alerts[0]["message"] == "Fee Spike: 5.10 sat/vB crossed above 5.00"


def test_notify_fee_spike_retries_after_failed_send(monkeypatch) -> None:
    send_results = [False, True]
    send_count = 0

    def fake_send_notification(alert: dict[str, str], settings: Settings) -> bool:
        nonlocal send_count
        send_count += 1
        return send_results.pop(0)

    monkeypatch.setattr("btc_dashboard.worker.send_notification", fake_send_notification)

    with state.lock:
        state.last_fee_spike_notification_key = None
        state.last_fee_spike_notification_ts = 0

    fee_data = pd.DataFrame({"height": [100, 101], "sat_per_vbyte": [4.9, 5.1]})

    notify_fee_spike_if_needed(fee_data, _settings())
    notify_fee_spike_if_needed(fee_data, _settings())

    assert send_count == 2


def test_refresh_once_preserves_last_metrics_on_api_failure(monkeypatch, tmp_path) -> None:
    _last_metric_refresh.clear()
    _stub_slow_refreshes(monkeypatch)
    settings = Settings(
        secret_key="test",
        fee_csv_path=tmp_path / "fees.csv",
        start_worker=False,
    )
    settings.fee_csv_path.write_text(
        "height,tx_count,total_fee_btc,sat_per_vbyte\n100,10,0.1,1.5\n",
        encoding="utf-8",
    )

    monkeypatch.setattr("btc_dashboard.worker.get_hashrate_result", lambda settings: None)
    monkeypatch.setattr(
        "btc_dashboard.worker.get_node_count_result",
        lambda settings: MetricValue("N/A", "fallback"),
    )
    monkeypatch.setattr("btc_dashboard.worker.get_btc_price_result", lambda settings: None)
    monkeypatch.setattr(
        "btc_dashboard.worker.get_fee_data",
        lambda settings: pd.read_csv(settings.fee_csv_path),
    )
    monkeypatch.setattr("btc_dashboard.worker.notify_fee_spike_if_needed", lambda *args: None)

    with state.lock:
        state.hashrate = 123.0
        state.node_count = 456
        state.btc_price = 789.0
        state.hashrate_history.clear()
        state.price_history.clear()
        state.time_labels.clear()

    refresh_once(settings)

    with state.lock:
        assert state.hashrate == 123.0
        assert state.node_count == 456
        assert state.btc_price == 789.0
        assert list(state.hashrate_history) == [123.0]
        assert list(state.price_history) == [789.0]


def test_refresh_once_updates_external_metrics(monkeypatch, tmp_path, caplog) -> None:
    _last_metric_refresh.clear()
    _stub_slow_refreshes(monkeypatch)
    settings = Settings(
        secret_key="test",
        fee_csv_path=tmp_path / "fees.csv",
        start_worker=False,
    )
    fee_data = pd.DataFrame({"height": [100], "tx_count": [10], "sat_per_vbyte": [1.5]})

    monkeypatch.setattr("btc_dashboard.worker.get_fee_data", lambda settings: fee_data)
    monkeypatch.setattr(
        "btc_dashboard.worker.get_hashrate_result",
        lambda settings: MetricValue(999.0, "mempool.space"),
    )
    monkeypatch.setattr(
        "btc_dashboard.worker.get_node_count_result",
        lambda settings: MetricValue(17425, "mempool.space"),
    )
    monkeypatch.setattr(
        "btc_dashboard.worker.get_btc_price_result",
        lambda settings: MetricValue(87654.32, "coingecko"),
    )
    monkeypatch.setattr("btc_dashboard.worker.notify_fee_spike_if_needed", lambda *args: None)

    with state.lock:
        state.hashrate = None
        state.node_count = None
        state.btc_price = None
        state.hashrate_history.clear()
        state.price_history.clear()
        state.time_labels.clear()

    caplog.set_level("INFO", logger="btc_dashboard.worker")

    refresh_once(settings)

    with state.lock:
        assert state.hashrate == 999.0
        assert state.node_count == 17425
        assert state.btc_price == 87654.32
        assert list(state.hashrate_history) == [999.0]
        assert list(state.price_history) == [87654.32]

    assert "price=$87,654.32 source=coingecko" in caplog.text
    assert "hashrate=999.00 TH/s source=mempool.space" in caplog.text
    assert "nodes=17425 source=mempool.space" in caplog.text


def test_refresh_once_keeps_only_price_on_five_second_cadence(monkeypatch, tmp_path) -> None:
    _last_metric_refresh.clear()
    _stub_slow_refreshes(monkeypatch)
    settings = Settings(
        secret_key="test",
        fee_csv_path=tmp_path / "fees.csv",
        start_worker=False,
    )
    fee_data = pd.DataFrame({"height": [100], "tx_count": [10], "sat_per_vbyte": [1.5]})
    calls = {"price": 0, "fees": 0, "hashrate": 0, "nodes": 0}
    monotonic_values = iter([100.0, 105.0])

    def fake_price(settings: Settings) -> MetricValue:
        calls["price"] += 1
        return MetricValue(80000 + calls["price"], "mempool.space")

    def fake_fees(settings: Settings) -> pd.DataFrame:
        calls["fees"] += 1
        return fee_data

    def fake_hashrate(settings: Settings) -> MetricValue:
        calls["hashrate"] += 1
        return MetricValue(999.0, "mempool.space")

    def fake_nodes(settings: Settings) -> MetricValue:
        calls["nodes"] += 1
        return MetricValue(17425, "mempool.space")

    monkeypatch.setattr("btc_dashboard.worker.time.monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr("btc_dashboard.worker.get_btc_price_result", fake_price)
    monkeypatch.setattr("btc_dashboard.worker.get_fee_data", fake_fees)
    monkeypatch.setattr("btc_dashboard.worker.get_hashrate_result", fake_hashrate)
    monkeypatch.setattr("btc_dashboard.worker.get_node_count_result", fake_nodes)
    monkeypatch.setattr("btc_dashboard.worker.notify_fee_spike_if_needed", lambda *args: None)

    with state.lock:
        state.fee_data = None
        state.hashrate = None
        state.node_count = None
        state.btc_price = None
        state.hashrate_history.clear()
        state.price_history.clear()

    refresh_once(settings)
    refresh_once(settings)

    assert calls == {"price": 2, "fees": 1, "hashrate": 1, "nodes": 1}


def test_run_worker_configures_and_starts_loop(monkeypatch, tmp_path, caplog) -> None:
    settings = Settings(
        secret_key="test",
        fee_csv_path=tmp_path / "fees.csv",
        start_worker=False,
        refresh_seconds=7,
    )
    calls: list[Settings] = []

    monkeypatch.setattr("btc_dashboard.worker.Settings.from_env", lambda: settings)
    monkeypatch.setattr(
        "btc_dashboard.worker.configure_state",
        lambda settings: calls.append(settings),
    )
    monkeypatch.setattr(
        "btc_dashboard.worker.background_worker",
        lambda settings: calls.append(settings),
    )
    caplog.set_level("INFO", logger="btc_dashboard.worker")

    run_worker()

    assert calls == [settings, settings]
    assert "Starting dashboard worker; refresh_seconds=7" in caplog.text


def test_warm_local_cache_seeds_hashrate_points(monkeypatch, tmp_path) -> None:
    settings = Settings(
        secret_key="test",
        fee_csv_path=tmp_path / "fees.csv",
        start_worker=False,
    )
    fee_data = pd.DataFrame({"height": [100], "tx_count": [10], "sat_per_vbyte": [1.5]})

    monkeypatch.setattr("btc_dashboard.worker.get_fee_data", lambda settings: fee_data)
    monkeypatch.setattr(
        "btc_dashboard.worker.get_btc_price_result",
        lambda settings: MetricValue(80000.0, "mempool.space"),
    )
    monkeypatch.setattr(
        "btc_dashboard.worker.get_hashrate_result",
        lambda settings: MetricValue(999.0, "mempool.space"),
    )
    monkeypatch.setattr("btc_dashboard.worker.get_hashrate_chart_points", lambda settings: [
        {"timestamp": "2026-05-04T14:00:00Z", "value": 990.0},
        {"timestamp": "2026-05-04T14:05:00Z", "value": 999.0},
    ])
    monkeypatch.setattr(
        "btc_dashboard.worker.get_node_count_result",
        lambda settings: MetricValue(17000, "bitnodes"),
    )
    monkeypatch.setattr("btc_dashboard.worker.get_etf_flow", lambda settings: {})
    monkeypatch.setattr("btc_dashboard.worker.get_btc_treasury_holdings", lambda settings: {})
    monkeypatch.setattr("btc_dashboard.worker.get_btc_supply_ownership", lambda settings: {})
    monkeypatch.setattr("btc_dashboard.worker.get_security_overview", lambda settings: {})

    with state.lock:
        state.hashrate_points.clear()
        state.hashrate_history.clear()

    warm_local_cache(settings)

    with state.lock:
        assert list(state.hashrate_points) == [
            {"timestamp": "2026-05-04T14:00:00Z", "value": 990.0},
            {"timestamp": "2026-05-04T14:05:00Z", "value": 999.0},
        ]
        assert list(state.hashrate_history) == [990.0, 999.0]
