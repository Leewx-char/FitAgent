"""应用生命周期中的数据库结构初始化测试。"""

import pytest

from app import main


@pytest.mark.anyio
async def test_lifespan_initializes_schema_before_runtime_work(monkeypatch):
    """schema 初始化先于运行检查，启动时不构建本地 COROS 进程。"""
    events: list[str] = []
    monkeypatch.setattr(main, "initialize_schema", lambda: events.append("schema"))
    monkeypatch.setattr(main, "validate_runtime", lambda: events.append("runtime") or [])
    async with main.lifespan(main.app):
        assert events == ["schema", "runtime"]

    assert events == ["schema", "runtime"]


@pytest.mark.anyio
async def test_lifespan_does_not_run_runtime_work_when_schema_creation_fails(monkeypatch):
    """建表失败后必须拒绝启动，而不是继续访问外部服务。"""
    events: list[str] = []

    def fail_schema_creation():
        events.append("schema")
        raise RuntimeError("schema unavailable")

    monkeypatch.setattr(main, "initialize_schema", fail_schema_creation)
    monkeypatch.setattr(main, "validate_runtime", lambda: events.append("runtime") or [])

    with pytest.raises(RuntimeError, match="schema unavailable"):
        async with main.lifespan(main.app):
            pass

    assert events == ["schema"]
