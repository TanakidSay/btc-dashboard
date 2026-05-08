from __future__ import annotations

import hashlib
from datetime import datetime

import pandas as pd

from btc_dashboard.config import Settings
from btc_dashboard.services import state
from btc_dashboard.signal_engine import (
    Signal,
    _queue_pending_signal,
    detect_signals,
    generate_daily_dashboard_post,
    latest_signals,
    process_daily_post,
    process_signals,
    should_auto_post_signal,
)
from btc_dashboard.x_poster import XPostResult


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

    assert first[0]["suppressed_reason"] == "queued"
    assert first[-1]["suppressed_reason"] == "x_posting_disabled"
    assert second[0]["suppressed_reason"] == "queued"
    assert second[-1]["suppressed_reason"] == "x_posting_disabled"
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
    assert policy["score"] == 80


def test_mega_whale_policy_bypasses_cooldown() -> None:
    policy = should_auto_post_signal(
        _signal("mega_whale", "critical", {"value_btc": 1000}, immediate=True)
    )

    assert policy["allowed"] is True
    assert policy["cooldown_applied"] is False


def test_normal_fee_blocked() -> None:
    policy = should_auto_post_signal(_signal("fee_trend_rising", "medium", {}))

    assert policy["allowed"] is False
    assert policy["reason"] == "score_too_low"


def test_cheap_fee_window_score_below_65_blocked() -> None:
    policy = should_auto_post_signal(_signal("cheap_fee_window", "low", {}))

    assert policy["allowed"] is False
    assert policy["reason"] == "score_too_low"
    assert policy["score"] == 45


def test_price_breakout_with_low_fee_allowed() -> None:
    policy = should_auto_post_signal(
        _signal("price_breakout", "medium", {"fee_still_low": True})
    )

    assert policy["allowed"] is True
    assert policy["score"] == 65


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
    assert policy["reason"] == "score_too_low"


def test_normal_informational_score_zero_blocked() -> None:
    policy = should_auto_post_signal(_signal("normal_info", "info", {}))

    assert policy["allowed"] is False
    assert policy["score"] == 0


def test_lower_score_duplicate_signal_blocked() -> None:
    state = {"pending_signal_queue": []}
    signal = _signal("price_breakout", "medium", {"fee_still_low": True})
    first = _queue_pending_signal(signal, {"score": 80}, state)
    second = _queue_pending_signal(signal, {"score": 65}, state)

    assert first["queued"] is True
    assert second == {"queued": False, "reason": "lower_priority_duplicate"}


def test_higher_score_signal_selected_before_lower(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "btc_dashboard.signal_engine.detect_signals",
        lambda settings: [
            _signal("fee_spike", "high", {}),
            _signal("whale_alert", "high", {"value_btc": 500}),
        ],
    )

    results = process_signals(_settings(tmp_path, enable_x_posting=False))

    selected = next(result for result in results if result.get("selected_from_queue"))
    assert selected["signal_type"] == "whale_alert"
    assert selected["score"] == 80


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


def test_daily_auto_post_runs_at_most_once_per_day(monkeypatch, tmp_path) -> None:
    calls = {"posts": 0}

    def fake_post_to_x(*args, **kwargs):
        calls["posts"] += 1
        return XPostResult(posted=True)

    _mock_daily_cached_data(monkeypatch)
    monkeypatch.setattr("btc_dashboard.signal_engine.post_to_x", fake_post_to_x)
    settings = _settings(tmp_path, enable_x_posting=True, x_daily_post_hour=9)
    now = datetime(2026, 5, 8, 9, 0)

    first = process_daily_post(settings, now)
    second = process_daily_post(settings, now)

    assert first["posted"] is True
    assert second["reason"] == "already_posted_today"
    assert calls["posts"] == 1


def test_frequent_polling_before_daily_hour_does_not_trigger_x_post(monkeypatch, tmp_path) -> None:
    _mock_daily_cached_data(monkeypatch)
    monkeypatch.setattr(
        "btc_dashboard.signal_engine.post_to_x",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not post")),
    )
    settings = _settings(tmp_path, enable_x_posting=True, x_daily_post_hour=9)

    first = process_daily_post(settings, datetime(2026, 5, 8, 8, 0))
    second = process_daily_post(settings, datetime(2026, 5, 8, 8, 1))

    assert first["reason"] == "not_due"
    assert second["reason"] == "not_due"


def test_daily_lock_prevents_duplicate_posts(monkeypatch, tmp_path) -> None:
    _mock_daily_cached_data(monkeypatch)
    today = "2026-05-08"
    settings = _settings(tmp_path, enable_x_posting=True, x_daily_post_hour=9)
    settings.x_signal_state_path.write_text(
        f'{{"last_daily_post_date": "{today}"}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "btc_dashboard.signal_engine.post_to_x",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not post")),
    )

    result = process_daily_post(settings, datetime(2026, 5, 8, 9, 0))

    assert result["reason"] == "already_posted_today"


def test_daily_post_generation_uses_cached_data(monkeypatch, tmp_path) -> None:
    calls = {"cache": 0}

    def fake_cached(cache_name):
        calls["cache"] += 1
        return _ownership_cache() if cache_name == "ownership_cache" else _security_cache()

    monkeypatch.setattr("btc_dashboard.signal_engine.cached_dashboard_resource", fake_cached)
    monkeypatch.setattr("btc_dashboard.signal_engine.snapshot", _daily_snapshot)

    text, reason = generate_daily_dashboard_post(_settings(tmp_path))

    assert reason is None
    assert calls["cache"] == 2
    assert "https://btcwindow.up.railway.app/" in text


def test_daily_post_makes_no_ai_call_by_default(monkeypatch, tmp_path) -> None:
    _mock_daily_cached_data(monkeypatch)
    monkeypatch.setattr(
        "btc_dashboard.signal_engine.detect_signals",
        lambda settings: (_ for _ in ()).throw(AssertionError("signal/AI path should not run")),
    )
    monkeypatch.setattr(
        "btc_dashboard.signal_engine.post_to_x",
        lambda *args, **kwargs: XPostResult(posted=False, reason="x_posting_disabled"),
    )

    result = process_daily_post(_settings(tmp_path, x_daily_post_hour=9), datetime(2026, 5, 8, 9))

    assert result["reason"] == "x_posting_disabled"


def test_invalid_daily_data_prevents_post(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("btc_dashboard.signal_engine.snapshot", _daily_snapshot)
    monkeypatch.setattr(
        "btc_dashboard.signal_engine.cached_dashboard_resource",
        lambda cache_name: {"percent_mined": "N/A"} if cache_name == "ownership_cache" else {},
    )
    monkeypatch.setattr(
        "btc_dashboard.signal_engine.post_to_x",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not post")),
    )

    result = process_daily_post(_settings(tmp_path, x_daily_post_hour=9), datetime(2026, 5, 8, 9))

    assert result["reason"] == "invalid_core_data"


def test_daily_post_blocks_duplicate_content_from_previous_day(monkeypatch, tmp_path) -> None:
    _mock_daily_cached_data(monkeypatch)
    settings = _settings(tmp_path, enable_x_posting=True, x_daily_post_hour=9)
    text, _ = generate_daily_dashboard_post(settings)
    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    settings.x_signal_state_path.write_text(
        f'{{"last_daily_post_date": "2026-05-07", "last_daily_post_hash": "{text_hash}"}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "btc_dashboard.signal_engine.post_to_x",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not post")),
    )

    result = process_daily_post(settings, datetime(2026, 5, 8, 9))

    assert result["reason"] == "duplicate_daily_content"


def test_generated_daily_post_quality_rules(monkeypatch, tmp_path) -> None:
    _mock_daily_cached_data(monkeypatch)

    text, reason = generate_daily_dashboard_post(_settings(tmp_path))

    assert reason is None
    assert "https://btcwindow.up.railway.app/" in text
    assert len(text) <= 280
    assert "51% attack risk" not in text.lower()


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


def _daily_snapshot():
    return {
        "btc_price": 98_765.43,
        "fee_data": pd.DataFrame({"height": [1], "sat_per_vbyte": [2.5]}),
        "hashrate": 650_000_000,
        "price_history": [97_000, 98_765.43],
    }


def _ownership_cache():
    return {
        "percent_mined": 94.29,
        "remaining_to_mine": 1_200_000,
        "categories": [],
        "chart_categories": [],
    }


def _security_cache():
    return {
        "double_spend": {"orphan_count": 0},
        "attack_51": {"top_pool_share": 0},
        "invalid_blocks": {"invalid_count": 0},
        "reorgs": {"reorg_count": 0},
    }


def _mock_daily_cached_data(monkeypatch) -> None:
    monkeypatch.setattr("btc_dashboard.signal_engine.snapshot", _daily_snapshot)
    monkeypatch.setattr(
        "btc_dashboard.signal_engine.cached_dashboard_resource",
        lambda cache_name: _ownership_cache()
        if cache_name == "ownership_cache"
        else _security_cache(),
    )
