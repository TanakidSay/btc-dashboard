from __future__ import annotations

import json
import sys
import types
from datetime import UTC, datetime, timedelta

from btc_dashboard.config import Settings
from btc_dashboard.x_poster import get_x_status, post_to_x


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


def test_x_poster_logs_preview_when_disabled(tmp_path, caplog) -> None:
    settings = _settings(tmp_path, enable_x_posting=False)
    caplog.set_level("INFO", logger="btc_dashboard.x_poster")

    result = post_to_x("hello X", settings)

    assert not result.posted
    assert result.reason == "x_posting_disabled"
    assert "[X preview] hello X" in caplog.text


def test_x_poster_reports_missing_credentials_without_crashing(tmp_path, caplog) -> None:
    settings = _settings(tmp_path, enable_x_posting=True, x_api_key="key")
    caplog.set_level("ERROR", logger="btc_dashboard.x_poster")

    result = post_to_x("hello X", settings)
    status = get_x_status(settings)

    assert not result.posted
    assert result.reason == "x_credentials_missing"
    assert "X_API_SECRET" in (result.error or "")
    assert "missing credentials" in caplog.text
    assert status["enabled"] is True
    assert status["credentials_configured"] is False
    assert status["last_error"] == result.error


def test_x_poster_blocks_duplicate_text(monkeypatch, tmp_path) -> None:
    _install_fake_tweepy(monkeypatch)
    settings = _posting_settings(tmp_path)
    text = "BTC Window signal one https://btcwindow.up.railway.app/"

    first = post_to_x(text, settings, event_id="event-1", signal_type="fee_spike")
    second = post_to_x(text, settings, event_id="event-2", signal_type="fee_spike")

    assert first.posted is True
    assert second.posted is False
    assert second.reason == "x_duplicate"
    assert "duplicate text" in (second.error or "")


def test_x_poster_blocks_280_character_limit(tmp_path) -> None:
    result = post_to_x("x" * 281, _posting_settings(tmp_path))

    assert result.posted is False
    assert result.reason == "x_text_too_long"


def test_x_poster_daily_limit_blocks_second_normal_signal(monkeypatch, tmp_path) -> None:
    _install_fake_tweepy(monkeypatch)
    settings = _posting_settings(tmp_path)

    first = post_to_x("Normal signal one", settings, event_id="event-1", signal_type="fee_spike")
    second = post_to_x("Normal signal two", settings, event_id="event-2", signal_type="etf_inflow")

    assert first.posted is True
    assert second.posted is False
    assert second.reason == "daily_limit_reached"


def test_x_poster_mega_whale_bypasses_cooldown(monkeypatch, tmp_path) -> None:
    _install_fake_tweepy(monkeypatch)
    settings = _posting_settings(tmp_path)

    first = post_to_x("Normal signal one", settings, event_id="event-1", signal_type="fee_spike")
    second = post_to_x(
        "Mega whale signal",
        settings,
        event_id="whale:txid-1",
        signal_type="mega_whale",
        bypass_cooldown=True,
    )

    assert first.posted is True
    assert second.posted is True


def test_x_poster_daily_limit_enforced(monkeypatch, tmp_path) -> None:
    _install_fake_tweepy(monkeypatch)
    settings = _posting_settings(tmp_path)
    now = datetime.now(UTC).replace(microsecond=0)
    events = [
        {
            "event_id": "event-0",
            "signal_type": "fee_spike",
            "text_hash": "hash-0",
            "posted_at": (now - timedelta(hours=2)).isoformat().replace("+00:00", "Z"),
            "bypass_cooldown": False,
        }
    ]
    settings.x_posted_events_path.write_text(json.dumps({"events": events}), encoding="utf-8")

    blocked = post_to_x("Normal daily limit signal", settings, event_id="event-13")
    bypassed = post_to_x(
        "Security critical after daily limit",
        settings,
        event_id="security:critical-1",
        signal_type="security_event",
        bypass_cooldown=True,
    )
    mega = post_to_x(
        "Mega whale after daily limit",
        settings,
        event_id="whale:mega-1",
        signal_type="mega_whale",
        bypass_cooldown=True,
    )
    status = get_x_status(settings)

    assert blocked.posted is False
    assert blocked.reason == "daily_limit_reached"
    assert bypassed.posted is True
    assert mega.posted is True
    assert status["max_posts_per_day"] == 1
    assert status["daily_post_count"] == 1
    assert status["daily_limit_remaining"] == 0


def _posting_settings(tmp_path) -> Settings:
    return _settings(
        tmp_path,
        enable_x_posting=True,
        x_api_key="key",
        x_api_secret="secret",
        x_access_token="token",
        x_access_secret="access-secret",
    )


def _install_fake_tweepy(monkeypatch) -> None:
    class FakeClient:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def create_tweet(self, text: str):
            return {"data": {"text": text}}

    monkeypatch.setitem(sys.modules, "tweepy", types.SimpleNamespace(Client=FakeClient))
