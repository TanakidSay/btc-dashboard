from __future__ import annotations

import pandas as pd

from btc_dashboard.config import Settings
from btc_dashboard.services import state
from btc_dashboard.signal_engine import (
    Signal,
    detect_signals,
    latest_signals,
    process_signals,
    should_auto_post_signal,
)


def _settings(tmp_path, **overrides) -> Settings:
    values = {
        "secret_key": "test",
        "fee_csv_path": tmp_path / "fees.csv",
        "viewer_stats_path": tmp_path / "viewer_stats.json",
        "x_signal_state_path": tmp_path / "x_signal_state.json",
        "x_posted_events_path": tmp_path / "posted_events.json",
        "start_worker": False,
    }
    values.update(overrides)
    return Settings(**values)


def test_detects_mega_whale_signal_from_cached_whale_lookup(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "btc_dashboard.signal_engine.get_recent_whale_transactions",
        lambda settings: [{"txid": "a" * 64, "value_btc": 1200}],
    )
    monkeypatch.setattr(
        "btc_dashboard.signal_engine.get_security_overview",
        lambda settings: _safe_security(),
    )
    monkeypatch.setattr(
        "btc_dashboard.signal_engine.get_etf_flow",
        lambda settings: {"latest_net_flow_usd": 0},
    )
    monkeypatch.setattr(
        "btc_dashboard.signal_engine.get_btc_treasury_holdings",
        lambda settings: {},
    )

    with state.lock:
        state.fee_data = pd.DataFrame({"height": [1], "sat_per_vbyte": [1.0]})
        state.price_history.clear()
        state.hashrate = None
        state.hashrate_points.clear()

    signals = detect_signals(_settings(tmp_path))

    mega = next(signal for signal in signals if signal.signal_type == "mega_whale")
    assert mega.immediate
    assert mega.duplicate_key == f"whale:{'a' * 64}"
    assert "https://btcwindow.up.railway.app/" in mega.post_text
    assert len(mega.post_text) <= 280


def test_disabled_post_logging_previews_each_signal(monkeypatch, tmp_path, caplog) -> None:
    signal_calls = {"count": 0}

    def fake_detect(settings):
        signal_calls["count"] += 1
        from btc_dashboard.signal_engine import Signal

        return [
            Signal(
                signal_type="whale_alert",
                severity="high",
                message="Whale alert",
                post_text="Whale alert: 500 BTC moved. Live view: https://btcwindow.up.railway.app/",
                duplicate_key="whale:txid-500",
                immediate=False,
                detected_at="2026-05-05T01:00:00Z",
                source={"value_btc": 500},
            )
        ]

    monkeypatch.setattr("btc_dashboard.signal_engine.detect_signals", fake_detect)
    caplog.set_level("INFO", logger="btc_dashboard.x_poster")
    settings = _settings(tmp_path, enable_x_posting=False)

    first = process_signals(settings)
    second = process_signals(settings)

    assert first[0]["suppressed_reason"] == "x_posting_disabled"
    assert second[0]["suppressed_reason"] == "x_posting_disabled"
    assert "[X preview]" in caplog.text
    assert signal_calls["count"] == 2


def test_process_signals_handles_missing_x_credentials(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "btc_dashboard.signal_engine.detect_signals",
        lambda settings: [
            Signal(
                signal_type="security_event",
                severity="critical",
                message="Security monitor detected 1 event",
                post_text=(
                    "Bitcoin security monitor: 1 event detected. "
                    "https://btcwindow.up.railway.app/"
                ),
                duplicate_key="security_event:2026-05-05T01",
                immediate=True,
                detected_at="2026-05-05T01:00:00Z",
                source={},
            ),
        ],
    )

    results = process_signals(_settings(tmp_path, enable_x_posting=True))

    assert results[0]["posted"] is False
    assert results[0]["suppressed_reason"] == "x_credentials_missing"


def test_whale_499_btc_blocked() -> None:
    policy = should_auto_post_signal(_signal("whale_alert", "high", {"value_btc": 499}))

    assert policy["allowed"] is False
    assert policy["reason"] == "whale_below_500_btc"


def test_whale_500_btc_allowed() -> None:
    policy = should_auto_post_signal(_signal("whale_alert", "high", {"value_btc": 500}))

    assert policy["allowed"] is True
    assert policy["cooldown_applied"] is True


def test_mega_whale_policy_bypasses_cooldown() -> None:
    policy = should_auto_post_signal(
        _signal("mega_whale", "critical", {"value_btc": 1000}, immediate=True)
    )

    assert policy["allowed"] is True
    assert policy["cooldown_applied"] is False


def test_normal_fee_blocked() -> None:
    policy = should_auto_post_signal(_signal("fee_trend_rising", "medium", {}))

    assert policy["allowed"] is False
    assert policy["reason"] == "signal_type_not_auto_postable"


def test_strong_fee_spike_allowed() -> None:
    policy = should_auto_post_signal(_signal("fee_spike", "high", {}))

    assert policy["allowed"] is True
    assert policy["reason"] == "strong_fee_spike"


def test_security_warning_blocked() -> None:
    policy = should_auto_post_signal(_signal("security_event", "high", {}))

    assert policy["allowed"] is False
    assert policy["reason"] == "security_not_critical"


def test_security_critical_allowed() -> None:
    policy = should_auto_post_signal(_signal("security_event", "critical", {}, immediate=True))

    assert policy["allowed"] is True
    assert policy["cooldown_applied"] is False


def test_stale_treasury_blocked() -> None:
    policy = should_auto_post_signal(
        _signal("treasury_holdings", "info", {"status": "stale", "total_btc_held": "N/A"})
    )

    assert policy["allowed"] is False
    assert policy["reason"] == "signal_type_not_auto_postable"


def test_latest_signals_payload_includes_detected_signals(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "btc_dashboard.signal_engine.get_recent_whale_transactions",
        lambda settings: [],
    )
    monkeypatch.setattr(
        "btc_dashboard.signal_engine.get_security_overview",
        lambda settings: _safe_security(),
    )
    monkeypatch.setattr(
        "btc_dashboard.signal_engine.get_etf_flow",
        lambda settings: {"latest_net_flow_usd": -250_000_000, "source": "test"},
    )
    monkeypatch.setattr(
        "btc_dashboard.signal_engine.get_btc_treasury_holdings",
        lambda settings: {},
    )

    with state.lock:
        state.fee_data = pd.DataFrame({"height": [1], "sat_per_vbyte": [1.0]})
        state.price_history.clear()
        state.hashrate_points.clear()

    payload = latest_signals(_settings(tmp_path))

    assert payload["x_posting_enabled"] is False
    assert any(signal["signal_type"] == "etf_outflow" for signal in payload["signals"])


def _safe_security():
    return {
        "double_spend": {"orphan_count": 0},
        "attack_51": {"top_pool_share": 0, "pools": []},
        "invalid_blocks": {"invalid_count": 0},
        "reorgs": {"reorg_count": 0},
    }


def _signal(
    signal_type: str,
    severity: str,
    source: dict,
    *,
    immediate: bool = False,
) -> Signal:
    return Signal(
        signal_type=signal_type,
        severity=severity,
        message="test",
        post_text="BTC Window test https://btcwindow.up.railway.app/",
        duplicate_key=f"{signal_type}:test",
        immediate=immediate,
        detected_at="2026-05-05T01:00:00Z",
        source=source,
    )
