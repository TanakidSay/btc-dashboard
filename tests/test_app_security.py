from __future__ import annotations

import json
from base64 import b64encode
from pathlib import Path

from btc_dashboard.app import create_app
from btc_dashboard.config import Settings
from btc_dashboard.services import MetricValue, state


def _settings(tmp_path, **overrides) -> Settings:
    values = {
        "secret_key": "test-secret",
        "fee_csv_path": tmp_path / "fees.csv",
        "viewer_stats_path": tmp_path / "viewer_stats.json",
        "viewer_analytics_path": tmp_path / "viewer_analytics.json",
        "view_counter_path": tmp_path / "view_counter.json",
        "alerts_history_path": tmp_path / "alerts_history.json",
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


def test_can_skip_startup_warm_cache(monkeypatch, tmp_path) -> None:
    called = False

    def fake_warm_cache(settings) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr("btc_dashboard.app.warm_local_cache", fake_warm_cache)

    create_app(_settings(tmp_path, warm_local_cache_on_startup=False))

    assert called is False


def test_api_responses_disable_browser_cache(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("btc_dashboard.app.warm_local_cache", lambda settings: None)
    app = create_app(_settings(tmp_path))

    response = app.test_client().get("/api/signals")

    assert response.headers["Cache-Control"] == "no-store, max-age=0"


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


def test_seo_static_routes_stay_public_when_auth_is_enabled(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("btc_dashboard.app.warm_local_cache", lambda settings: None)
    app = create_app(_settings(tmp_path, dashboard_api_token="token-123"))

    robots = app.test_client().get("/robots.txt")
    sitemap = app.test_client().get("/sitemap.xml")

    assert robots.status_code == 200
    assert "Sitemap: https://btcwindow.uk/sitemap.xml" in robots.text
    assert "Disallow: /api/" in robots.text
    assert sitemap.status_code == 200
    assert "<loc>https://btcwindow.uk/</loc>" in sitemap.text


def test_railway_host_redirects_to_canonical_domain(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("btc_dashboard.app.warm_local_cache", lambda settings: None)
    app = create_app(_settings(tmp_path))

    response = app.test_client().get(
        "/api/metrics?source=railway",
        headers={"Host": "btcwindow.up.railway.app"},
    )

    assert response.status_code == 308
    assert response.headers["Location"] == "https://btcwindow.uk/api/metrics?source=railway"


def test_health_check_does_not_redirect_on_railway_host(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("btc_dashboard.app.warm_local_cache", lambda settings: None)
    app = create_app(_settings(tmp_path))

    response = app.test_client().get("/health", headers={"Host": "btcwindow.up.railway.app"})

    assert response.status_code == 200


def test_etf_api_does_not_redirect_on_railway_host(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("btc_dashboard.app.warm_local_cache", lambda settings: None)
    monkeypatch.setattr(
        "btc_dashboard.routes.get_etf_flow",
        lambda settings: {
            "latest_date": "2026-05-22",
            "latest_net_flow_usd": -36_300_000,
            "flow_history": [{"date": "2026-05-22", "net_flow_usd": -36_300_000}],
        },
    )
    app = create_app(_settings(tmp_path))

    response = app.test_client().get("/api/etf", headers={"Host": "btcwindow.up.railway.app"})

    assert response.status_code == 200
    assert "Location" not in response.headers
    assert response.get_json()["latest_date"] == "2026-05-22"


def test_etf_admin_update_does_not_redirect_on_railway_host(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("btc_dashboard.app.warm_local_cache", lambda settings: None)
    app = create_app(_settings(tmp_path, etf_admin_token="secret-token"))

    response = app.test_client().post(
        "/api/admin/etf-flows",
        headers={
            "Authorization": "Bearer secret-token",
            "Host": "btcwindow.up.railway.app",
        },
        json={
            "source": "manual",
            "updated_at": "2026-05-17T00:00:00Z",
            "flow_history": [{"date": "2026-05-15", "net_flow_usd": -290_400_000}],
        },
    )

    assert response.status_code == 200
    assert "Location" not in response.headers


def test_index_records_viewer_stats(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("btc_dashboard.app.warm_local_cache", lambda settings: None)
    app = create_app(_settings(tmp_path))

    response = app.test_client().get("/", headers={"User-Agent": "pytest-browser"})

    assert response.status_code == 200
    viewer_response = app.test_client().get("/api/viewers")
    assert viewer_response.status_code == 200
    assert viewer_response.get_json()["total_views"] == 1


def test_viewer_analytics_endpoint_reports_aggregate_sources(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("btc_dashboard.app.warm_local_cache", lambda settings: None)
    app = create_app(_settings(tmp_path))
    client = app.test_client()

    response = client.get(
        "/",
        headers={
            "User-Agent": "Mozilla/5.0 (iPhone) AppleWebKit/605.1.15 Safari/604.1",
            "Referer": "https://x.com/BitcoinWindow",
            "CF-Connecting-IP": "203.0.113.9",
            "CF-IPCountry": "TH",
            "Accept-Language": "th-TH,th;q=0.9",
        },
    )
    second_response = client.get(
        "/",
        headers={
            "User-Agent": "Mozilla/5.0 (iPhone) AppleWebKit/605.1.15 Safari/604.1",
            "Referer": "https://www.tiktok.com/@btcwindow/video/123",
            "CF-Connecting-IP": "203.0.113.10",
            "CF-IPCountry": "TH",
            "Accept-Language": "th-TH,th;q=0.9",
        },
    )
    analytics_response = client.get("/api/viewer-analytics")

    assert response.status_code == 200
    assert second_response.status_code == 200
    assert analytics_response.status_code == 200
    body = analytics_response.get_json()
    assert body["sources"]["x"] == 1
    assert body["sources"]["tiktok"] == 1
    assert body["unique_today"] == 2
    assert body["unique_7d"] == 2
    assert body["returning_visitors"] == 0
    assert body["returning_rate"] == "0.0%"
    assert body["devices"]["mobile"] == 2
    assert body["countries"]["TH"] == 2
    assert "privacy" in body
    assert "203.0.113" not in json.dumps(body)


def test_index_records_utm_source_when_referrer_is_missing(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("btc_dashboard.app.warm_local_cache", lambda settings: None)
    app = create_app(_settings(tmp_path))
    client = app.test_client()

    response = client.get(
        "/?utm_source=youtube",
        headers={
            "User-Agent": "Mozilla/5.0 Chrome/124",
            "CF-Connecting-IP": "203.0.113.20",
            "CF-IPCountry": "TH",
        },
    )
    analytics_response = client.get("/api/viewer-analytics")

    assert response.status_code == 200
    assert analytics_response.status_code == 200
    body = analytics_response.get_json()
    assert body["sources"] == {"youtube": 1}
    assert body["referrers"] == {"direct": 1}
    assert "203.0.113" not in json.dumps(body)


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


def test_fear_greed_route_returns_stable_json_when_service_fails(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("btc_dashboard.app.warm_local_cache", lambda settings: None)
    monkeypatch.setattr(
        "btc_dashboard.routes.get_fear_greed_index",
        lambda settings: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    app = create_app(_settings(tmp_path))

    response = app.test_client().get("/api/fear-greed")

    assert response.status_code == 200
    body = response.get_json()
    assert body["value"] == "N/A"
    assert body["classification"] == "N/A"
    assert body["historical"] == {}
    assert body["source_label"] == "Alternative.me"
    assert body["status"] == "error"
    assert body["error"] == "boom"


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


def test_etf_admin_update_requires_admin_token(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("btc_dashboard.app.warm_local_cache", lambda settings: None)
    app = create_app(_settings(tmp_path, etf_admin_token="secret-token"))

    response = app.test_client().post(
        "/api/admin/etf-flows",
        json={"source": "manual", "updated_at": "2026-05-17T00:00:00Z", "flow_history": []},
    )

    assert response.status_code == 401


def test_etf_admin_update_uses_separate_token_when_dashboard_auth_enabled(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr("btc_dashboard.app.warm_local_cache", lambda settings: None)
    app = create_app(
        _settings(
            tmp_path,
            dashboard_api_token="dashboard-token",
            etf_admin_token="secret-token",
            etf_flow_path=tmp_path / "etf_flows.json",
        ),
    )

    response = app.test_client().post(
        "/api/admin/etf-flows",
        headers={"Authorization": "Bearer secret-token"},
        json={
            "source": "manual",
            "updated_at": "2026-05-17T00:00:00Z",
            "flow_history": [{"date": "2026-05-15", "net_flow_usd": -290_400_000}],
        },
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["ok"] is True
    assert body["source"] == "manual"
    assert body["latest_date"] == "2026-05-15"
    assert body["latest_net_flow_usd"] == -290_400_000.0


def test_etf_admin_update_rejects_invalid_payload(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("btc_dashboard.app.warm_local_cache", lambda settings: None)
    app = create_app(_settings(tmp_path, etf_admin_token="secret-token"))

    response = app.test_client().post(
        "/api/admin/etf-flows",
        headers={"Authorization": "Bearer secret-token"},
        json={
            "source": "live",
            "updated_at": "2026-05-17T00:00:00Z",
            "flow_history": [{"date": "2026-05-15", "net_flow_usd": -290_400_000}],
        },
    )

    assert response.status_code == 400
    assert response.get_json()["ok"] is False


def test_price_route_returns_change_fields(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("btc_dashboard.app.warm_local_cache", lambda settings: None)
    monkeypatch.setattr(
        "btc_dashboard.routes.get_btc_price_result",
        lambda settings: MetricValue(91234.56, "binance", 1234.56, 1.57),
    )
    with state.lock:
        state.btc_price = None
        state.btc_change_24h_usd = None
        state.btc_change_24h_percent = None
        state.btc_price_source = "unknown"
        state.btc_price_is_cached = True
        state.price_points.clear()
        state.metric_timestamps.pop("price", None)
    app = create_app(_settings(tmp_path))

    response = app.test_client().get("/api/price")

    assert response.status_code == 200
    body = response.get_json()
    assert body["price"] == 91234.56
    assert body["change_24h_usd"] == 1234.56
    assert body["change_24h_percent"] == 1.57
    assert body["updated_at"]
    assert body["source"] == "binance"
    assert body["is_cached"] is False


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
    assert body["daily_limit_remaining"] == 1
    assert body["last_post_date"] is None
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
    assert body["max_posts_per_day"] == 1
    assert body["daily_post_hour"] == 9


def test_api_signals_returns_signal_engine_payload(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("btc_dashboard.app.warm_local_cache", lambda settings: None)
    monkeypatch.setattr(
        "btc_dashboard.routes.latest_signals",
        lambda settings: {
            "signals": [],
            "x_posting_enabled": False,
            "cooldown_seconds": 3600,
            "dashboard_url": "https://btcwindow.uk/",
            "post_state": {"posted_key_count": 0, "last_normal_posted_at": 0},
        },
    )
    app = create_app(_settings(tmp_path))

    response = app.test_client().get("/api/signals")

    assert response.status_code == 200
    body = response.get_json()
    assert body["signals"] == []
    assert body["x_posting_enabled"] is False
    assert body["cooldown_seconds"] == 3600


def test_api_signals_returns_safe_fallback_on_error(monkeypatch, tmp_path) -> None:
    def fail_signals(settings):
        raise RuntimeError("cache unavailable")

    monkeypatch.setattr("btc_dashboard.app.warm_local_cache", lambda settings: None)
    monkeypatch.setattr("btc_dashboard.routes.latest_signals", fail_signals)
    app = create_app(_settings(tmp_path))

    response = app.test_client().get("/api/signals")

    assert response.status_code == 200
    body = response.get_json()
    assert body["signals"] == []
    assert body["x_posting_enabled"] is False
    assert body["error"] == "cache unavailable"


def test_api_alert_returns_recent_alert_history(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("btc_dashboard.app.warm_local_cache", lambda settings: None)
    monkeypatch.setattr("btc_dashboard.routes.get_recent_whale_transactions", lambda settings: [])
    monkeypatch.setattr(
        "btc_dashboard.routes.build_alerts",
        lambda *args, **kwargs: [
            {
                "type": "fee_spike",
                "severity": "high",
                "status": "red",
                "message": "Fee Spike: 10 sat/vB",
                "action": "Wait if not urgent",
                "height": "100",
            }
        ],
    )
    app = create_app(_settings(tmp_path))

    response = app.test_client().get("/api/alert")

    assert response.status_code == 200
    body = response.get_json()
    assert body["alerts"][0]["message"] == "Fee Spike: 10 sat/vB"
    assert body["recent_alerts"][0]["message"] == "Fee Spike: 10 sat/vB"
    assert "recorded_at" in body["recent_alerts"][0]


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


def test_ownership_route_returns_stable_json(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("btc_dashboard.app.warm_local_cache", lambda settings: None)
    monkeypatch.setattr(
        "btc_dashboard.routes.get_btc_supply_ownership",
        lambda settings: {
            "circulating_supply": 19_800_000,
            "max_supply": 21_000_000,
            "remaining_to_mine": 1_200_000,
            "percent_mined": 94.29,
            "estimated_lost_btc": {"low": 3_000_000, "high": 4_000_000},
            "effective_liquid_supply": {"low": 15_800_000, "high": 16_800_000},
            "categories": [
                {
                    "name": "Satoshi Nakamoto estimate",
                    "btc": 1_100_000,
                    "percent": 5.56,
                    "source_type": "Research estimate",
                    "confidence": "medium",
                    "estimated": True,
                }
            ],
            "chart_categories": [],
            "insights": ["Mining scarcity: about 1,200,000 BTC remain."],
            "updated_at": "2026-05-06T00:00:00Z",
            "status": "ok",
        },
    )
    app = create_app(_settings(tmp_path))

    response = app.test_client().get("/api/ownership")

    assert response.status_code == 200
    body = response.get_json()
    assert body["categories"][0]["estimated"] is True
    assert body["remaining_to_mine"] == 1_200_000


def test_frontend_renders_ownership_categories_and_insights() -> None:
    js = Path("btc_dashboard/static/dashboard.js").read_text(encoding="utf-8")
    html = Path("btc_dashboard/templates/dashboard.html").read_text(encoding="utf-8")

    assert "/api/ownership" in js
    assert "supplyInsightCards" in js
    assert "source_type" in js
    assert "confidence" in js
    assert "display_btc" in js
    assert "chart_categories" in js
    assert "Limited visibility" in js
    assert "renderEtfFlowNote" in js
    assert "ETF flow history is using fallback estimate data. Live data unavailable." in js
    assert "etfChartRows" in js
    assert "Latest:" in js
    assert 'startRefreshJob("btc-price-card", refreshBtcPriceCard, 5000)' in js
    assert 'startRefreshJob("btc-price-chart", refreshPriceChart, 60000)' in js
    assert 'startRefreshJob("mempool-metrics", refreshMempoolMetrics, 30000)' in js
    assert 'startRefreshJob("hashrate", refreshHashrateMetrics, 10 * 60 * 1000)' in js
    assert 'startRefreshJob("node-count", refreshNodeMetrics, 30 * 60 * 1000)' in js
    assert 'startRefreshJob("institutional", refreshInstitutionalMetrics, 60 * 60 * 1000)' in js
    assert "supplyInsightCards" in html
    assert "etfFlowNote" in html
    assert "Effective liquid supply" in html
    assert "Recent Alerts" in html
    assert "recentAlertBox" in html
    assert "Market Cap" in html
    assert "btcMarketCap" in html
    assert "Network Health" in html
    assert "Security" in html
    assert "networkSecuritySummary" in html
    assert "dashboard.js" in html
    assert "20260602-1" in html
    assert "BTC Window | Bitcoin Fees, ETF Flow & Network Health" in html
    assert 'rel="canonical"' in html
    assert 'property="og:title"' in html
    assert 'property="og:image"' in html
    assert 'name="twitter:card"' in html
    assert 'name="twitter:image"' in html
    assert 'type="application/ld+json"' in html
    assert "FinanceApplication" in html


def test_frontend_includes_generational_wealth_branding_asset() -> None:
    html = Path("btc_dashboard/templates/dashboard.html").read_text(encoding="utf-8")
    asset = Path("btc_dashboard/static/generational-mascot.webp")

    assert "Built for Generational Wealth." in html
    assert "Real-Time Bitcoin Intelligence for Long-Term Holders." in html
    assert "BTC Window — Built for Generational Wealth." in html
    assert "generational-mascot.webp" in html
    assert "Animation-style child mascot" in html
    assert asset.exists()
    assert asset.stat().st_size < 150_000


def test_mvrv_api_endpoints_return_clean_json(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("btc_dashboard.app.warm_local_cache", lambda settings: None)
    monkeypatch.setattr(
        "btc_dashboard.routes.get_mvrv_summary",
        lambda settings: {
            "value": 2.15,
            "zone": "Neutral / Warm",
            "description": (
                "Market value is above realized value but below historical overheated levels."
            ),
            "source": "CoinMetrics",
            "updated_at": "2026-06-05T10:00:00Z",
        },
    )
    monkeypatch.setattr(
        "btc_dashboard.routes.get_mvrv_history",
        lambda settings: {
            "source": "CoinMetrics",
            "data": [{"date": "2024-06-01", "mvrv": 2.35}],
        },
    )
    app = create_app(_settings(tmp_path))
    client = app.test_client()

    summary = client.get("/api/mvrv").get_json()
    history = client.get("/api/mvrv/history").get_json()

    assert summary["value"] == 2.15
    assert summary["zone"] == "Neutral / Warm"
    assert "api" not in json.dumps(summary).lower()
    assert history == {
        "source": "CoinMetrics",
        "data": [{"date": "2024-06-01", "mvrv": 2.35}],
    }


def test_mvrv_frontend_lazy_chart_is_wired() -> None:
    js = Path("btc_dashboard/static/dashboard.js").read_text(encoding="utf-8")
    html = Path("btc_dashboard/templates/dashboard.html").read_text(encoding="utf-8")

    assert "Bitcoin MVRV Ratio" in html
    assert "mvrvChartPanel" in html
    assert "▼ Show Historical Chart" in html
    assert 'fetchJson("/api/mvrv")' in js
    assert 'fetchJson("/api/mvrv/history")' in js
    assert "loadMvrvHistoryOnce()" in js
    assert "mvrvHistoryLoaded" in js
    assert "mvrv_chart_open" in js
    assert "mvrv_chart_close" in js
    assert "MVRV chart is temporarily unavailable." in js
