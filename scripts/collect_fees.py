from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path
from typing import Any

import requests
from requests.auth import HTTPBasicAuth

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dependency is declared, fallback helps bare local runs.
    load_dotenv = None


BASE_DIR = Path(__file__).resolve().parent.parent


def rpc_call(
    method: str,
    params: list[Any],
    rpc_url: str,
    rpc_user: str,
    rpc_password: str,
    timeout: int,
) -> Any:
    payload = {
        "jsonrpc": "1.0",
        "id": "btc-dashboard",
        "method": method,
        "params": params,
    }
    response = requests.post(
        rpc_url,
        json=payload,
        auth=HTTPBasicAuth(rpc_user, rpc_password),
        timeout=timeout,
    )
    response.raise_for_status()
    body = response.json()
    if body.get("error"):
        raise RuntimeError(body["error"])
    return body["result"]


def collect_blocks(
    block_count: int,
    rpc_url: str,
    rpc_user: str,
    rpc_password: str,
    block_reward: float,
    timeout: int,
) -> list[dict[str, float | int]]:
    current_hash = rpc_call("getbestblockhash", [], rpc_url, rpc_user, rpc_password, timeout)
    results: list[dict[str, float | int]] = []

    for _ in range(block_count):
        block = rpc_call("getblock", [current_hash, 2], rpc_url, rpc_user, rpc_password, timeout)
        coinbase_tx = block["tx"][0]
        coinbase_output = sum(vout["value"] for vout in coinbase_tx["vout"])

        total_fee_btc = coinbase_output - block_reward
        total_fee_sat = total_fee_btc * 100_000_000
        block_size = block["size"]

        results.append(
            {
                "height": block["height"],
                "tx_count": len(block["tx"]),
                "total_fee_btc": total_fee_btc,
                "sat_per_vbyte": total_fee_sat / block_size if block_size > 0 else 0,
            }
        )
        current_hash = block["previousblockhash"]

    return results


def append_csv(path: Path, rows: list[dict[str, float | int]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    file_exists = path.exists()

    with path.open(mode="a", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=["height", "tx_count", "total_fee_btc", "sat_per_vbyte"],
        )
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Append Bitcoin Core fee data to CSV.")
    parser.add_argument(
        "--blocks",
        type=int,
        default=10,
        help="Number of recent blocks to collect.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(os.getenv("BITCOIN_FEE_CSV", "data/bitcoin_fee_data.csv")),
        help="CSV output path.",
    )
    return parser.parse_args()


def main() -> None:
    if load_dotenv:
        load_dotenv(BASE_DIR / ".env")

    args = parse_args()
    rpc_url = os.getenv("BITCOIN_RPC_URL", "http://127.0.0.1:8332")
    rpc_user = os.getenv("BITCOIN_RPC_USER", "bitcoinuser")
    rpc_password = os.getenv("BITCOIN_RPC_PASSWORD")
    block_reward = float(os.getenv("BITCOIN_BLOCK_REWARD_BTC", "3.125"))
    timeout = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "5"))

    if not rpc_password:
        raise SystemExit("BITCOIN_RPC_PASSWORD is required.")

    output = args.output if args.output.is_absolute() else BASE_DIR / args.output
    rows = collect_blocks(args.blocks, rpc_url, rpc_user, rpc_password, block_reward, timeout)
    append_csv(output, rows)

    print(f"Saved {len(rows)} rows to {output}")


if __name__ == "__main__":
    main()
