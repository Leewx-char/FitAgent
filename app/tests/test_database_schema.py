"""应用启动时模型直建数据库结构的测试。"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app import models
from app.core import database


def test_create_all_tables_uses_the_supplied_engine(monkeypatch):
    """模型建表入口必须把调用方传入的引擎交给 SQLAlchemy。"""
    bind = object()
    create_all = Mock()
    monkeypatch.setattr(models.Base.metadata, "create_all", create_all)

    models.create_all_tables(bind)

    create_all.assert_called_once_with(bind=bind)


def test_ensure_schema_exists_delegates_to_model_entry(monkeypatch):
    """数据库层只调度模型入口，不在自身定义表结构。"""
    create_all_tables = Mock()
    monkeypatch.setattr(models, "create_all_tables", create_all_tables)

    database.ensure_schema_exists()

    create_all_tables.assert_called_once_with(database.engine)


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
