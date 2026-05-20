from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from typing import Any

import requests

from btc_dashboard import services
from btc_dashboard.config import Settings

DEFAULT_BASE_URL = "https://btcwindow.up.railway.app"
ADMIN_ETF_PATH = "/api/admin/etf-flows"

LIVE_SOURCE_LOADERS = (
    ("sosovalue", "_get_etf_flow_from_sosovalue", "sosovalue_api_key"),
    ("coinglass", "_get_etf_flow_from_coinglass", "coinglass_api_key"),
    ("farside-latest", "_get_etf_flow_from_farside_latest", None),
    ("farside", "_get_etf_flow_from_farside", None),
    ("bitbo", "_get_etf_flow_from_bitbo", None),
)


class EtfUpdateError(RuntimeError):
    pass


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def is_live_etf_payload(payload: dict[str, Any]) -> bool:
    if payload.get("source") in {"fallback", "manual"}:
        return False
    if payload.get("is_fallback"):
        return False
    history = payload.get("flow_history")
    if not isinstance(history, list) or not history:
        return False
    latest_date = str(payload.get("latest_date") or "").strip()
    return bool(latest_date)


def fetch_live_etf_flow(settings: Settings) -> dict[str, Any]:
    failures: list[str] = []
    for source_name, loader_name, required_key in LIVE_SOURCE_LOADERS:
        if required_key and not getattr(settings, required_key):
            failures.append(f"{source_name}: skipped; API key is not configured")
            continue
        loader = getattr(services, loader_name)
        payload = loader(settings)
        if is_live_etf_payload(payload):
            return payload
        failures.append(f"{source_name}: {payload.get('error') or 'no usable live rows'}")
    raise EtfUpdateError("No live ETF flow source returned usable rows. " + " | ".join(failures))


def build_manual_payload(
    etf_payload: dict[str, Any],
    *,
    updated_at: str | None = None,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for row in etf_payload.get("flow_history") or []:
        if not isinstance(row, dict):
            continue
        try:
            net_flow_usd = float(row["net_flow_usd"])
        except (KeyError, TypeError, ValueError):
            continue
        clean_row: dict[str, Any] = {
            "date": str(row.get("date") or "").strip(),
            "net_flow_usd": net_flow_usd,
        }
        if row.get("close_price") not in (None, "", "N/A"):
            try:
                clean_row["close_price"] = float(row["close_price"])
            except (TypeError, ValueError):
                pass
        if clean_row["date"]:
            rows.append(clean_row)

    if not rows:
        raise EtfUpdateError("Live ETF payload had no valid numeric flow rows.")

    return {
        "source": "manual",
        "updated_at": updated_at or utc_now_iso(),
        "flow_history": rows[-30:],
    }


def get_current_latest_date(base_url: str, timeout: int) -> str | None:
    response = requests.get(f"{base_url.rstrip('/')}/api/etf", timeout=timeout)
    response.raise_for_status()
    data = response.json()
    return str(data.get("latest_date") or "")


def post_admin_payload(
    base_url: str,
    token: str,
    payload: dict[str, Any],
    timeout: int,
) -> dict[str, Any]:
    clean_token = "".join(ch for ch in token if ch >= " " and ch != "\x7f").strip()
    if not clean_token:
        raise EtfUpdateError("ETF_ADMIN_TOKEN is required for posting ETF updates.")

    response = requests.post(
        f"{base_url.rstrip('/')}{ADMIN_ETF_PATH}",
        headers={"Authorization": f"Bearer {clean_token}"},
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise EtfUpdateError(str(data.get("error") or "ETF admin update was rejected."))
    return data


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update BTC Window manual ETF flow data.")
    parser.add_argument("--base-url", default=os.getenv("BTCWINDOW_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--dry-run", action="store_true", help="Print payload without posting.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Post even when production already has the same latest ETF date.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=int(os.getenv("REQUEST_TIMEOUT_SECONDS", "15")),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    settings = Settings.from_env()
    live_payload = fetch_live_etf_flow(settings)
    admin_payload = build_manual_payload(live_payload)
    latest_date = admin_payload["flow_history"][-1]["date"]

    if args.dry_run:
        print(json.dumps(admin_payload, indent=2))
        return 0

    try:
        current_latest_date = get_current_latest_date(args.base_url, args.timeout)
        if current_latest_date == latest_date and not args.force:
            print(f"ETF flow already up to date at {latest_date}; skipping admin update.")
            return 0
    except requests.RequestException as exc:
        print(f"Current ETF check failed; continuing with admin update: {exc}", file=sys.stderr)

    result = post_admin_payload(
        args.base_url,
        os.getenv("ETF_ADMIN_TOKEN", ""),
        admin_payload,
        args.timeout,
    )
    print(
        "ETF flow updated: "
        f"{result.get('latest_date')} {result.get('latest_net_flow_usd')} "
        f"({result.get('source_label')})",
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (EtfUpdateError, requests.RequestException) as exc:
        print(f"ETF update failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
