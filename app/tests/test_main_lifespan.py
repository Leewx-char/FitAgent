"""应用生命周期中的数据库结构初始化测试。"""

import pytest

from app import main


@pytest.mark.anyio
async def test_lifespan_initializes_database_and_schema_before_runtime_work(monkeypatch):
    """建库、建表先于运行检查，启动时不构建本地检索器。"""
    events: list[str] = []
    monkeypatch.setattr(main, "ensure_database_exists", lambda: events.append("database"))
    monkeypatch.setattr(main, "ensure_schema_exists", lambda: events.append("schema"))
    monkeypatch.setattr(main, "validate_runtime", lambda: events.append("runtime") or [])
    monkeypatch.setattr(main, "close_coros", lambda: events.append("close"))

    async with main.lifespan(main.app):
        assert events == ["database", "schema", "runtime"]

    assert events == ["database", "schema", "runtime", "close"]


@pytest.mark.anyio
async def test_lifespan_does_not_run_runtime_work_when_schema_creation_fails(monkeypatch):
    """建表失败后必须拒绝启动，而不是继续访问外部服务。"""
    events: list[str] = []
    monkeypatch.setattr(main, "ensure_database_exists", lambda: events.append("database"))

    def fail_schema_creation():
        events.append("schema")
        raise RuntimeError("schema unavailable")

    monkeypatch.setattr(main, "ensure_schema_exists", fail_schema_creation)
    monkeypatch.setattr(main, "validate_runtime", lambda: events.append("runtime") or [])

    with pytest.raises(RuntimeError, match="schema unavailable"):
        async with main.lifespan(main.app):
            pass

    assert events == ["database", "schema"]


@pytest.mark.anyio
async def test_lifespan_does_not_create_schema_when_database_creation_fails(monkeypatch):
    """建库失败后不得继续建表或运行检查。"""
    events: list[str] = []

    def fail_database_creation():
        events.append("database")
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(main, "ensure_database_exists", fail_database_creation)
    monkeypatch.setattr(main, "ensure_schema_exists", lambda: events.append("schema"))
    monkeypatch.setattr(main, "validate_runtime", lambda: events.append("runtime") or [])

    with pytest.raises(RuntimeError, match="database unavailable"):
        async with main.lifespan(main.app):
            pass

    assert events == ["database"]
