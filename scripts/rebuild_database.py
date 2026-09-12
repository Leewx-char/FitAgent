"""受保护地删除并按当前 ORM 模型重建配置的 MySQL 数据库。"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# 支持从仓库根目录直接执行 ``python scripts/rebuild_database.py``。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pymysql

from app.core.database import initialize_schema
from app.core.settings import get_settings


def _database_name(value: str) -> str:
    """验证数据库名，避免将配置值直接拼进破坏性 SQL。"""

    if not re.fullmatch(r"[A-Za-z0-9_]+", value):
        raise RuntimeError("MYSQL_DATABASE 只能包含字母、数字和下划线")
    return value


def rebuild_database(confirm_database: str) -> None:
    """仅在确认值精确匹配当前配置数据库时执行删除、创建和建表。"""

    settings = get_settings()
    database = _database_name(settings.mysql_database)
    print(f"目标 MySQL：{settings.mysql_host}:{settings.mysql_port} / {database}")
    if confirm_database != database:
        raise RuntimeError("拒绝执行：--confirm-database 必须与当前 MYSQL_DATABASE 完全一致。")

    connection = pymysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        charset="utf8mb4",
        autocommit=True,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"DROP DATABASE IF EXISTS `{database}`")
            cursor.execute(
                f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
    finally:
        connection.close()

    initialize_schema()
    print(f"数据库已重建并按当前 ORM 建表：{database}")


def main() -> None:
    """解析显式确认参数，不提供交互式或默认确认。"""

    parser = argparse.ArgumentParser(description="删除并重建当前配置的 FitAgent MySQL 数据库")
    parser.add_argument("--confirm-database", required=True, help="必须精确等于 MYSQL_DATABASE")
    args = parser.parse_args()
    rebuild_database(args.confirm_database)


if __name__ == "__main__":
    main()
