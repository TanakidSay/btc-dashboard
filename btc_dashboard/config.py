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


@dataclass(frozen=True)
class Settings:
    secret_key: str
    fee_csv_path: Path
    dashboard_username: str | None = None
    dashboard_password: str | None = None
    dashboard_api_token: str | None = None
    bitcoin_rpc_url: str = "http://127.0.0.1:8332"
    bitcoin_rpc_user: str = "bitcoinuser"
    bitcoin_rpc_password: str | None = None
    bitcoin_block_reward_btc: float = 3.125
    refresh_seconds: int = 10
    request_timeout: int = 5
    cache_ttl_seconds: int = 30
    node_block_count: int = 10
    max_csv_rows: int = 100
    max_table_rows: int = 20
    max_chart_rows: int = 50
    fee_spike_threshold: float = 5
    price_breakout_lookback: int = 10
    notification_webhook_url: str | None = None
    notification_cooldown_seconds: int = 300
    start_worker: bool = True
    coinglass_api_key: str | None = None
    coingecko_demo_api_key: str | None = None

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

        return cls(
            secret_key=os.getenv("SECRET_KEY", "dev-only-change-me"),
            fee_csv_path=csv_path,
            dashboard_username=os.getenv("DASHBOARD_USERNAME") or None,
            dashboard_password=os.getenv("DASHBOARD_PASSWORD") or None,
            dashboard_api_token=os.getenv("DASHBOARD_API_TOKEN") or None,
            bitcoin_rpc_url=os.getenv("BITCOIN_RPC_URL", "http://127.0.0.1:8332"),
            bitcoin_rpc_user=os.getenv("BITCOIN_RPC_USER", "bitcoinuser"),
            bitcoin_rpc_password=os.getenv("BITCOIN_RPC_PASSWORD") or None,
            bitcoin_block_reward_btc=float(os.getenv("BITCOIN_BLOCK_REWARD_BTC", "3.125")),
            refresh_seconds=int(os.getenv("DASHBOARD_REFRESH_SECONDS", "10")),
            request_timeout=int(os.getenv("REQUEST_TIMEOUT_SECONDS", "5")),
            cache_ttl_seconds=int(os.getenv("CACHE_TTL_SECONDS", "30")),
            node_block_count=int(os.getenv("NODE_BLOCK_COUNT", "10")),
            fee_spike_threshold=float(os.getenv("FEE_SPIKE_THRESHOLD", "5")),
            notification_webhook_url=os.getenv("NOTIFICATION_WEBHOOK_URL") or None,
            notification_cooldown_seconds=int(os.getenv("NOTIFICATION_COOLDOWN_SECONDS", "300")),
            start_worker=_bool_from_env(
                "START_WORKER",
                _bool_from_env("DASHBOARD_START_WORKER", True),
            ),
            coinglass_api_key=os.getenv("COINGLASS_API_KEY") or None,
            coingecko_demo_api_key=os.getenv("COINGECKO_DEMO_API_KEY") or None,
        )
