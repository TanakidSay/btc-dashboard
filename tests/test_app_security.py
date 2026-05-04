from __future__ import annotations

from base64 import b64encode

from btc_dashboard.app import create_app
from btc_dashboard.config import Settings


def _settings(tmp_path, **overrides) -> Settings:
    values = {
        "secret_key": "test-secret",
        "fee_csv_path": tmp_path / "fees.csv",
        "viewer_stats_path": tmp_path / "viewer_stats.json",
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
