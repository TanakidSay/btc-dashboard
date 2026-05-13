from __future__ import annotations

import logging
import os
import sys
from hmac import compare_digest
from pathlib import Path

from flask import Flask, Response, redirect, request

if __package__:
    from .config import Settings
    from .routes import api
    from .services import configure_state
    from .worker import start_background_worker, warm_local_cache
else:  # pragma: no cover - supports running this file directly.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from btc_dashboard.config import Settings
    from btc_dashboard.routes import api
    from btc_dashboard.services import configure_state
    from btc_dashboard.worker import start_background_worker, warm_local_cache


def create_app(settings: Settings | None = None) -> Flask:
    settings = settings or Settings.from_env()

    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=settings.secret_key,
        DASHBOARD_SETTINGS=settings,
    )

    @app.before_request
    def redirect_to_canonical_host():
        if request.endpoint in {"api.healthz", "static"}:
            return None
        if not _should_redirect_to_canonical_host(settings):
            return None

        path = request.full_path.rstrip("?")
        return redirect(f"https://{settings.canonical_host}{path}", code=308)

    @app.before_request
    def require_dashboard_auth():
        if request.endpoint == "api.healthz" or not settings.dashboard_auth_enabled:
            return None

        if _is_authorized_request(settings):
            return None

        return Response(
            "Authentication required",
            401,
            {"WWW-Authenticate": 'Basic realm="Bitcoin Dashboard"'},
        )

    @app.after_request
    def add_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=()",
        )
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self' https://cdn.tailwindcss.com https://cdn.jsdelivr.net "
            "'unsafe-inline' 'unsafe-eval'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "font-src 'self' data:; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'",
        )
        return response

    app.register_blueprint(api)

    configure_state(settings)
    warm_local_cache(settings)

    if settings.start_worker:
        start_background_worker(settings)

    return app


def _should_redirect_to_canonical_host(settings: Settings) -> bool:
    canonical_host = (settings.canonical_host or "").strip().lower()
    if not canonical_host:
        return False

    current_host = request.host.split(":", 1)[0].strip().lower()
    redirect_hosts = {host.strip().lower() for host in settings.canonical_redirect_hosts}
    return current_host in redirect_hosts and current_host != canonical_host


def _is_authorized_request(settings: Settings) -> bool:
    bearer_prefix = "Bearer "
    authorization = request.headers.get("Authorization", "")
    if settings.dashboard_api_token and authorization.startswith(bearer_prefix):
        token = authorization.removeprefix(bearer_prefix).strip()
        if compare_digest(token, settings.dashboard_api_token):
            return True

    basic_auth = request.authorization
    if not basic_auth or not settings.dashboard_username or not settings.dashboard_password:
        return False

    username_matches = compare_digest(
        basic_auth.username or "",
        settings.dashboard_username,
    )
    password_matches = compare_digest(
        basic_auth.password or "",
        settings.dashboard_password,
    )
    return username_matches and password_matches


def run_dev_server() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "5000"))
    create_app().run(host=host, port=port)


if __name__ == "__main__":
    run_dev_server()
