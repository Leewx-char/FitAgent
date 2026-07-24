# FitAgent 架构说明

> 本文聚焦**系统分层**与**Coros MCP 集成边界**。
> UI 设计规范见 `design.md`，关键技术决策见 `decisions.md`。

---

## 一、系统总览

FastAPI 后端（标准分层）+ Vue3 前端，后端内部是**两个相对独立的子系统**：

```
                         ┌───────────────────────────┐
   Vue3 前端  ──────────▶│         FastAPI            │
   (Naive UI)            │                            │
                         │  ┌──────────────────────┐  │
                         │  │  A. 对话 / Agent 子系统 │  │
   /api/chat (SSE) ─────▶│  │  ReactAgent + 工具     │  │──▶ 通义千问 LLM
                         │  │  RAG(Qdrant+BM25)      │  │
                         │  └──────────────────────┘  │
                         │  ┌──────────────────────┐  │
   /api/fitness/* ──────▶│  │  B. 运动数据 / Coros   │  │──▶ coros-mcp 子进程
                         │  │  CorosClient(MCP)      │  │     (外部设备数据)
                         │  └──────────────────────┘  │
                         │           │  │             │
                         └───────────┼──┼─────────────┘
                                     ▼  ▼
                                   MySQL (users/sessions/messages
                                          user_profiles/fitness_data)
```

### 后端分层

| 层 | 目录 | 职责 |
|---|---|---|
| API | `app/api/routers/` | 路由、请求校验、鉴权、SSE |
| 依赖 | `app/core/` | `deps.py`(依赖注入)、`auth.py`(JWT)、`database.py`、`request_context.py` |
| 服务 | `app/services/` | ReactAgent、RAG、CorosClient、中间件等业务逻辑 |
| 数据 | `app/models.py` + `app/schemas.py` | ORM 模型 / Pydantic 校验 |
| 工具 | `app/utils/` | logger、audit、config、path 等横切能力 |

### 两个子系统的关系

- **A（对话/Agent）**：`/api/chat` → `ReactAgent` → 工具（RAG、天气、用户画像…）→ LLM 流式返回。
- **B（运动数据/Coros）**：`/api/fitness/*` → `CorosClient` 拉取高驰设备数据 → 落 `fitness_data` 表 → 前端 Dashboard 展示。
- **二者当前只在数据库层相邻**（各写各的表），**不互相调用**：Agent 的工具集里没有 coros 工具，fitness 路由也不经过 Agent。这是刻意的边界——运动数据同步是「设备→DB」的 ETL，与对话推理解耦，任一侧故障不影响另一侧。

---

## 二、Coros MCP 集成边界（重点）

高驰（Coros）运动手表的数据通过一个**独立的 MCP 服务器 `coros-mcp`** 获取。它是**外部子项目**（仓库根 `.gitignore` 已忽略 `coros-mcp/`，通过 `pip` 以 `coros-mcp` 命令形式安装），FitAgent 只作为它的**客户端**。

集成通过三道边界隔离，任一道都为了「让不可靠的外部依赖不拖垮主应用」。

### 边界一：进程边界（`CorosClient` ↔ `coros-mcp serve` 子进程）

`app/services/coros_client.py` 把 MCP 协议细节全部封装，对外只暴露三个业务方法。

- **传输**：启动 `coros-mcp serve` 子进程，走 **stdin/stdout 上的 JSON-RPC 2.0**（MCP 标准传输）。
- **环境隔离**：子进程有独立环境变量空间，构造时**显式拷贝** `COROS_EMAIL/PASSWORD/REGION` 传入（当前进程读到的 `.env` 子进程未必有）。
- **握手**：MCP 要求先 `initialize` 自报家门，再发 `notifications/initialized` 通知，少一步服务端不理会。
- **调用**：`tools/call` 调用远端工具，真正的业务 JSON 在 `response.result.content[0].text` 里（需二次 `json.loads`）。

对外暴露的方法（`fitness.py` 只认这三个）：

| 方法 | MCP 工具 | 含义 |
|---|---|---|
| `get_daily_metrics(weeks=4)` | get_daily_metrics | HRV / 静息心率 / 训练负荷 / VO2max |
| `get_sleep_data(weeks=4)` | get_sleep_data | 深睡 / 浅睡 / REM 时长 |
| `list_activities(start, end)` | list_activities | 时间段内的运动记录 |

### 边界二：生命周期（惰性创建 + 单例）

`app/core/deps.py` 的 `get_coros()` 用 `@lru_cache(maxsize=1)` 提供**进程级单例**：

- **为什么惰性**：子进程启动 + MCP 握手耗时 **1-2s**。放在 app 启动时会拖慢冷启动，且没用 fitness 的请求根本不需要它——所以第一次真正用到时才创建。
- **为什么单例**：一个子进程可复用，不必每请求重启。
- **为什么用 `lru_cache` 而非模块级变量**：`lru_cache` 线程安全。模块级 `if _x is None: _x = ...` 在并发首次访问时有竞态窗口（1-2s 很长），可能创建多个子进程 → 子进程泄漏。

> 对比：`get_agent()` 是**每请求新建**（实例级隔离，避免 LangGraph 状态串扰），底层 LLM 连接靠 `get_chat_model()` 的 `lru_cache` 复用。两者取舍不同：Coros 单例复用子进程，Agent 每请求隔离。

### 边界三：故障隔离（超时 + 存活检查 + 502）

外部子进程随时可能挂、可能卡，`CorosClient._send` 三重加固：

1. **发送前存活检查**：`proc.poll()` 非 None → 子进程已退出，直接抛 `RuntimeError`。
2. **读取超时**：`select.select([stdout], [], [], 30.0)`，30s 无响应就抛超时，**不永久阻塞**请求线程。
3. **读取前二次存活检查**：select 返回后再确认子进程没在响应前退出。

上层 `fitness.py` 的 `/sync` 把 coros 每类数据的拉取**各自 try/except**，失败转 **HTTP 502**（上游服务错误）——coros 挂了只让本次同步失败，**不会 500、不拖垮整个 app**，也不影响对话子系统。

### 数据流：写路径 vs 读路径

**写路径（同步，依赖 coros）**：
```
POST /api/fitness/sync
  → get_coros() 拉 daily_metrics / sleep / activities
  → _upsert_fitness() 按 (user_id, date, data_type) 唯一键 upsert 进 fitness_data
  → 返回 {upserted: N}
```

**读路径（展示，不碰 coros）**：
```
GET /api/fitness/daily|sleep|activities
  → 只查本地 fitness_data 表
  → 前端 Dashboard 渲染
```

**关键点**：读路径**完全不依赖 coros**。即使 coros-mcp 不可用，用户仍能看到上次同步的历史数据。同步是显式触发的写操作，展示是纯本地读——这个读写分离让 Dashboard 的可用性不受外部依赖影响。

---

## 三、测试边界

`app/tests/conftest.py` 的 `coros_mock` fixture 用 `app.dependency_overrides[get_coros]` 注入 MagicMock，**测试永不真启动子进程**——既快又稳定，也不需要真实高驰账号。这印证了「`get_coros` 依赖注入」这道边界的价值：外部依赖可被一行替换。

---

## 四、关键文件索引

| 关注点 | 文件 |
|---|---|
| MCP 客户端封装（进程/协议/超时） | `app/services/coros_client.py` |
| 单例惰性注入 | `app/core/deps.py` (`get_coros`) |
| fitness REST 端点（sync + 读） | `app/api/routers/fitness.py` |
| fitness 数据表（唯一键 upsert） | `app/models.py` (`FitnessData`) |
| 外部 MCP 子项目（gitignore） | `coros-mcp/`（`pip install` 的独立包） |
| 测试替身 | `app/tests/conftest.py` (`coros_mock`) |
