"""显式将旧 MySQL 记忆复制到 mem0；默认预览，永不删除源记录。"""

import argparse
import json
import logging
from datetime import timezone

from app.core.database import SessionLocal
from app.models import MemoryFact
from app.services.memory_backend import get_memory_backend

logger = logging.getLogger(__name__)


def migrate_legacy_memories(db, *, backend=None, user_id=None, apply=False):
    """复制选定旧记忆并返回计数；重跑通过用户与 legacy_id 去重。

    旧数据库的无时区时间采用当前服务器本地时区转换到 UTC。
    提供商失败只记录错误类型，源数据库仅执行 SELECT。
    """
    query = db.query(MemoryFact)
    if user_id is not None:
        query = query.filter(MemoryFact.user_id == user_id)
    report = {"selected": 0, "created": 0, "skipped": 0, "failed": 0}
    imported = {}
    for row in query.order_by(MemoryFact.user_id, MemoryFact.id).yield_per(100):
        report["selected"] += 1
        if not apply:
            continue
        try:
            target = backend if backend is not None else get_memory_backend()
            if row.user_id not in imported:
                imported[row.user_id] = {
                    record.metadata.get("legacy_id")
                    for record in target.list(user_id=row.user_id, include_revoked=True)
                }
            if row.id in imported[row.user_id]:
                report["skipped"] += 1
                continue
            expiry = (
                row.expires_at.astimezone(timezone.utc).replace(tzinfo=None).isoformat()
                if row.expires_at
                else None
            )
            target.create(
                user_id=row.user_id,
                text=row.display_text,
                metadata={
                    "legacy_id": row.id,
                    "source": "legacy",
                    "status": row.status,
                    "source_message_id": row.source_message_id,
                    "fact_key": row.fact_key,
                    "category": row.category,
                    "value": json.loads(row.value),
                    "expires_at": expiry,
                    "legacy_created_at": row.created_at.isoformat(),
                    "legacy_updated_at": row.updated_at.isoformat(),
                },
            )
            imported[row.user_id].add(row.id)
            report["created"] += 1
        except Exception as error:
            logger.warning(
                "legacy memory migration failed: id=%s kind=%s", row.id, type(error).__name__
            )
            report["failed"] += 1
    return report


def main(argv=None):
    """运行可预览、可重复的迁移命令；有失败时退出码为 1。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", type=int, help="只迁移指定用户；省略时选择所有用户")
    parser.add_argument("--apply", action="store_true", help="实际写入 mem0；默认仅预览")
    args = parser.parse_args(argv)
    if args.user_id is not None and args.user_id <= 0:
        parser.error("--user-id 必须为正整数")
    with SessionLocal() as db:
        report = migrate_legacy_memories(db, user_id=args.user_id, apply=args.apply)
    print(json.dumps({"mode": "apply" if args.apply else "dry-run", **report}, ensure_ascii=False))
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
