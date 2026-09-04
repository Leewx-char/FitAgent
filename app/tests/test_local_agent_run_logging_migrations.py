"""本地 Agent 运行记录迁移的离线顺序测试。"""

import importlib.util
from pathlib import Path


class MigrationOperationRecorder:
    """记录迁移向 Alembic 发出的离线操作及其顺序。"""

    def __init__(self) -> None:
        """初始化空的操作事件列表。"""
        self.events: list[tuple[str, tuple[object, ...]]] = []

    def create_index(self, *args: object, **_kwargs: object) -> None:
        """记录创建索引的迁移操作。"""
        self.events.append(("create_index", args))

    def drop_index(self, *args: object, **_kwargs: object) -> None:
        """记录删除索引的迁移操作。"""
        self.events.append(("drop_index", args))

    def execute(self, statement: object) -> None:
        """记录显式执行的数据修复语句。"""
        self.events.append(("execute", (statement,)))

    def __getattr__(self, operation_name: str):
        """记录本测试不关心参数的其余 Alembic 操作。"""

        def record(*args: object, **_kwargs: object) -> None:
            self.events.append((operation_name, args))

        return record


def _migration(filename: str):
    """按文件名加载不作为 Python 包发布的 Alembic 迁移。"""
    path = Path(__file__).parents[2] / "alembic" / "versions" / filename
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _event_index(
    events: list[tuple[str, tuple[object, ...]]], operation_name: str, target_name: str
) -> int:
    """返回指定索引操作在迁移事件中的位置。"""
    return next(
        index
        for index, (name, arguments) in enumerate(events)
        if name == operation_name and arguments[0] == target_name
    )


def _column_alter_index(events: list[tuple[str, tuple[object, ...]]], column_name: str) -> int:
    """返回指定列改名操作在迁移事件中的位置。"""
    return next(
        index
        for index, (name, arguments) in enumerate(events)
        if name == "alter_column" and arguments[1] == column_name
    )


def test_coaching_memory_migration_keeps_a_user_prefix_index_during_index_replacement(
    monkeypatch,
):
    """升级和降级均应先创建替代索引，再移除旧索引。"""
    migration = _migration("20260817_03_coaching_memory_and_plans.py")

    upgrade = MigrationOperationRecorder()
    monkeypatch.setattr(migration, "op", upgrade)
    migration.upgrade()
    created_replacement = _event_index(
        upgrade.events, "create_index", "ix_fitness_user_type_external"
    )
    dropped_legacy = _event_index(upgrade.events, "drop_index", "ix_fitness_user_date_type")
    assert created_replacement < dropped_legacy

    downgrade = MigrationOperationRecorder()
    monkeypatch.setattr(migration, "op", downgrade)
    migration.downgrade()
    recreated_legacy = _event_index(downgrade.events, "create_index", "ix_fitness_user_date_type")
    dropped_replacement = _event_index(
        downgrade.events, "drop_index", "ix_fitness_user_type_external"
    )
    assert recreated_legacy < dropped_replacement


def test_run_logging_downgrade_normalizes_old_tool_columns_before_renaming(monkeypatch):
    """降级必须先恢复旧列可接受的 JSON 对象和短文本。"""
    migration = _migration("20260904_04_local_agent_run_logging.py")
    recorder = MigrationOperationRecorder()
    monkeypatch.setattr(migration, "op", recorder)

    migration.downgrade()

    input_update = _event_index(
        recorder.events, "execute", "UPDATE agent_tool_calls SET tool_input = '{}'"
    )
    output_update = _event_index(
        recorder.events,
        "execute",
        "UPDATE agent_tool_calls SET tool_output = LEFT(tool_output, 120)",
    )
    output_rename = _column_alter_index(recorder.events, "tool_output")
    input_rename = _column_alter_index(recorder.events, "tool_input")
    assert input_update < output_rename
    assert output_update < output_rename
    assert input_update < input_rename
