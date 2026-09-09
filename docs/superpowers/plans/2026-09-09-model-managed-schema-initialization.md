# 模型直建数据库结构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 删除 Alembic，并让服务启动时自动创建缺失的 MySQL 数据库和全部 ORM 模型表。

**Architecture:** `app/models.py` 在所有模型声明之后持有唯一的 `create_all_tables(bind)` 建表入口；`app/core/database.py` 保留引擎、会话、建库和延迟导入的调度职责。FastAPI lifespan 在运行检查和 RAG 预热前依次建库和建表，因而仅补建缺失结构，不改写已存在表。

**Tech Stack:** Python 3.13、FastAPI、SQLAlchemy 2、PyMySQL、pytest、Ruff、MySQL 9.6。

**Spec:** `docs/superpowers/specs/2026-09-09-model-managed-schema-initialization-design.md`

## Global Constraints

- 只操作 `MYSQL_DATABASE=fitness`；不得读取、迁移或删除 `zhitong`。
- 缺失数据库和表必须幂等创建；已有表不自动变更列、索引或数据。
- 应用运行时只有 `app/models.py` 的建表入口可以调用 `Base.metadata.create_all()`；测试夹具可继续直接创建隔离的 SQLite 表，其函数只接收调用方传入的 `Engine`。
- 不保留 Alembic 依赖、版本脚本、测试或运行/学习文档引用；历史设计和计划文档可保留背景记录。
- 保持 `新手`、`中级`、`高级` 经验值和训练安全降级逻辑不变。
- 每项代码行为先有失败测试；每个任务只提交列出的文件。

---

## File Structure

| 文件 | 变更 | 职责 |
| --- | --- | --- |
| `app/models.py` | 修改 | 全部模型注册后提供 `create_all_tables(bind: Engine) -> None`。 |
| `app/core/database.py` | 修改 | 幂等建库；延迟加载模型并调度建表。 |
| `app/core/settings.py` | 修改 | 删除废弃的 `AUTO_CREATE_DATABASE`。 |
| `app/main.py` | 修改 | lifespan 的最前面初始化数据库和表。 |
| `app/tests/test_database_schema.py` | 新建 | 覆盖模型入口、调度、建库 SQL 和名称保护。 |
| `app/tests/test_main_lifespan.py` | 新建 | 覆盖启动顺序与初始化失败短路。 |
| `alembic/`、`alembic.ini`、`app/tests/test_local_agent_run_logging_migrations.py` | 删除 | 清除迁移运行时、历史和专属测试。 |
| `pyproject.toml`、`.env.example`、`README.md`、`docs/agent-run-logging-architecture.md`、`docs/learning-guide.md` | 修改 | 移除过期依赖、设置、命令和链接。 |

### Task 1: 模型建表入口与数据库编排

**Files:**
- Create: `app/tests/test_database_schema.py`
- Modify: `app/models.py:1-260`
- Modify: `app/core/database.py:1-90`
- Modify: `app/core/settings.py:14-26`

**Interfaces:**
- Consumes: `Base`、`engine`、`Settings`。
- Produces: `create_all_tables(bind: Engine) -> None`、`ensure_schema_exists() -> None`、`ensure_database_exists(settings: Settings | None = None) -> None`。

- [x] **Step 1: 先写失败测试**

```python
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app import models
from app.core import database


def test_create_all_tables_uses_the_supplied_engine(monkeypatch):
    bind = object()
    create_all = Mock()
    monkeypatch.setattr(models.Base.metadata, "create_all", create_all)

    models.create_all_tables(bind)

    create_all.assert_called_once_with(bind=bind)


def test_ensure_schema_exists_delegates_to_model_entry(monkeypatch):
    create_all_tables = Mock()
    monkeypatch.setattr(models, "create_all_tables", create_all_tables)

    database.ensure_schema_exists()

    create_all_tables.assert_called_once_with(database.engine)


def test_ensure_database_exists_creates_the_configured_database(monkeypatch):
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
    settings = SimpleNamespace(mysql_database="fitness; DROP DATABASE zhitong")

    with pytest.raises(RuntimeError, match="MYSQL_DATABASE"):
        database.ensure_database_exists(settings)
```

- [x] **Step 2: 运行失败测试**

Run: `.venv/bin/python -m pytest app/tests/test_database_schema.py -q -p no:cacheprovider`  
Expected: FAIL，提示 `models.create_all_tables` 和 `database.ensure_schema_exists` 尚不存在。

- [x] **Step 3: 写最小实现**

在 `app/models.py` 的 SQLAlchemy 导入区加入 `from sqlalchemy.engine import Engine`，并在最后一个 ORM 类后新增：

```python
def create_all_tables(bind: Engine) -> None:
    """根据本模块已注册的 ORM 模型创建缺失表，不修改已有表。"""
    Base.metadata.create_all(bind=bind)
```

在 `app/core/database.py`：
1. 删除 `if not settings.auto_create_database: return` 以及“仅供本地开发”的描述；保留数据库名正则校验和现有 PyMySQL 连接错误包装。
2. 增加以下函数（延迟导入是为避免 `models.py -> Base` 的循环导入）：

```python
def ensure_schema_exists() -> None:
    """加载全部 ORM 模型并创建缺失的模型表。"""
    from app.models import create_all_tables

    create_all_tables(engine)
```

3. 从 `app/core/settings.py` 删除 `auto_create_database: bool = False`。不能把 `create_all()` 移进 `database.py`，也不能在模块导入时访问数据库。

- [x] **Step 4: 验证 Task 1**

Run: `.venv/bin/python -m pytest app/tests/test_database_schema.py app/tests/test_database_session.py app/tests/test_settings.py -q -p no:cacheprovider`  
Expected: PASS。

Run: `.venv/bin/python -m ruff format --check app/models.py app/core/database.py app/core/settings.py app/tests/test_database_schema.py && .venv/bin/python -m ruff check app/models.py app/core/database.py app/core/settings.py app/tests/test_database_schema.py`  
Expected: PASS。

- [x] **Step 5: 提交 Task 1**

```bash
git add app/models.py app/core/database.py app/core/settings.py app/tests/test_database_schema.py
git commit -m "feat: create missing schema from ORM models"
```

### Task 2: 在 FastAPI 生命周期自动初始化

**Files:**
- Create: `app/tests/test_main_lifespan.py`
- Modify: `app/main.py:13-61`

**Interfaces:**
- Consumes: Task 1 的 `ensure_database_exists()` 与 `ensure_schema_exists()`。
- Produces: 启动事件顺序 `database`、`schema`、`runtime`、`warm`；任一建库建表错误均阻止后续工作。

- [x] **Step 1: 写失败测试**

```python
import pytest

from app import main


@pytest.mark.anyio
async def test_lifespan_initializes_database_and_schema_before_runtime_work(monkeypatch):
    events: list[str] = []
    monkeypatch.setattr(main, "ensure_database_exists", lambda: events.append("database"))
    monkeypatch.setattr(main, "ensure_schema_exists", lambda: events.append("schema"))
    monkeypatch.setattr(main, "validate_runtime", lambda: events.append("runtime") or [])
    monkeypatch.setattr(main, "warm_rag_retriever", lambda: events.append("warm"))
    monkeypatch.setattr(main, "close_coros", lambda: events.append("close"))

    async with main.lifespan(main.app):
        assert events == ["database", "schema", "runtime", "warm"]

    assert events == ["database", "schema", "runtime", "warm", "close"]


@pytest.mark.anyio
async def test_lifespan_does_not_run_runtime_work_when_schema_creation_fails(monkeypatch):
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
```

- [x] **Step 2: 运行失败测试**

Run: `.venv/bin/python -m pytest app/tests/test_main_lifespan.py -q -p no:cacheprovider`  
Expected: FAIL，`app.main` 尚未导入初始化函数。

- [x] **Step 3: 最小接入**

在 `app/main.py` 加入：

```python
from app.core.database import ensure_database_exists, ensure_schema_exists
```

在 `lifespan()` 的首个可执行语句添加：

```python
    ensure_database_exists()
    ensure_schema_exists()
```

两行必须位于 `validate_runtime()`、`warm_rag_retriever()` 前面且不捕获异常。

- [x] **Step 4: 验证 Task 2**

Run: `.venv/bin/python -m pytest app/tests/test_main_lifespan.py app/tests/test_bootstrap.py app/tests/test_database_schema.py -q -p no:cacheprovider`  
Expected: PASS。

Run: `.venv/bin/python -m ruff format --check app/main.py app/tests/test_main_lifespan.py && .venv/bin/python -m ruff check app/main.py app/tests/test_main_lifespan.py`  
Expected: PASS。

- [x] **Step 5: 提交 Task 2**

```bash
git add app/main.py app/tests/test_main_lifespan.py
git commit -m "feat: initialize missing schema at startup"
```

### Task 3: 删除 Alembic 并更新项目说明

**Files:**
- Delete: `alembic.ini`、`alembic/env.py`、`alembic/script.py.mako`
- Delete: `alembic/versions/20260723_01_initial_schema.py`
- Delete: `alembic/versions/20260724_02_agent_traces.py`
- Delete: `alembic/versions/20260817_03_coaching_memory_and_plans.py`
- Delete: `alembic/versions/20260904_04_local_agent_run_logging.py`
- Delete: `app/tests/test_local_agent_run_logging_migrations.py`
- Modify: `pyproject.toml:29-42`、`.env.example:28-35`
- Modify: `README.md:58-98,214-218,273-279`
- Modify: `docs/agent-run-logging-architecture.md:122-141`
- Modify: `docs/learning-guide.md:191`

**Interfaces:**
- Consumes: Task 1–2 的启动初始化。
- Produces: 源码、依赖、示例设置和文档均不再引用 Alembic。

- [x] **Step 1: 记录当前检查的失败证据**

Run: `rg -n "alembic|AUTO_CREATE_DATABASE" pyproject.toml .env.example README.md docs app alembic.ini`  
Expected: 显示包依赖、示例设置、README 命令、文档链接、迁移脚本和迁移测试。

- [x] **Step 2: 删除精确范围内的迁移资产**

用 `apply_patch` 删除本任务 Files 块列出的 `alembic.ini`、`alembic/` 全部文件和 `app/tests/test_local_agent_run_logging_migrations.py`。这些正是用户授权的删除范围；不得删除其它 `docs/superpowers` 文件、`.agents/` 或任何不在列表内的测试。

- [x] **Step 3: 移除依赖、变量与失效文档**

1. 从 `pyproject.toml` 的依赖数组删除精确行：

   ```toml
   "alembic==1.14.1",
   ```

2. 从 `.env.example` 删除：

   ```dotenv
   # 仅限本地开发；生产环境应由运维预先创建数据库
   AUTO_CREATE_DATABASE=true
   ```

3. README 的两处安装流程改为“启动服务会自动创建缺失数据库和表”，删除 `ensure_database_exists` 与 `alembic upgrade head` 命令；升级说明改为“模型字段变更须采用显式维护方案，重启不会升级已有表”；从目录树删除 Alembic 两行。
4. 将 `docs/agent-run-logging-architecture.md` 的迁移节点与链接替换为 `app/models.py` 中 `AgentRun`、`AgentToolCall` 的模型说明；将 `docs/learning-guide.md` 的 Alembic 链接替换为 `app/core/database.py` 启动建库建表流程。

- [x] **Step 4: 验证清理**

Run: `test ! -e alembic && test ! -e alembic.ini && ! rg -n "alembic|AUTO_CREATE_DATABASE" pyproject.toml .env.example README.md docs/agent-run-logging-architecture.md docs/learning-guide.md app`  
Expected: 退出码 0。

Run: `.venv/bin/python -m pytest app/tests/test_database_schema.py app/tests/test_main_lifespan.py -q -p no:cacheprovider`  
Expected: PASS。

- [x] **Step 5: 提交 Task 3**

```bash
git add -u alembic alembic.ini app/tests/test_local_agent_run_logging_migrations.py
git add pyproject.toml .env.example README.md docs/agent-run-logging-architecture.md docs/learning-guide.md
git commit -m "refactor: remove alembic schema migrations"
```

### Task 4: 重建 `fitness` 与完整验证

**Files:**
- Modify: 无源代码改动；只操作用户已授权的 `fitness` 数据库。

**Interfaces:**
- Consumes: `ensure_database_exists()`、`ensure_schema_exists()`、`Base.metadata.tables`。
- Produces: 干净的 `fitness`，包含且仅包含当前 ORM 模型表，并有完整测试结果。

- [x] **Step 1: 确认破坏性操作的精确目标**

```bash
.venv/bin/python - <<'PY'
from app.core.settings import get_settings

settings = get_settings()
if settings.mysql_database != "fitness":
    raise SystemExit(f"refusing to rebuild unexpected database: {settings.mysql_database!r}")
print(f"target_database={settings.mysql_database}")
PY
```

Expected: 仅输出 `target_database=fitness`，且不打印密码。

- [x] **Step 2: 仅删除再重建 `fitness`**

```bash
.venv/bin/python - <<'PY'
import re

import pymysql

from app.core.settings import get_settings

settings = get_settings()
if settings.mysql_database != "fitness":
    raise SystemExit(f"refusing to rebuild unexpected database: {settings.mysql_database!r}")
if not re.fullmatch(r"[A-Za-z0-9_]+", settings.mysql_database):
    raise SystemExit("unsafe MYSQL_DATABASE")
identifier = chr(96) + settings.mysql_database + chr(96)
connection = pymysql.connect(
    host=settings.mysql_host,
    port=settings.mysql_port,
    user=settings.mysql_user,
    password=settings.mysql_password,
    charset="utf8mb4",
)
try:
    with connection.cursor() as cursor:
        cursor.execute("DROP DATABASE IF EXISTS " + identifier)
finally:
    connection.close()

from app.core.database import ensure_database_exists, ensure_schema_exists

ensure_database_exists()
ensure_schema_exists()
PY
```

Expected: 成功创建空数据库与模型表；任何认证或权限错误都立即停止且不操作其它数据库。

- [x] **Step 3: 使用真实 MySQL 表元数据比对**

```bash
.venv/bin/python - <<'PY'
from sqlalchemy import text

from app.core.database import engine
from app.models import Base

expected = set(Base.metadata.tables)
with engine.connect() as connection:
    actual = set(
        connection.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE'"
            )
        ).scalars()
    )
if actual != expected:
    raise SystemExit(f"schema mismatch: missing={expected - actual}, unexpected={actual - expected}")
print(f"verified_tables={len(actual)}")
PY
```

Expected: `verified_tables` 等于模型表数量且无缺失或额外表。

- [x] **Step 4: 运行原中文经验值计划与新机制的全部门禁**

Run: `.venv/bin/python -m pytest app/tests/test_training_safety.py::test_safety_policy_uses_frontend_chinese_experience_values -q -p no:cacheprovider`  
Expected: PASS。

Run: `.venv/bin/python -m pytest app/tests/test_training_safety.py -q -p no:cacheprovider`  
Expected: PASS。

Run: `.venv/bin/python -m pytest app/tests/test_training_plans.py -q -p no:cacheprovider`  
Expected: PASS。

Run: `.venv/bin/python -m ruff format --check app/models.py app/services/training_plan_service.py app/tests/test_training_safety.py app/core/database.py app/main.py app/tests/test_database_schema.py app/tests/test_main_lifespan.py && .venv/bin/python -m ruff check app/models.py app/services/training_plan_service.py app/tests/test_training_safety.py app/core/database.py app/main.py app/tests/test_database_schema.py app/tests/test_main_lifespan.py`  
Expected: PASS。

- [x] **Step 5: 审计与回退点提交**

```bash
git diff --check
git status --short
git add docs/superpowers/specs/2026-09-09-model-managed-schema-initialization-design.md docs/superpowers/plans/2026-09-09-model-managed-schema-initialization.md
git commit -m "docs: document model managed schema initialization"
git log --oneline -3
```

Expected: 仅计划列出的文件进入三个提交。若实现不理想，按本计划三个提交的相反顺序执行 `git revert`，并仅重建空 `fitness`。
