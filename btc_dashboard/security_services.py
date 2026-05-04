from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)


def get_double_spend_attempts(rpc_call_fn, settings) -> dict[str, Any]:
    """
    Detect potential double-spend attempts by monitoring orphaned/stale blocks.
    Uses getchaintips RPC to find forks in the chain.
    """
    try:
        chain_tips = rpc_call_fn("getchaintips", [], settings)
        orphans = [
            t
            for t in chain_tips
            if t["status"] in ("invalid", "headers-only", "valid-fork")
        ]
        active = next((t for t in chain_tips if t["status"] == "active"), None)

        return {
            "orphan_count": len(orphans),
            "orphans": [
                {
                    "height": t["height"],
                    "hash": t["hash"][:16] + "...",
                    "branch_len": t["branchlen"],
                    "status": t["status"],
                }
                for t in orphans[:10]
            ],
            "active_height": active["height"] if active else None,
            "risk_level": _risk_from_count(len(orphans), medium=2, high=5),
        }
    except Exception as exc:
        logger.warning("get_double_spend_attempts failed: %s", exc)
        return {"orphan_count": 0, "orphans": [], "active_height": None, "risk_level": "unknown"}


def get_51_attack_risk(settings) -> dict[str, Any]:
    """
    Assess 51% attack risk by checking hashrate distribution across mining pools.
    """
    try:
        session = requests.Session()
        response = session.get(
            "https://mempool.space/api/v1/mining/pools/1w",
            headers={"Accept": "application/json"},
            timeout=settings.request_timeout,
        )
        response.raise_for_status()
        data = response.json()

        pools = data.get("pools", [])
        total_blocks = sum(p.get("blockCount", 0) for p in pools)

        pool_data = []
        for pool in pools[:10]:
            block_count = pool.get("blockCount", 0)
            share = (block_count / total_blocks * 100) if total_blocks > 0 else 0
            pool_data.append({
                "name": pool.get("name", "Unknown"),
                "share": round(share, 2),
                "blocks": block_count,
                "risk": _risk_from_share(share),
            })

        top_pool_share = pool_data[0]["share"] if pool_data else 0
        risk_level = _risk_from_share(top_pool_share)

        return {
            "pools": pool_data,
            "total_blocks": total_blocks,
            "top_pool_share": top_pool_share,
            "risk_level": risk_level,
            "period": "7 days",
        }
    except Exception as exc:
        logger.warning("get_51_attack_risk failed: %s", exc)
        return {
            "pools": [],
            "total_blocks": 0,
            "top_pool_share": 0,
            "risk_level": "unknown",
            "period": "7 days",
        }


def get_invalid_block_attempts(rpc_call_fn, settings) -> dict[str, Any]:
    """
    Detect invalid block attempts using getchaintips looking for 'invalid' status chains.
    """
    try:
        chain_tips = rpc_call_fn("getchaintips", [], settings)
        invalid_chains = [t for t in chain_tips if t["status"] == "invalid"]

        return {
            "invalid_count": len(invalid_chains),
            "invalid_chains": [
                {
                    "height": t["height"],
                    "hash": t["hash"][:16] + "...",
                    "branch_len": t["branchlen"],
                }
                for t in invalid_chains[:10]
            ],
            "risk_level": _risk_from_count(len(invalid_chains), medium=1, high=3),
        }
    except Exception as exc:
        logger.warning("get_invalid_block_attempts failed: %s", exc)
        return {"invalid_count": 0, "invalid_chains": [], "risk_level": "unknown"}


def get_reorg_events(rpc_call_fn, settings) -> dict[str, Any]:
    """
    Detect blockchain reorganization events by monitoring valid-fork chain tips.
    """
    try:
        chain_tips = rpc_call_fn("getchaintips", [], settings)
        reorgs = [t for t in chain_tips if t["status"] == "valid-fork"]

        blockchain_info = rpc_call_fn("getblockchaininfo", [], settings)
        current_height = blockchain_info.get("blocks", 0)

        reorg_data = []
        for r in reorgs[:10]:
            depth = current_height - r["height"]
            reorg_data.append({
                "height": r["height"],
                "hash": r["hash"][:16] + "...",
                "branch_len": r["branchlen"],
                "depth_from_tip": depth,
                "severity": _risk_from_branch_length(r["branchlen"]),
            })

        max_branch = max((r["branch_len"] for r in reorgs), default=0)

        return {
            "reorg_count": len(reorgs),
            "reorgs": reorg_data,
            "current_height": current_height,
            "max_branch_length": max_branch,
            "risk_level": _risk_from_branch_length(max_branch) if reorgs else "safe",
        }
    except Exception as exc:
        logger.warning("get_reorg_events failed: %s", exc)
        return {
            "reorg_count": 0,
            "reorgs": [],
            "current_height": 0,
            "max_branch_length": 0,
            "risk_level": "unknown",
        }


def _risk_from_count(count: int, medium: int, high: int) -> str:
    if count > high:
        return "high"
    if count > medium:
        return "medium"
    return "low"


def _risk_from_share(share: float) -> str:
    if share >= 40:
        return "critical"
    if share >= 30:
        return "high"
    if share >= 20:
        return "medium"
    return "low"


def _risk_from_branch_length(branch_length: int) -> str:
    if branch_length > 3:
        return "critical"
    if branch_length > 1:
        return "high"
    return "low"
