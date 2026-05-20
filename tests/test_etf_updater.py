from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts import update_etf_flows


def test_build_manual_payload_keeps_numeric_rows_only() -> None:
    payload = update_etf_flows.build_manual_payload(
        {
            "source": "farside",
            "latest_date": "2026-05-18",
            "flow_history": [
                {"date": "2026-05-17", "net_flow_usd": "N/A"},
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
    monkeypatch.setattr(
        update_etf_flows,
        "fetch_live_etf_flow",
        lambda settings: {
            "source": "bitbo",
            "latest_date": "2026-05-18",
            "flow_history": [{"date": "2026-05-18", "net_flow_usd": 123_000_000}],
        },
    )

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

    assert update_etf_flows.main([]) == 0

    assert posted["base_url"] == "https://btcwindow.up.railway.app"
    assert posted["payload"]["flow_history"] == [
        {"date": "2026-05-18", "net_flow_usd": 123_000_000.0},
    ]
    assert "Current ETF check failed; continuing" in capsys.readouterr().err
