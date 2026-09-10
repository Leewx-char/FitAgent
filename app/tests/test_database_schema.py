"""应用启动时模型直建数据库结构的测试。"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.core import database


def test_initialize_schema_creates_all_models_with_configured_engine(monkeypatch):
    """数据库层直接以配置引擎创建所有已注册模型表。"""
    create_all = Mock()
    monkeypatch.setattr(database, "ensure_database_exists", Mock())
    monkeypatch.setattr(database.Base.metadata, "create_all", create_all)

    database.initialize_schema()

    create_all.assert_called_once_with(bind=database.engine)


def test_ensure_database_exists_creates_the_configured_database(monkeypatch):
    """启动建库不再依赖已经删除的本地开发开关。"""
    statements: list[str] = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, statement):
            statements.append(statement)

    class Connection:
        def cursor(self):
            return Cursor()

        def close(self):
            return None

    settings = SimpleNamespace(
        mysql_database="fitness",
        mysql_host="localhost",
        mysql_port=3306,
        mysql_user="root",
        mysql_password="password",
    )
    monkeypatch.setattr("pymysql.connect", lambda **_kwargs: Connection())

    database.ensure_database_exists(settings)

    identifier = chr(96) + "fitness" + chr(96)
    assert statements == [
        "CREATE DATABASE IF NOT EXISTS "
        + identifier
        + " CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
    ]


def test_ensure_database_exists_rejects_unsafe_database_name():
    """数据库名必须保持原有白名单，防止 DDL 注入。"""
    settings = SimpleNamespace(mysql_database="fitness; DROP DATABASE zhitong")

    with pytest.raises(RuntimeError, match="MYSQL_DATABASE"):
        database.ensure_database_exists(settings)
