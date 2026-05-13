from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dependency is declared, fallback helps bare local runs.
    load_dotenv = None


BASE_DIR = Path(__file__).resolve().parent.parent


def _bool_from_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _tuple_from_env(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    value = os.getenv(name)
    if value is None:
        return default
    return tuple(item.strip() for item in value.split(",") if item.strip())


@dataclass(frozen=True)
class Settings:
    secret_key: str
    fee_csv_path: Path
    viewer_stats_path: Path = BASE_DIR / "data/viewer_stats.json"
    viewer_analytics_path: Path = BASE_DIR / "data/viewer_analytics.json"
    view_counter_path: Path = BASE_DIR / "data/view_counter.json"
    etf_flow_path: Path = BASE_DIR / "data/etf_flows.json"
    btc_price_baseline_path: Path = BASE_DIR / "data/btc_price_baseline.json"
    dashboard_username: str | None = None
    dashboard_password: str | None = None
    dashboard_api_token: str | None = None
    bitcoin_rpc_url: str = "http://127.0.0.1:8332"
    bitcoin_rpc_user: str = "bitcoinuser"
    bitcoin_rpc_password: str | None = None
    bitcoin_block_reward_btc: float = 3.125
    refresh_seconds: int = 5
    request_timeout: int = 5
    cache_ttl_seconds: int = 30
    etf_flow_ttl_seconds: int = 12 * 60 * 60
    view_counter_initial_total: int = 0
    viewer_stats_initial_unique: int = 0
    node_block_count: int = 10
    max_csv_rows: int = 100
    max_table_rows: int = 20
    max_chart_rows: int = 50
    fee_spike_threshold: float = 5
    whale_alert_threshold_btc: float = 100
    price_breakout_lookback: int = 10
    notification_webhook_url: str | None = None
    notification_cooldown_seconds: int = 300
    enable_x_posting: bool = False
    enable_x_test_post: bool = False
    x_daily_post_hour: int = 9
    x_api_key: str | None = None
    x_api_secret: str | None = None
    x_access_token: str | None = None
    x_access_secret: str | None = None
    x_signal_state_path: Path = BASE_DIR / "data/x_signal_state.json"
    x_posted_events_path: Path = BASE_DIR / "data/posted_events.json"
    start_worker: bool = True
    coinglass_api_key: str | None = None
    coingecko_demo_api_key: str | None = None
    sosovalue_api_key: str | None = None
    canonical_host: str = "btcwindow.uk"
    canonical_redirect_hosts: tuple[str, ...] = ("btcwindow.up.railway.app",)

    @property
    def dashboard_auth_enabled(self) -> bool:
        return bool(
            self.dashboard_api_token
            or (self.dashboard_username and self.dashboard_password)
        )

    @classmethod
    def from_env(cls) -> Settings:
        if load_dotenv:
            load_dotenv(BASE_DIR / ".env")

        csv_path = Path(os.getenv("BITCOIN_FEE_CSV", "data/bitcoin_fee_data.csv"))
        if not csv_path.is_absolute():
            csv_path = BASE_DIR / csv_path
        viewer_stats_path = Path(os.getenv("VIEWER_STATS_FILE", "data/viewer_stats.json"))
        if not viewer_stats_path.is_absolute():
            viewer_stats_path = BASE_DIR / viewer_stats_path
        viewer_analytics_path = Path(
            os.getenv("VIEWER_ANALYTICS_FILE", "data/viewer_analytics.json"),
        )
        if not viewer_analytics_path.is_absolute():
            viewer_analytics_path = BASE_DIR / viewer_analytics_path
        view_counter_path = Path(os.getenv("VIEW_COUNTER_FILE", "data/view_counter.json"))
        if not view_counter_path.is_absolute():
            view_counter_path = BASE_DIR / view_counter_path
        etf_flow_path = Path(os.getenv("ETF_FLOW_FILE", "data/etf_flows.json"))
        if not etf_flow_path.is_absolute():
            etf_flow_path = BASE_DIR / etf_flow_path
        btc_price_baseline_path = Path(
            os.getenv("BTC_PRICE_BASELINE_FILE", "data/btc_price_baseline.json"),
        )
        if not btc_price_baseline_path.is_absolute():
            btc_price_baseline_path = BASE_DIR / btc_price_baseline_path
        x_signal_state_path = Path(os.getenv("X_SIGNAL_STATE_FILE", "data/x_signal_state.json"))
        if not x_signal_state_path.is_absolute():
            x_signal_state_path = BASE_DIR / x_signal_state_path
        x_posted_events_path = Path(os.getenv("X_POSTED_EVENTS_FILE", "data/posted_events.json"))
        if not x_posted_events_path.is_absolute():
            x_posted_events_path = BASE_DIR / x_posted_events_path

        return cls(
            secret_key=os.getenv("SECRET_KEY", "dev-only-change-me"),
            fee_csv_path=csv_path,
            viewer_stats_path=viewer_stats_path,
            viewer_analytics_path=viewer_analytics_path,
            view_counter_path=view_counter_path,
            etf_flow_path=etf_flow_path,
            btc_price_baseline_path=btc_price_baseline_path,
            dashboard_username=os.getenv("DASHBOARD_USERNAME") or None,
            dashboard_password=os.getenv("DASHBOARD_PASSWORD") or None,
            dashboard_api_token=os.getenv("DASHBOARD_API_TOKEN") or None,
            bitcoin_rpc_url=os.getenv("BITCOIN_RPC_URL", "http://127.0.0.1:8332"),
            bitcoin_rpc_user=os.getenv("BITCOIN_RPC_USER", "bitcoinuser"),
            bitcoin_rpc_password=os.getenv("BITCOIN_RPC_PASSWORD") or None,
            bitcoin_block_reward_btc=float(os.getenv("BITCOIN_BLOCK_REWARD_BTC", "3.125")),
            refresh_seconds=int(os.getenv("DASHBOARD_REFRESH_SECONDS", "5")),
            request_timeout=int(os.getenv("REQUEST_TIMEOUT_SECONDS", "5")),
            cache_ttl_seconds=int(os.getenv("CACHE_TTL_SECONDS", "30")),
            etf_flow_ttl_seconds=int(os.getenv("ETF_FLOW_TTL_SECONDS", str(12 * 60 * 60))),
            view_counter_initial_total=int(os.getenv("VIEW_COUNTER_INITIAL_TOTAL", "0")),
            viewer_stats_initial_unique=int(os.getenv("VIEWER_STATS_INITIAL_UNIQUE", "0")),
            node_block_count=int(os.getenv("NODE_BLOCK_COUNT", "10")),
            fee_spike_threshold=float(os.getenv("FEE_SPIKE_THRESHOLD", "5")),
            whale_alert_threshold_btc=float(os.getenv("WHALE_ALERT_THRESHOLD_BTC", "100")),
            notification_webhook_url=os.getenv("NOTIFICATION_WEBHOOK_URL") or None,
            notification_cooldown_seconds=int(os.getenv("NOTIFICATION_COOLDOWN_SECONDS", "300")),
            enable_x_posting=_bool_from_env("ENABLE_X_POSTING", False),
            enable_x_test_post=_bool_from_env("ENABLE_X_TEST_POST", False),
            x_daily_post_hour=int(os.getenv("X_DAILY_POST_HOUR", "9")),
            x_api_key=os.getenv("X_API_KEY") or None,
            x_api_secret=os.getenv("X_API_SECRET") or None,
            x_access_token=os.getenv("X_ACCESS_TOKEN") or None,
            x_access_secret=os.getenv("X_ACCESS_SECRET") or None,
            x_signal_state_path=x_signal_state_path,
            x_posted_events_path=x_posted_events_path,
            start_worker=_bool_from_env(
                "START_WORKER",
                _bool_from_env("DASHBOARD_START_WORKER", True),
            ),
            coinglass_api_key=os.getenv("COINGLASS_API_KEY") or None,
            coingecko_demo_api_key=os.getenv("COINGECKO_DEMO_API_KEY") or None,
            sosovalue_api_key=os.getenv("SOSOVALUE_API_KEY") or None,
            canonical_host=os.getenv("CANONICAL_HOST", "btcwindow.uk").strip(),
            canonical_redirect_hosts=_tuple_from_env(
                "CANONICAL_REDIRECT_HOSTS",
                ("btcwindow.up.railway.app",),
            ),
        )
