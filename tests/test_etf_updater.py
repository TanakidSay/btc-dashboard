from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from scripts import update_etf_flows


def test_build_manual_payload_keeps_numeric_rows_only() -> None:
    payload = update_etf_flows.build_manual_payload(
        {
            "source": "farside",
            "latest_date": "2026-05-18",
            "flow_history": [
                {"date": "Total", "net_flow_usd": "999000000"},
                {"date": "2026-05-17", "net_flow_usd": "N/A"},
                {"date": "Not a date", "net_flow_usd": "45000000"},
                {"date": "2026-05-18", "net_flow_usd": "123000000", "close_price": "95000"},
            ],
        },
        updated_at="2026-05-19T00:00:00Z",
    )

    assert payload == {
        "source": "manual",
        "updated_at": "2026-05-19T00:00:00Z",
        "flow_history": [
            {"date": "2026-05-18", "net_flow_usd": 123_000_000.0, "close_price": 95_000.0},
        ],
    }


def test_fetch_live_etf_flow_skips_missing_key_and_fallback(monkeypatch) -> None:
    settings = SimpleNamespace(sosovalue_api_key=None, coinglass_api_key=None)

    monkeypatch.setattr(
        update_etf_flows.services,
        "_get_etf_flow_from_farside_latest",
        lambda settings: {"source": "fallback", "is_fallback": True, "error": "blocked"},
    )
    monkeypatch.setattr(
        update_etf_flows.services,
        "_get_etf_flow_from_farside",
        lambda settings: {
            "source": "farside",
            "latest_date": "2026-05-18",
            "flow_history": [{"date": "2026-05-18", "net_flow_usd": 123_000_000}],
        },
    )

    payload = update_etf_flows.fetch_live_etf_flow(settings)  # type: ignore[arg-type]

    assert payload["source"] == "farside"
    assert payload["latest_date"] == "2026-05-18"


def test_fetch_live_etf_flow_rejects_rows_older_than_expected(monkeypatch) -> None:
    settings = SimpleNamespace(sosovalue_api_key=None, coinglass_api_key=None)

    for _, loader_name, _ in update_etf_flows.LIVE_SOURCE_LOADERS:
        monkeypatch.setattr(
            update_etf_flows.services,
            loader_name,
            lambda settings: {
                "source": "bitbo",
                "latest_date": "2026-05-20",
                "flow_history": [{"date": "2026-05-20", "net_flow_usd": 0}],
            },
        )

    with pytest.raises(update_etf_flows.EtfUpdateError, match="older than expected 2026-05-21"):
        update_etf_flows.fetch_live_etf_flow(
            settings,  # type: ignore[arg-type]
            minimum_latest_date=datetime(2026, 5, 21, tzinfo=UTC).date(),
        )


def test_fetch_live_etf_flow_rejects_all_fallback_sources(monkeypatch) -> None:
    settings = SimpleNamespace(sosovalue_api_key=None, coinglass_api_key=None)

    for _, loader_name, _ in update_etf_flows.LIVE_SOURCE_LOADERS:
        monkeypatch.setattr(
            update_etf_flows.services,
            loader_name,
            lambda settings: {"source": "fallback", "is_fallback": True, "error": "unavailable"},
        )

    with pytest.raises(update_etf_flows.EtfUpdateError, match="No live ETF flow source"):
        update_etf_flows.fetch_live_etf_flow(settings)  # type: ignore[arg-type]


def test_post_admin_payload_strips_control_characters(monkeypatch) -> None:
    captured = {}

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"ok": True, "latest_date": "2026-05-18"}

    def fake_post(url, headers, json, timeout):
        captured.update({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return Response()

    monkeypatch.setattr(update_etf_flows.requests, "post", fake_post)

    result = update_etf_flows.post_admin_payload(
        "https://btcwindow.uk/",
        "\n secret-token\t",
        {"source": "manual", "updated_at": "2026-05-19T00:00:00Z", "flow_history": []},
        20,
    )

    assert result["ok"] is True
    assert captured["url"] == "https://btcwindow.uk/api/admin/etf-flows"
    assert captured["headers"] == {"Authorization": "Bearer secret-token"}


def test_main_continues_when_current_etf_check_is_blocked(monkeypatch, capsys) -> None:
    posted = {}

    monkeypatch.setattr(update_etf_flows.Settings, "from_env", lambda: object())
    def fake_fetch_live_etf_flow(settings, *, minimum_latest_date=None):
        return {
            "source": "bitbo",
            "latest_date": "2026-05-18",
            "flow_history": [{"date": "2026-05-18", "net_flow_usd": 123_000_000}],
        }

    monkeypatch.setattr(update_etf_flows, "fetch_live_etf_flow", fake_fetch_live_etf_flow)

    def fail_current_check(base_url: str, timeout: int) -> str:
        raise update_etf_flows.requests.HTTPError("403 Client Error")

    def fake_post_admin(base_url: str, token: str, payload: dict, timeout: int) -> dict:
        posted.update({"base_url": base_url, "payload": payload})
        return {
            "ok": True,
            "latest_date": "2026-05-18",
            "latest_net_flow_usd": 123_000_000,
            "source_label": "Manual",
        }

    monkeypatch.setattr(update_etf_flows, "get_current_latest_date", fail_current_check)
    monkeypatch.setattr(update_etf_flows, "post_admin_payload", fake_post_admin)
    monkeypatch.setenv("ETF_ADMIN_TOKEN", "secret-token")

    assert update_etf_flows.main(["--expected-date", "2026-05-18"]) == 0

    assert posted["base_url"] == "https://btcwindow.up.railway.app"
    assert posted["payload"]["flow_history"] == [
        {"date": "2026-05-18", "net_flow_usd": 123_000_000.0},
    ]
    assert "Current ETF check failed; continuing" in capsys.readouterr().err


def test_minimum_acceptable_latest_date_allows_catch_up() -> None:
    assert update_etf_flows.minimum_acceptable_latest_date(
        datetime(2026, 5, 22, tzinfo=UTC).date(),
        "2026-05-20",
    ).isoformat() == "2026-05-21"


def test_main_allows_next_missing_day_before_expected(monkeypatch) -> None:
    posted = {}
    minimum_dates = []

    monkeypatch.setattr(update_etf_flows.Settings, "from_env", lambda: object())
    monkeypatch.setattr(
        update_etf_flows,
        "get_current_latest_date",
        lambda base_url, timeout: "2026-05-20",
    )

    def fake_fetch_live_etf_flow(settings, *, minimum_latest_date=None):
        minimum_dates.append(minimum_latest_date)
        return {
            "source": "bitbo",
            "latest_date": "2026-05-21",
            "flow_history": [{"date": "2026-05-21", "net_flow_usd": -103_700_000}],
        }

    def fake_post_admin(base_url: str, token: str, payload: dict, timeout: int) -> dict:
        posted.update({"base_url": base_url, "payload": payload})
        return {
            "ok": True,
            "latest_date": "2026-05-21",
            "latest_net_flow_usd": -103_700_000,
            "source_label": "Manual",
        }

    monkeypatch.setattr(update_etf_flows, "fetch_live_etf_flow", fake_fetch_live_etf_flow)
    monkeypatch.setattr(update_etf_flows, "post_admin_payload", fake_post_admin)
    monkeypatch.setenv("ETF_ADMIN_TOKEN", "secret-token")

    assert update_etf_flows.main(["--expected-date", "2026-05-22"]) == 0

    assert minimum_dates == [datetime(2026, 5, 21, tzinfo=UTC).date()]
    assert posted["payload"]["flow_history"] == [
        {"date": "2026-05-21", "net_flow_usd": -103_700_000.0},
    ]


def test_expected_previous_us_trading_date_uses_bangkok_date() -> None:
    assert update_etf_flows.expected_previous_us_trading_date(
        datetime(2026, 5, 22, 1, 30, tzinfo=UTC),
    ).isoformat() == "2026-05-21"


def test_expected_previous_us_trading_date_skips_weekend() -> None:
    assert update_etf_flows.expected_previous_us_trading_date(
        datetime(2026, 5, 25, 1, 30, tzinfo=UTC),
    ).isoformat() == "2026-05-22"
