# 05 - 数据治理与数据库重构方案

> **状态**: 待实施  
> **优先级**: P1（影响数据一致性和长期维护）  
> **预计工时**: 3-4 天

---

## 一、数据架构现状诊断

### 1.1 数据全景

| 数据类型 | 存储位置 | 格式 | 写入方 | 读取方 | 质量保证 |
|---------|---------|------|--------|--------|---------|
| 用户账户 | MySQL `users` | 结构化列 | `auth.py` | `auth.py`, `agent_tools.py` | 唯一索引 |
| 用户画像 | MySQL `user_profiles` | 混合（列 + JSON TEXT） | `profile.py` | `agent_tools.py`, `chat.py` | 无 |
| 会话消息 | MySQL `sessions` + `messages` | 结构化列 | `chat.py` | `chat.py` | 外键级联 |
| 运动数据 | MySQL `fitness_data` | 混合（列 + JSON TEXT） | `fitness.py` | `agent_tools.py` | 唯一索引 |
| 知识库 | `data/*.txt` 文件 | 纯文本 | 人工 | `vector_store.py`, `rag_service.py` | MD5 manifest |
| 向量索引 | ChromaDB `chroma.sqlite3` | 向量 + 元数据 | `vector_store.py` | `rag_service.py` | HNSW 自检 |
| BM25 索引 | 内存（重启丢失） | Python 对象 | `bm25_retriever.py` | `rag_service.py` | 无 |
| 上传文件 | `storage/uploads/` | 图片/PDF | `upload.py` | `doc_parser.py` | MIME 校验 |
| 日志 | `logs/*.log` | 文本 | `logger_handler.py` | 运维人员 | 按天轮转 |
| 审计日志 | `logs/audit_*.log` | JSON 行 | `audit.py` | 安全审计 | 无 |

### 1.2 问题清单

| 问题 | 位置 | 严重程度 | 说明 |
|------|------|---------|------|
| **数据契约缺失** | 全局 | 高 | `fitness_data.data` 存 JSON 字符串，无 schema 约束，写入方和读取方靠口头约定 |
| **JSON 字段过度使用** | `models.py:44-46,82` | 中 | `injuries`, `diet_restrict`, `preferences`, `health_data`, `data` 都是 TEXT 列存 JSON |
| **数据一致性无保障** | `fitness.py` | 高 | 运动数据同步时无幂等保证，重复同步可能产生脏数据 |
| **数据清理缺失** | 全局 | 中 | 上传临时文件、过期会话、旧日志无自动清理 |
| **数据迁移策略缺失** | 全局 | 低 | 无 Alembic 等迁移工具，建表靠 `Base.metadata.create_all` |
| **测试数据管理** | `app/tests/conftest.py` | 低 | 测试数据库依赖真实 MySQL 连接，非隔离 |
| **向量库版本追溯** | `vector_store.py` | 中 | 知识库更新后无法回滚到上一个版本 |
| **Coros 子进程管理** | `coros_client.py` | 中 | 子进程异常退出或僵尸进程无自动恢复机制 |
| **模型与 Schema 分离** | `models.py` + `schemas.py` | 低 | 两个文件分别定义同一实体的不同表示，部分字段未对齐 |

---

## 二、分步骤重构方案

### 步骤 1：数据契约定义

**目标**：每个 active dataset 都有明确的写入方、读取方、格式约束和质量检查。

**方案**：创建 `app/core/data_contracts.py`：

```python
# app/core/data_contracts.py (新文件)

"""
数据契约：定义每个数据集的 schema、写入方、读取方和质量检查规则。

遵循 AGENTS.md 数据门禁要求：
- 每个 active dataset 有 contract + writer + collect
- resource_id 与 registry 一致
- 质量检查覆盖空写、重复写、时间边界、幂等
"""

from datetime import date, datetime
from typing import Any
from pydantic import BaseModel, Field

# ==================== fitness_data 数据契约 ====================

class DailyMetricsItem(BaseModel):
    """日指标数据契约（对应 coros get_daily_metrics 返回的单条记录）"""
    date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    rhr: int | None = Field(None, ge=30, le=200)   # 静息心率 30-200
    avg_sleep_hrv: int | None = Field(None, ge=0, le=200)
    training_load: float | None = Field(None, ge=0)
    training_load_ratio: float | None = Field(None, ge=0, le=5.0)
    tired_rate: float | None = Field(None, ge=0, le=100)
    vo2max: float | None = Field(None, ge=20, le=80)

class SleepItem(BaseModel):
    """睡眠数据契约"""
    date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    total_duration_minutes: int = Field(..., ge=60, le=1440)
    phases: dict = Field(default_factory=dict)

class ActivityItem(BaseModel):
    """运动记录契约"""
    name: str = ""
    sport_name: str = ""
    duration_seconds: int = Field(0, ge=0, le=86400)
    start_time: str = ""

# ==================== 数据集注册表 ====================

DATA_REGISTRY = {
    "user_profiles": {
        "resource_id": "mysql://zhitong/user_profiles",
        "writer": "app.api.routers.profile",
        "reader": ["app.services.agent_tools.get_user_profile"],
        "contract": "ProfileCreate",
        "quality_checks": {
            "no_empty_age": lambda r: r.age is not None and r.age > 0,
            "no_empty_weight": lambda r: r.weight is not None and r.weight > 0,
            "valid_goal": lambda r: r.goal in ("减脂", "增肌", "塑形", "耐力提升", "健康维护", ""),
        },
    },
    "fitness_daily_metrics": {
        "resource_id": "mysql://zhitong/fitness_data?type=daily_metrics",
        "writer": "app.api.routers.fitness.sync_from_coros",
        "reader": ["app.services.agent_tools.get_fitness_summary"],
        "contract": "DailyMetricsItem",
        "quality_checks": {
            "no_empty_data": lambda r: r.rhr is not None or r.training_load is not None,
            "date_in_range": lambda r: date.fromisoformat(r.date) >= date.today().replace(year=date.today().year - 1),
        },
    },
    "knowledge_base": {
        "resource_id": "chromadb://chroma_db/agent",
        "writer": "app.services.vector_store.load_document",
        "reader": ["app.services.rag_service.retriever_docs"],
        "contract": "KnowledgeChunk",
        "quality_checks": {
            "min_chunk_size": lambda c: len(c["content"]) >= 20,
            "has_source": lambda c: bool(c.get("metadata", {}).get("source")),
        },
    },
}
```

### 步骤 2：混合存储策略优化

**当前问题**：`injuries`, `diet_restrict`, `preferences`, `health_data` 都存为 JSON TEXT 列，无法利用数据库的查询能力。

**方案**：保持当前的混合存储策略（动机正确：这些字段结构多变，不适宜用固定列），但增加以下改进：

**2.1 JSON 列使用 MySQL 原生的 JSON 类型（MySQL 5.7.8+）**

```python
# app/models.py 修改

from sqlalchemy import JSON  # SQLAlchemy 2.0+ 原生支持

class UserProfile(Base):
    # 将 Text 改为 JSON 类型
    injuries = Column(JSON, default=list)        # 原：Text, default="[]"
    diet_restrict = Column(JSON, default=list)   # 原：Text, default="[]"
    preferences = Column(JSON, default=dict)      # 原：Text, default="{}"
    health_data = Column(JSON, default=dict)      # 原：Text, default="{}"

class FitnessData(Base):
    data = Column(JSON, default=dict)             # 原：Text, default="{}"
```

优点：
- SQLAlchemy 自动 JSON 序列化/反序列化，无需手动 `json.loads` / `json.dumps`
- 可以利用 MySQL 的 JSON 查询函数（`JSON_EXTRACT`, `JSON_CONTAINS`）
- `schemas.py` 中的 `@field_validator` 可以简化

**2.2 同步简化 schemas.py 中的 JSON 解析**

```python
# app/schemas.py 简化（如果 models 使用 JSON 类型）

# 不再需要这些 validator（SQLAlchemy JSON 类型已自动处理）
# @field_validator("injuries", "diet_restrict", mode="before")
# @classmethod
# def parse_json_list(cls, v): ...
```

### 步骤 3：数据幂等写入保障

**当前问题**：运动数据同步时使用 `INSERT ... ON DUPLICATE KEY UPDATE`（依赖唯一索引），但没有数据版本号，无法判断是否需要更新。

**方案**：

```python
# app/api/routers/fitness.py 修改

def _upsert_fitness_data(db, user_id, date_val, data_type, data_dict):
    """幂等写入：已存在且数据相同则跳过"""
    existing = (
        db.query(FitnessData)
        .filter(
            FitnessData.user_id == user_id,
            FitnessData.date == date_val,
            FitnessData.data_type == data_type,
        )
        .with_for_update()  # 行锁，防止并发覆盖
        .first()
    )
    
    if existing:
        # 比对数据是否变更
        existing_data = existing.data if isinstance(existing.data, dict) else json.loads(existing.data)
        if existing_data == data_dict:
            logger.debug(f"数据未变更，跳过：user={user_id} date={date_val} type={data_type}")
            return existing, False  # False 表示未更新
        
        # 数据有变更，更新
        existing.data = data_dict
        db.flush()
        logger.info(f"数据已更新：user={user_id} date={date_val} type={data_type}")
        return existing, True
    else:
        # 新数据，插入
        record = FitnessData(
            user_id=user_id, date=date_val,
            data_type=data_type, data=data_dict,
        )
        db.add(record)
        db.flush()
        logger.info(f"数据已插入：user={user_id} date={date_val} type={data_type}")
        return record, True
```

### 步骤 4：数据清理策略

**方案**：添加定时清理任务（在 lifespan 中注册）：

```python
# app/core/maintenance.py (新文件)

import os
import time
from datetime import datetime, timedelta
from app.core.database import SessionLocal
from app.models import Session
from app.utils.logger_handler import logger

def cleanup_expired_sessions(days: int = 30):
    """清理超过 N 天未活动的会话"""
    cutoff = datetime.now() - timedelta(days=days)
    db = SessionLocal()
    try:
        deleted = (
            db.query(Session)
            .filter(Session.updated_at < cutoff)
            .delete()
        )
        db.commit()
        if deleted:
            logger.info(f"清理过期会话：{deleted} 条（截止 {cutoff.isoformat()}）")
    except Exception as e:
        db.rollback()
        logger.error(f"清理过期会话失败：{str(e)}")
    finally:
        db.close()

def cleanup_upload_temp_files(hours: int = 24):
    """清理超过 N 小时的临时上传文件"""
    upload_dir = "storage/uploads"
    if not os.path.exists(upload_dir):
        return
    
    cutoff = time.time() - hours * 3600
    cleaned = 0
    for filename in os.listdir(upload_dir):
        filepath = os.path.join(upload_dir, filename)
        if os.path.isfile(filepath) and os.path.getmtime(filepath) < cutoff:
            try:
                os.remove(filepath)
                cleaned += 1
            except OSError:
                pass
    
    if cleaned:
        logger.info(f"清理临时上传文件：{cleaned} 个")

def cleanup_old_logs(days: int = 30):
    """清理超过 N 天的日志文件"""
    log_dir = "logs"
    if not os.path.exists(log_dir):
        return
    
    cutoff = time.time() - days * 3600
    cleaned = 0
    for filename in os.listdir(log_dir):
        if filename.endswith(".log"):
            filepath = os.path.join(log_dir, filename)
            if os.path.isfile(filepath) and os.path.getmtime(filepath) < cutoff:
                try:
                    os.remove(filepath)
                    cleaned += 1
                except OSError:
                    pass
    
    if cleaned:
        logger.info(f"清理旧日志文件：{cleaned} 个")

def run_maintenance():
    """执行所有维护任务"""
    logger.info("开始执行定期维护任务...")
    cleanup_expired_sessions(days=30)
    cleanup_upload_temp_files(hours=24)
    cleanup_old_logs(days=30)
    logger.info("定期维护任务完成")
```

在 `main.py` 的 lifespan 中注册：

```python
import asyncio

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ... 现有启动逻辑 ...
    
    # 启动维护任务调度器
    import threading
    def maintenance_loop():
        while True:
            time.sleep(3600)  # 每小时执行一次
            try:
                run_maintenance()
            except Exception:
                logger.error("维护任务异常", exc_info=True)
    
    maintenance_thread = threading.Thread(target=maintenance_loop, daemon=True)
    maintenance_thread.start()
    
    yield
```

### 步骤 5：数据库迁移工具引入

**当前问题**：表结构变更依赖 `create_all`，无法增量迁移。

**方案**：引入 Alembic：

```bash
pip install alembic
alembic init alembic
```

配置 `alembic/env.py`：

```python
from app.core.database import Base, DATABASE_URL
from app.models import User, UserProfile, Session, Message, FitnessData

target_metadata = Base.metadata
```

之后每次改模型：

```bash
alembic revision --autogenerate -m "add_column_to_user_profiles"
alembic upgrade head
```

### 步骤 6：Coros 子进程管理器

**当前问题**：子进程崩溃后无自动恢复，调用方直接抛异常。

**方案**：

```python
# app/services/coros_client.py 修改

import time

class CorosClient:
    _MAX_RETRIES = 3
    _RETRY_DELAY = 2.0
    
    def _ensure_process_alive(self):
        """检查子进程存活，崩溃则重启"""
        if self.proc.poll() is not None:
            logger.warning(f"coros-mcp 子进程已退出(rc={self.proc.returncode})，尝试重启...")
            self._restart_process()
    
    def _restart_process(self):
        """重启子进程"""
        if self.proc:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        
        for attempt in range(self._MAX_RETRIES):
            try:
                env = os.environ.copy()
                env.setdefault("COROS_EMAIL", os.getenv("COROS_EMAIL", ""))
                env.setdefault("COROS_PASSWORD", os.getenv("COROS_PASSWORD", ""))
                
                self.proc = subprocess.Popen(
                    ["coros-mcp", "serve"],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True, env=env,
                )
                self._req_id = 0
                self._initialize()
                logger.info("coros-mcp 子进程重启成功")
                return
            except Exception as e:
                logger.error(f"coros-mcp 重启失败（第{attempt+1}次）：{str(e)}")
                time.sleep(self._RETRY_DELAY)
        
        raise RuntimeError(f"coros-mcp 子进程重启失败，已尝试 {self._MAX_RETRIES} 次")
    
    def _send(self, method: str, params: dict | None = None) -> dict:
        self._ensure_process_alive()  # ← 新增：发送前检查存活
        # ... 原有逻辑 ...
```

### 步骤 7：测试数据隔离

**当前问题**：测试依赖真实 MySQL 连接，测试数据可能污染开发数据。

**方案**：

**7.1 使用 SQLite 内存数据库做测试**

在 `app/tests/conftest.py` 中：

```python
import pytest
from sqlalchemy import create_engine
from app.core.database import Base, get_db

TEST_DATABASE_URL = "sqlite:///./test.db"

@pytest.fixture(scope="function")
def db_session():
    """每个测试函数使用独立的 SQLite 内存数据库"""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
```

但需要注意：MySQL 特有功能（`CHAR`, `JSON`）在 SQLite 中行为不同，需要针对性适配或使用 Docker 起临时 MySQL。

**7.2 推荐方案：docker compose 起测试 MySQL**

```yaml
# docker-compose.test.yml
services:
  mysql-test:
    image: mysql:8.0
    environment:
      MYSQL_ROOT_PASSWORD: test
      MYSQL_DATABASE: zhitong_test
    ports:
      - "3307:3306"
```

### 步骤 8：向量库版本管理

**当前问题**：知识库更新后无法回滚。

**方案**：

```python
# app/services/vector_store.py 新增

import shutil

VERSION_BACKUP_DIR = "chroma_db/backups"
MAX_BACKUP_VERSIONS = 3

def create_backup(self):
    """为当前向量库创建快照"""
    version = self._get_current_version()
    backup_path = os.path.join(
        self.persist_directory, "backups", f"v{version}"
    )
    os.makedirs(backup_path, exist_ok=True)
    
    # 复制 ChromaDB 数据文件
    for item in os.listdir(self.persist_directory):
        src = os.path.join(self.persist_directory, item)
        dst = os.path.join(backup_path, item)
        if os.path.isfile(src):
            shutil.copy2(src, dst)
        elif os.path.isdir(src) and item != "backups":
            if os.path.exists(dst):
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
    
    # 轮转：保留最近 MAX_BACKUP_VERSIONS 个版本
    backups = sorted(
        [d for d in os.listdir(os.path.join(self.persist_directory, "backups"))
         if d.startswith("v")],
        key=lambda x: int(x[1:]),
    )
    while len(backups) > MAX_BACKUP_VERSIONS:
        oldest = os.path.join(self.persist_directory, "backups", backups.pop(0))
        shutil.rmtree(oldest)
    
    logger.info(f"向量库快照已创建：v{version}")

def restore_from_backup(self, version: int):
    """从指定版本恢复向量库"""
    backup_path = os.path.join(
        self.persist_directory, "backups", f"v{version}"
    )
    if not os.path.exists(backup_path):
        raise ValueError(f"快照 v{version} 不存在")
    
    # 清空当前向量库
    self.reset_store()
    
    # 恢复快照文件
    for item in os.listdir(backup_path):
        src = os.path.join(backup_path, item)
        dst = os.path.join(self.persist_directory, item)
        if os.path.isfile(src):
            shutil.copy2(src, dst)
        elif os.path.isdir(src):
            if os.path.exists(dst):
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
    
    # 重新初始化 Chroma 连接
    self.vector_store = self.create_chroma()
    logger.info(f"向量库已从 v{version} 恢复")
```

在 `load_document` 执行前自动创建备份：

```python
def load_document(self):
    self.create_backup()  # ← 新增：更新知识库前自动备份
    # ... 原有加载逻辑 ...
```

---

## 三、实施检查清单

- [ ] 1. 创建 `app/core/data_contracts.py`（数据集注册表 + 质量检查规则）
- [ ] 2. `models.py` JSON TEXT 列改为 SQLAlchemy JSON 类型
- [ ] 3. `schemas.py` 移除冗余的 JSON 解析 validator
- [ ] 4. `fitness.py` 添加幂等写入函数
- [ ] 5. 创建 `app/core/maintenance.py`（自动清理任务）
- [ ] 6. `main.py` lifespan 注册维护调度器
- [ ] 7. 引入 Alembic，创建初始迁移
- [ ] 8. `coros_client.py` 添加子进程自动恢复
- [ ] 9. `vector_store.py` 添加备份/恢复功能
- [ ] 10. `vector_store.py` `load_document` 前自动备份
- [ ] 11. 测试数据隔离方案实施
- [ ] 12. `.gitignore` 确认 `alembic/versions/` 不排除迁移文件

---

## 四、验收标准

1. `data_contracts.py` 覆盖所有 5 个 active dataset
2. MySQL JSON 类型替代 TEXT 后，`schemas.py` 无需手动 `json.loads`
3. 运动数据重复同步不产生脏数据（幂等验证）
4. 上传文件在 24 小时后自动清理
5. `alembic upgrade head` 可正确建表
6. coros-mcp 子进程异常退出后自动重启（3 次重试）
7. 知识库更新前自动创建快照，可回滚到上一个版本
8. `docker-compose.yml` 可一键启动 MySQL + 应用
