"""使用 FitAgent 管理的 SQLite 缓存位置启动社区 Coros MCP。
本模块由隔离解释器运行，保留真实 Windows 用户配置中的认证令牌，只重定向服务方缓存。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path


def _configure_provider_cache() -> None:
    """在导入服务端或同步代码前覆盖服务方模块的缓存路径。"""

    raw_path = os.getenv("FITAGENT_COROS_MCP_CACHE_DIR", "").strip()
    if not raw_path:
        raise RuntimeError("FITAGENT_COROS_MCP_CACHE_DIR 未配置")
    cache_dir = Path(raw_path).expanduser().resolve()

    from coros_mcp.cache import store

    store.CACHE_DB = cache_dir / "cache.db"


def _synchronize(start_day: str, end_day: str) -> int:
    """同步请求日期范围至私有缓存，并仅输出汇总数量。"""

    _configure_provider_cache()
    from coros_mcp.cache.sync import sync_all
    from coros_mcp.coros_api import get_stored_auth, try_auto_login

    auth = get_stored_auth() or asyncio.run(try_auto_login())
    if auth is None:
        print(json.dumps({"status": "authentication_required"}))
        return 1
    stats = asyncio.run(sync_all(auth, start_day, end_day=end_day))
    cache = stats.get("cache", {})
    cached_counts = {
        "daily": int(cache.get("daily_records", {}).get("count", 0)),
        "sleep": int(cache.get("sleep_records", {}).get("count", 0)),
        "activities": int(cache.get("activities", {}).get("count", 0)),
    }
    failed_sources = sorted({error.split(" ", 1)[0] for error in stats["errors"]})
    print(
        json.dumps(
            {
                "daily": stats["daily"],
                "sleep": stats["sleep"],
                "activities": stats["activities"],
                "partial": bool(failed_sources),
                "failed_sources": failed_sources,
                "cached_source_counts": cached_counts,
            }
        )
    )
    # 单个来源（尤其移动端睡眠）可能暂不可用；仅在没有任何可用数据时失败。
    return 1 if failed_sources and not any(cached_counts.values()) else 0


def main() -> int:
    """分派标准输入输出服务，或执行用户显式触发的缓存同步。"""

    parser = argparse.ArgumentParser(prog="fitagent-coros-mcp")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("serve")
    sync_parser = subparsers.add_parser("sync")
    sync_parser.add_argument("--from", dest="start_day", required=True)
    sync_parser.add_argument("--to", dest="end_day", required=True)
    args = parser.parse_args()

    if args.command == "sync":
        return _synchronize(args.start_day, args.end_day)

    _configure_provider_cache()
    from coros_mcp import server

    server.main()
    return 0


if __name__ == "__main__":  # pragma: no cover - run by the isolated provider interpreter
    sys.exit(main())
