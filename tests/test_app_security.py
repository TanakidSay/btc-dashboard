from __future__ import annotations

from base64 import b64encode

from btc_dashboard.app import create_app
from btc_dashboard.config import Settings


def _settings(tmp_path, **overrides) -> Settings:
    values = {
        "secret_key": "test-secret",
        "fee_csv_path": tmp_path / "fees.csv",
        "viewer_stats_path": tmp_path / "viewer_stats.json",
        "x_signal_state_path": tmp_path / "x_signal_state.json",
        "x_posted_events_path": tmp_path / "posted_events.json",
        "start_worker": False,
    }
    values.update(overrides)
    return Settings(**values)


def test_security_headers_are_added(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("btc_dashboard.app.warm_local_cache", lambda settings: None)
    app = create_app(_settings(tmp_path))

    response = app.test_client().get("/healthz")

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


def test_basic_auth_blocks_dashboard_without_credentials(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("btc_dashboard.app.warm_local_cache", lambda settings: None)
    app = create_app(
        _settings(
            tmp_path,
            dashboard_username="admin",
            dashboard_password="secret",
        )
    )

    response = app.test_client().get("/api/metrics")

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == 'Basic realm="Bitcoin Dashboard"'


def test_basic_auth_allows_valid_credentials(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("btc_dashboard.app.warm_local_cache", lambda settings: None)
    app = create_app(
        _settings(
            tmp_path,
            dashboard_username="admin",
            dashboard_password="secret",
        )
    )
    credentials = b64encode(b"admin:secret").decode("ascii")

    response = app.test_client().get(
        "/api/metrics",
        headers={"Authorization": f"Basic {credentials}"},
    )

    assert response.status_code == 200


def test_bearer_token_allows_api_access(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("btc_dashboard.app.warm_local_cache", lambda settings: None)
    app = create_app(_settings(tmp_path, dashboard_api_token="token-123"))

    response = app.test_client().get(
        "/api/metrics",
        headers={"Authorization": "Bearer token-123"},
    )

    assert response.status_code == 200


def test_health_check_stays_public_when_auth_is_enabled(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("btc_dashboard.app.warm_local_cache", lambda settings: None)
    app = create_app(_settings(tmp_path, dashboard_api_token="token-123"))

    response = app.test_client().get("/healthz")

    assert response.status_code == 200


def test_health_alias_stays_public_when_auth_is_enabled(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("btc_dashboard.app.warm_local_cache", lambda settings: None)
    app = create_app(_settings(tmp_path, dashboard_api_token="token-123"))

    response = app.test_client().get("/health")

    assert response.status_code == 200


def test_index_records_viewer_stats(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("btc_dashboard.app.warm_local_cache", lambda settings: None)
    app = create_app(_settings(tmp_path))

    response = app.test_client().get("/", headers={"User-Agent": "pytest-browser"})

    assert response.status_code == 200
    viewer_response = app.test_client().get("/api/viewers")
    assert viewer_response.status_code == 200
    assert viewer_response.get_json()["total_views"] == 1


def test_treasury_route_returns_stable_json_when_service_fails(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("btc_dashboard.app.warm_local_cache", lambda settings: None)
    monkeypatch.setattr(
        "btc_dashboard.routes.get_btc_treasury_holdings",
        lambda settings: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    app = create_app(_settings(tmp_path))

    response = app.test_client().get("/api/treasury")

    assert response.status_code == 200
    assert response.get_json() == {
        "total_btc_held": "N/A",
        "treasury_dominance_percent": "N/A",
        "top_holders": [],
        "source": "fallback",
        "status": "error",
        "updated_at": None,
        "error": "boom",
    }


def test_security_route_never_returns_null_fields(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("btc_dashboard.app.warm_local_cache", lambda settings: None)
    monkeypatch.setattr(
        "btc_dashboard.routes.get_security_overview",
        lambda settings: {
            "double_spend": {
                "orphan_count": 0,
                "orphans": [],
                "active_height": None,
                "risk_level": "low",
            },
            "attack_51": {"pools": [], "top_pool_share": 0, "risk_level": "low"},
            "invalid_blocks": {"invalid_count": 0, "invalid_chains": [], "risk_level": "low"},
            "reorgs": {
                "reorg_count": 0,
                "reorgs": [],
                "current_height": None,
                "max_branch_length": 0,
                "risk_level": "low",
            },
            "updated_at": "2026-05-04T14:23:03Z",
            "status": "ok",
        },
    )
    app = create_app(_settings(tmp_path))

    response = app.test_client().get("/api/security")

    assert response.status_code == 200
    body = response.get_json()
    assert body["double_spend"]["active_height"] == 0
    assert body["reorgs"]["current_height"] == 0
    assert body["updated_at"] == "2026-05-04T14:23:03Z"


def test_etf_route_returns_empty_strings_for_missing_timestamps(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("btc_dashboard.app.warm_local_cache", lambda settings: None)
    monkeypatch.setattr(
        "btc_dashboard.routes.get_etf_flow",
        lambda settings: {
            "latest_date": "",
            "latest_net_flow_usd": 0,
            "7d_flow": 0,
            "trend": "neutral",
            "flow_history": [],
            "source": "fallback",
            "updated_at": "",
            "status": "error",
            "error": "No fresh ETF flow source available",
        },
    )
    app = create_app(_settings(tmp_path))

    response = app.test_client().get("/api/etf")

    assert response.status_code == 200
    body = response.get_json()
    assert body["latest_date"] == ""
    assert body["updated_at"] == ""


def test_x_status_route_reports_configuration(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("btc_dashboard.app.warm_local_cache", lambda settings: None)
    app = create_app(_settings(tmp_path, enable_x_posting=True, x_api_key="key"))

    response = app.test_client().get("/api/x-status")

    assert response.status_code == 200
    body = response.get_json()
    assert body["enabled"] is True
    assert body["credentials_configured"] is False
    assert body["last_post_time"] is None
    assert "last_error" in body
    assert body["test_enabled"] is False
    assert body["cooldown_remaining_seconds"] == 0
    assert body["posted_events_count"] == 0
    assert body["daily_post_count"] == 0
    assert body["daily_limit_remaining"] == 4
    assert "last_block_reason" in body


def test_signals_policy_route(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("btc_dashboard.app.warm_local_cache", lambda settings: None)
    app = create_app(_settings(tmp_path))

    response = app.test_client().get("/api/signals-policy")

    assert response.status_code == 200
    body = response.get_json()
    assert "whale_alert" in body["allowed_signal_types"]
    assert body["thresholds"]["whale_btc"] == 500
    assert body["cooldown_minutes"] == 60
    assert body["max_posts_per_day"] == 4


def test_x_test_post_preview_mode(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("btc_dashboard.app.warm_local_cache", lambda settings: None)
    app = create_app(_settings(tmp_path, enable_x_test_post=True, enable_x_posting=False))

    response = app.test_client().post("/api/x-test-post")

    assert response.status_code == 200
    body = response.get_json()
    assert body["ok"] is True
    assert body["mode"] == "preview"
    assert "BTC Window X posting test" in body["text"]
    assert body["last_error"] is None


def test_x_test_post_get_preview_mode(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("btc_dashboard.app.warm_local_cache", lambda settings: None)
    app = create_app(_settings(tmp_path, enable_x_test_post=True, enable_x_posting=False))

    response = app.test_client().get("/api/x-test-post")

    assert response.status_code == 200
    body = response.get_json()
    assert body["ok"] is True
    assert body["mode"] == "preview"
    assert "BTC Window X posting test" in body["text"]
    assert body["last_error"] is None


def test_x_test_post_disabled_returns_403(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("btc_dashboard.app.warm_local_cache", lambda settings: None)
    app = create_app(_settings(tmp_path, enable_x_test_post=False))

    response = app.test_client().post("/api/x-test-post")

    assert response.status_code == 403
    body = response.get_json()
    assert body["ok"] is False
    assert body["mode"] == "error"


def test_x_test_post_get_disabled_returns_403(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("btc_dashboard.app.warm_local_cache", lambda settings: None)
    app = create_app(_settings(tmp_path, enable_x_test_post=False))

    response = app.test_client().get("/api/x-test-post")

    assert response.status_code == 403
    body = response.get_json()
    assert body["ok"] is False
    assert body["mode"] == "error"


def test_x_test_post_missing_credentials_does_not_crash(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("btc_dashboard.app.warm_local_cache", lambda settings: None)
    app = create_app(
        _settings(
            tmp_path,
            enable_x_test_post=True,
            enable_x_posting=True,
            x_api_key="key",
        )
    )

    response = app.test_client().post("/api/x-test-post")

    assert response.status_code == 200
    body = response.get_json()
    assert body["ok"] is False
    assert body["mode"] == "error"
    assert "X_API_SECRET" in body["last_error"]
