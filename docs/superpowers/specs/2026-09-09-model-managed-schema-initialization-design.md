# 模型直建数据库结构设计

**日期：** 2026-09-09  
**状态：** 已实施并验证  
**决策：** 当服务启动时，若配置的 MySQL 数据库或 ORM 模型对应的表不存在，则自动创建；数据库结构的定义和建表操作由 `app/models.py` 统一拥有，不再使用 Alembic。

## 背景

当前项目用 Alembic 管理表结构，但用户决定改为由 SQLAlchemy 模型直接创建结构。当前目标数据库为 `fitness`，允许清空并重建；既有的 `zhitong` 数据库不在本次操作范围内。

`Base.metadata.create_all()` 只会创建缺失的表，不会删除、重命名或变更已存在表的列。因此它适合当前全新数据库初始化，不提供未来在线表结构迁移能力。

## 目标与约束

1. 删除仓库中的 Alembic 配置、版本脚本和 Python 依赖，并移除运行与学习文档中的迁移指令。
2. 在 `app/models.py` 定义唯一的模型表创建入口，例如 `create_all_tables(engine)`；入口必须在该模块所有模型注册完成后调用 `Base.metadata.create_all()`。
3. 服务生命周期启动时，先确保配置的 MySQL 数据库存在，再调用模型建表入口。
4. 已存在数据库和表时，启动不得改写表定义或删除数据；缺失表必须被补建。
5. 创建 `fitness` 的数据库表并用真实 MySQL 连接验证。只操作 `MYSQL_DATABASE=fitness`，不修改 `zhitong`。
6. 保持当前中文训练经验值实现和既有 API 行为不变。

## 非目标

- 不为已有表执行自动列新增、列类型变更、索引变更或数据回填。
- 不迁移、删除或读取 `zhitong` 中的旧数据。
- 不修改 API 路由、鉴权逻辑或业务模型字段来迁就本次初始化机制。
- 不保留 Alembic 作为备用或并行的迁移路径。

## 模块边界

| 模块 | 职责 |
| --- | --- |
| `app/core/database.py` | 创建引擎和会话、校验数据库名、创建缺失数据库；不定义业务表。 |
| `app/models.py` | 定义全部 ORM 表，并在模块末尾提供 `create_all_tables(engine)`，集中执行 `Base.metadata.create_all(bind=engine)`。 |
| `app/main.py` | 在 FastAPI lifespan 内按顺序调用“确保数据库存在”和“确保模型表存在”，之后才执行运行检查和 RAG 预热。 |

`app/core/database.py` 调用模型入口时必须采用延迟导入，避免 `models.py` 导入 `Base` 造成循环依赖。建表函数本身不接受或读取环境变量，只使用传入的 SQLAlchemy `Engine`，便于单元测试。

## 启动流程

```text
FastAPI lifespan 开始
  -> ensure_database_exists()
       -> CREATE DATABASE IF NOT EXISTS `fitness`
  -> ensure_schema_exists()
       -> 延迟导入 app.models（注册全部 ORM 模型）
       -> models.create_all_tables(engine)
       -> Base.metadata.create_all(bind=engine)
  -> validate_runtime()
  -> warm_rag_retriever()
  -> 开始接收请求
```

为满足“数据库不存在时自动创建”的要求，启动路径不能受 `AUTO_CREATE_DATABASE` 开关阻止。该开关及其文档将移除，启动路径始终针对当前 `MYSQL_DATABASE` 执行幂等创建。

## 删除与重建流程

1. 先用有效 `.env` 配置确认目标是 `fitness`，并列出该数据库中的现有表。
2. 在用户已授权的范围内仅删除 `fitness` 数据库；不执行宽泛删除，也不触碰 `zhitong`。
3. 删除 `alembic/`、`alembic.ini`、迁移相关测试、依赖声明以及 README 中的 Alembic 命令。
4. 增加模型建表函数、数据库编排函数和启动调用。
5. 启动服务或调用同一启动初始化函数，使其创建新的 `fitness` 和全部模型表。

如果重建或验证失败，停止后保留错误证据；代码回退采用 Git 恢复本次明确改动的文件，数据库回退仅重建空 `fitness`，绝不影响旧库。

## 测试与验收

1. 单元测试：模拟 `Base.metadata.create_all`，证明 `models.create_all_tables` 以传入引擎调用它；验证 `ensure_schema_exists` 会加载模型并调用模型入口。
2. 生命周期测试：模拟数据库初始化、运行检查和预热，证明它们按“建库、建表、检查、预热”顺序执行，且任一步失败时不会继续启动。
3. 移除或替换 Alembic 专属迁移测试；源码、依赖、示例配置与运行/学习文档不得再引用 `alembic` 或 `alembic upgrade`。历史设计与计划文档可保留其背景记录。
4. MySQL 集成验证：通过项目配置连接 `fitness`，确认数据库存在，并将 `Base.metadata.tables` 中所有表名与 `information_schema.tables` 中的表名逐一比对。
5. 回归验证：运行原计划指定的中文经验值安全测试、完整安全测试、训练计划 API 测试和 Ruff 检查。

## 风险与运维说明

- `create_all()` 不会把模型的新字段同步到已经存在的表。未来结构变更需要明确的数据维护方案；不能误以为重启服务会自动升级表。
- 多实例同时首次启动时，`CREATE DATABASE IF NOT EXISTS` 和 `create_all()` 都是幂等操作，但应由 MySQL 处理并发 DDL；失败需要让应用启动失败而非静默忽略。
- 启动建库账户需要具有 `CREATE` 权限。若没有该权限，服务必须报告明确错误并拒绝启动。
