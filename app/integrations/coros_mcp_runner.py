"""Launch the community Coros MCP with a FitAgent-owned SQLite cache location.

This module is executed by the isolated ``.tools/coros-mcp-venv`` interpreter, not by the
FastAPI dependency environment. It preserves the real Windows user profile so the provider
can read its OS-managed authentication token, while redirecting only the provider cache that
otherwise defaults to ``Path.home()/.config/coros-mcp/cache.db``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path


def _configure_provider_cache() -> None:
    """Override the provider module's cache path before importing its server or sync code."""

    raw_path = os.getenv("FITAGENT_COROS_MCP_CACHE_DIR", "").strip()
    if not raw_path:
        raise RuntimeError("FITAGENT_COROS_MCP_CACHE_DIR 未配置")
    cache_dir = Path(raw_path).expanduser().resolve()

    from coros_mcp.cache import store

    store.CACHE_DB = cache_dir / "cache.db"


def _synchronize(start_day: str, end_day: str) -> int:
    """Fetch a requested range into the private cache and emit only summary counts."""

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
    # A provider source may be temporarily unavailable (notably mobile sleep data) while
    # daily/activity records are usable. Fail only when no data exists at all; otherwise let
    # the API persist successful sources and report a structured partial result.
    return 1 if failed_sources and not any(cached_counts.values()) else 0


def main() -> int:
    """Dispatch the stdio server or the explicit user-triggered cache synchronization."""

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
