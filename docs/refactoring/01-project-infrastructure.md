# 01 - 项目基础设施重构方案

> **状态**: 待实施  
> **优先级**: P0（阻塞新开发者入场的启动问题）  
> **预计工时**: 3-4 天  
> **审查状态**: 已通过 3 方对抗性审查（Windows 新手 / 后端开发者 / DevOps）

---

## 一、问题诊断

### 1.1 README.md 问题

| 问题 | 现状 | 影响 |
|------|------|------|
| 启动命令仅覆盖 macOS/Linux | `python -m venv .venv && source .venv/bin/activate` | Windows 用户无法照做 |
| 数据库初始化缺失 | 没有 `CREATE DATABASE zhitong` 步骤 | 新用户启动后数据库连接失败 |
| python-magic 跨平台问题 | `python-magic==0.4.27` 在 Windows 需要 `python-magic-bin` | Windows 用户 `pip install` 成功但 `import` 崩溃 |
| pdf2image 缺少 poppler | Windows 无预装 poppler | 上传 PDF 时报 "Is poppler installed and in PATH?" |
| PowerShell 执行策略 | 默认 Restricted 拦截 `.ps1` 脚本 | `Activate.ps1` 无法执行，虚拟环境激活失败 |
| 项目目录树残留旧名 | 显示 "智扫通/" 而非 "FitAgent/" | 混淆新成员 |
| 技术栈信息过时 | 没有提及依赖版本号 | 版本漂移后无法复现 |
| 缺少架构文档链接 | 根目录有 ARCHITECTURE.md 但 README 未提及 | 文档不可发现 |
| 没有故障排查章节 | 无 | 用户遇到问题无处查找 |
| `mysql` 命令不在 PATH | Windows 用户安装路径各异 | 按 README 敲命令找不到 mysql.exe |

### 1.2 配置问题

| 问题 | 位置 | 影响 |
|------|------|------|
| `rag.yml` 文件职责混乱 | 名为 `rag` 但存 LLM/Embedding 模型配置 | 应改为 `models.yml` |
| `agent.yml` 几乎为空 | 仅 1 行 `external_data_path`，代码有引用但功能未实现 | 死配置，需先清理代码再删文件 |
| 模块级配置变量无法热更新 | `config_handler.py:43-47` 在导入时一次性赋值 | 测试中改配置困难 |
| `yaml.FullLoader` 安全漏洞 | `config_handler.py` 5 处使用，可执行任意 Python 对象 | 开发/测试环境也应避免不安全加载 |
| 缺少日志级别配置 | `logger_handler.py` 硬编码 `console_level=logging.INFO` | 开发/调试切换需改代码 |
| `DEBUG_MODE` 硬编码 | `exception_handlers.py:23` `DEBUG_MODE = True` | 内部错误栈暴露到前端响应 |

### 1.3 项目结构问题

| 问题 | 说明 |
|------|------|
| 文档散落在根目录 | `ARCHITECTURE.md`, `DECISIONS.md`, `DESIGN.md`, `PRODUCT.md` 应在 `docs/` |
| 缺少 `pyproject.toml` | AGENTS.md 模板要求但未实现 |
| 缺少 lint/format 配置 | 无 `[tool.ruff]` 等 |
| `frontend/README.md` 是 Vite 模板 | 内容为 "Vue 3 + Vite" 通用模板 |
| `streamlit` 遗留依赖 | `requirements.txt:43` 标注 legacy 但仍为正式依赖 |
| `main.py` 标题为旧名 | `app/main.py:47` 仍为 `title="智扫通 API"` |
| `ALLOWED_ORIGINS` 含废弃端口 | `.env.example:18` 默认值含 `localhost:8501`（streamlit 端口） |

### 1.4 数据库初始化问题

- `database.py` 定义了 MySQL 连接但 `main.py:lifespan` 只做 `create_all`（建表），不检查数据库是否存在
- 如果 `zhitong` 数据库不存在，应用启动即报错，错误信息不友好
- `DATABASE_URL` 用 `+` 拼接，密码含特殊字符（`@`, `/`, `:`, `%`）时 URL 解析失败

### 1.5 日志问题

- `logger_handler.py` 不读取 `LOG_LEVEL` 环境变量
- 日志按天生成文件（`agent_20250101.log`），永不清理，磁盘持续膨胀

---

## 二、重构方案

### 步骤 1：README.md 重写

**目标**：新开发者（无论 macOS/Windows/Linux）能按 README 在 15 分钟内启动项目。

```markdown
# FitAgent — 多Agent个性化运动教练

基于大语言模型的智能运动教练系统，结合 RAG 知识库检索、用户画像和多模态健康文档上传，提供个性化健身指导。

## 环境要求

| 依赖 | 版本 | 说明 |
|------|------|------|
| Python | 3.11+ | |
| Node.js | 20+ | |
| MySQL | 8.0+ | |
| **[Windows]** Visual C++ | 14.0+ | 编译 python-magic-bin 所需 |
| **[Windows]** poppler | 最新版 | pdf2image 依赖，[下载地址](https://github.com/oschwartz10612/poppler-windows/releases)，将 `bin/` 加入系统 PATH |

## 项目结构

```
FitAgent/
├── app/                        # 后端代码（FastAPI 标准结构）
│   ├── main.py                 # 应用入口 + CORS + lifespan
│   ├── models.py               # ORM 模型（User/Session/Message/UserProfile）
│   ├── schemas.py              # Pydantic 请求/响应模型
│   ├── core/                   # 基础设施
│   │   ├── database.py         # MySQL 连接 + SessionLocal
│   │   ├── auth.py             # JWT 认证 + bcrypt 密码哈希
│   │   └── deps.py             # 依赖注入（get_db, get_agent）
│   ├── api/                    # HTTP 层
│   │   ├── routers/            # 路由（auth/chat/sessions/messages/profile/upload/fitness）
│   │   ├── exception_handlers.py
│   │   └── response.py         # 统一响应格式
│   ├── services/               # 业务逻辑层
│   │   ├── factory.py          # LLM/VL/Embedding 模型工厂
│   │   ├── react_agent.py      # LangGraph ReAct Agent（7个工具）
│   │   ├── agent_tools.py      # 工具定义
│   │   ├── middleware.py       # Agent 中间件（监控/提示词切换）
│   │   ├── rag_service.py      # RAG 检索服务
│   │   ├── vector_store.py     # ChromaDB 向量库
│   │   ├── bm25_retriever.py   # BM25 关键词检索
│   │   └── doc_parser.py       # 多模态文档解析
│   └── utils/                  # 无状态工具函数
│       ├── config_handler.py   # YAML 配置加载
│       ├── logger_handler.py   # 日志初始化
│       ├── file_handler.py     # 文件 IO + 文本处理
│       ├── path_tool.py        # 路径解析
│       ├── prompt_loader.py    # 提示词加载
│       └── bootstrap.py        # 启动校验
├── config/                     # YAML 配置（models/chroma/synonyms/prompts）
├── prompts/                    # 系统提示词 + 健康数据提取提示词
├── data/                       # 知识库（5个txt）
├── frontend/                   # Vue 3 前端
│   └── src/
│       ├── views/              # Login/Register/Onboarding/Chat/Profile/Dashboard
│       ├── components/         # Sidebar
│       └── stores/             # auth/chat/profile
├── docs/                       # 架构文档
│   ├── architecture.md
│   ├── decisions.md
│   ├── design.md
│   └── product.md
├── storage/uploads/            # 上传文件临时目录
├── chroma_db/                  # ChromaDB 持久化存储
└── DECISIONS.md                # 项目决策记录
```

## Windows 启动指南

```powershell
# 0. 允许 PowerShell 脚本执行（仅首次需要）
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

# 1. 克隆并配置环境变量
cp .env.example .env
# 编辑 .env，填入 DASHSCOPE_API_KEY、MySQL 配置、JWT_SECRET_KEY

# 2. 创建数据库（MySQL 命令行或 GUI 工具）
# 如果 mysql 不在 PATH 中，使用完整路径，例如：
# & "C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe" -u root -p -e "CREATE DATABASE IF NOT EXISTS zhitong CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
mysql -u root -p -e "CREATE DATABASE IF NOT EXISTS zhitong CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"

# 3. Python 环境 & 依赖
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install python-magic-bin   # Windows 必需，提供 libmagic DLL

# 4. 启动后端
uvicorn app.main:app --reload --port 8000

# 5. 启动前端（新终端）
cd frontend
npm install
npm run dev
```

## macOS / Linux 启动指南

```bash
# 1. 克隆并配置环境变量
cp .env.example .env

# 2. 创建数据库
mysql -u root -p -e "CREATE DATABASE IF NOT EXISTS zhitong CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"

# 3. Python 环境
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 4. 启动后端
uvicorn app.main:app --reload --port 8000

# 5. 启动前端
cd frontend && npm install && npm run dev
```

浏览器打开 http://localhost:5173

## 常见问题

### Q: Windows 上 `.\.venv\Scripts\Activate.ps1` 报错 "running scripts is disabled"
**A**: PowerShell 默认禁止执行脚本。以管理员身份运行 PowerShell，执行：
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```
或者使用 cmd 替代：在 cmd 中运行 `.venv\Scripts\activate.bat`

### Q: Windows 上 `pip install -r requirements.txt` 后启动报 `ImportError: No module named 'magic'`
**A**: `python-magic` 在 Windows 上需要额外的 DLL。执行：
```
pip install python-magic-bin
```

### Q: 上传 PDF 时报 "Is poppler installed and in PATH?"
**A**: Windows 需要手动安装 poppler。从 [poppler-windows](https://github.com/oschwartz10612/poppler-windows/releases) 下载最新版，解压后将 `bin/` 目录加入系统 PATH 环境变量，重启终端即可。

### Q: 启动报错 "Can't connect to MySQL server"
**A**: 
1. 确认 MySQL 服务已启动（Windows：服务管理器查找 MySQL80）
2. 确认已创建 zhitong 数据库：
```sql
CREATE DATABASE IF NOT EXISTS zhitong CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```
3. 检查 `.env` 中的 `MYSQL_HOST` 和 `MYSQL_PORT` 是否正确

### Q: `mysql` 命令找不到 (Windows)
**A**: MySQL 安装路径默认不在 PATH 中。使用完整路径运行：
```powershell
& "C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe" -u root -p
```
或者将 MySQL bin 目录（如 `C:\Program Files\MySQL\MySQL Server 8.0\bin\`）添加到系统 PATH。

### Q: ChromaDB 向量库损坏
**A**: 删除 `chroma_db/` 目录后重启，应用会自动重建向量库。

### Q: `npm install` 失败或前端启动报错
**A**: 
1. 确认 Node.js 版本 ≥ 20：`node --version`
2. 删除 `node_modules/` 和 `package-lock.json`，重新 `npm install`
3. Windows 防火墙弹出时点"允许"
```

---

### 步骤 2：配置文件修复

#### 2.1 重命名 `config/rag.yml` → `config/models.yml`

**文件重命名**：
```
config/rag.yml → config/models.yml
```

**`config/models.yml` 内容（不变，模型名确认为 deepseek-v4-pro）**：
```yaml
chat_model_name: deepseek-v4-pro
embedding_model_name: text-embedding-v4
vl_model_name: qwen-vl-plus
chat_max_tokens: 4096
```

**`config_handler.py` 修改**：

```python
# 原函数名 load_rag_config → load_models_config
def load_models_config(
        config_path: str = get_abs_path("config/models.yml"),  # 路径改为 models.yml
        encoding: str = "utf-8",
):
    with open(config_path, "r", encoding=encoding) as f:
        return yaml.load(f, Loader=yaml.SafeLoader)  # FullLoader → SafeLoader（安全修复）
```

#### 2.2 删除 `config/agent.yml`

**步骤**：
1. 删除 `config_handler.py` 中的 `load_agent_config()` 函数（`:29-34`）
2. 删除 `config_handler.py` 中的模块级变量 `agent_conf = load_agent_config()`（`:47`）
3. 删除 `config/agent.yml` 文件

> 确认：全仓搜索 `agent_conf` 无其他文件引用，`external_data_path` 无任何代码消费，可安全删除。

#### 2.3 配置改为惰性加载 + lru_cache

`config_handler.py` 完整重构：

```python
"""
YAML 配置文件加载模块。

所有配置通过 @lru_cache 惰性加载，首次调用时读取 YAML 文件并缓存。
测试中可通过 get_xxx_config.cache_clear() 重置。
"""
import yaml
from functools import lru_cache
from app.utils.path_tool import get_abs_path

def _load_yaml(relative_path: str) -> dict:
    """通用 YAML 加载：SafeLoader 替代 FullLoader，避免任意代码执行"""
    abs_path = get_abs_path(relative_path)
    with open(abs_path, "r", encoding="utf-8") as f:
        return yaml.load(f, Loader=yaml.SafeLoader)

@lru_cache(maxsize=1)
def get_models_config() -> dict:
    """LLM / Embedding / VL 模型配置"""
    return _load_yaml("config/models.yml")

@lru_cache(maxsize=1)
def get_chroma_config() -> dict:
    """ChromaDB 向量库 + 文本切分配置"""
    return _load_yaml("config/chroma.yml")

@lru_cache(maxsize=1)
def get_synonyms_config() -> dict:
    """同义词扩展 + 归一化 + 停用词配置"""
    return _load_yaml("config/synonyms.yml")

@lru_cache(maxsize=1)
def get_prompts_config() -> dict:
    """提示词文件路径映射"""
    return _load_yaml("config/prompts.yml")
```

> **注意**：此处不再保留模块级变量。所有调用处改为函数调用形式（见步骤 2.4）。

#### 2.4 全量引用追踪与修改

`rag_conf` → `get_models_config()` 涉及的**所有文件**：

| 文件 | 行号 | 当前引用方式 | 改动 |
|------|------|------------|------|
| `app/services/factory.py` | `:7` | `from app.utils.config_handler import rag_conf` | `from app.utils.config_handler import get_models_config` |
| `app/services/factory.py` | `:19` | `rag_conf["chat_model_name"]` | `get_models_config()["chat_model_name"]` |
| `app/services/factory.py` | `:23` | `rag_conf["embedding_model_name"]` | `get_models_config()["embedding_model_name"]` |
| `app/services/factory.py` | `:45` | `model="qwen-vl-plus"`（硬编码） | `model=get_models_config()["vl_model_name"]` |
| `app/utils/bootstrap.py` | `:2` | `from ... import ... rag_conf` | `from ... import ... get_models_config` |
| `app/utils/bootstrap.py` | `:28` | `rag_conf.get(key)` | `get_models_config().get(key)` |

`chroma_conf` → `get_chroma_config()` 涉及的**所有文件**：

| 文件 | 行号 | 当前引用方式 | 改动 |
|------|------|------------|------|
| `app/utils/bootstrap.py` | `:2,15,33` | `import chroma_conf` | `import get_chroma_config` + 调用替换 |
| `app/services/vector_store.py` | `:8,18,20,31,32,36,37,41,42,47,56,74,107,172,173,182` | `import chroma_conf` | `from ... import get_chroma_config` + 所有引用处改为函数调用 |
| `app/services/rag_service.py` | `:18,30,31,32` | `import chroma_conf` | `from ... import get_chroma_config` + 所有引用处改为函数调用 |

`prompts_conf` → `get_prompts_config()` 涉及的文件：

| 文件 | 行号 | 当前引用方式 | 改动 |
|------|------|------------|------|
| `app/utils/prompt_loader.py` | `:2,8,21,34` | `import prompts_conf` | `from ... import get_prompts_config` + 调用替换 |
| `app/utils/bootstrap.py` | `:2,14,15,38,39` | `import prompts_conf` | `import get_prompts_config` + 调用替换 |

`synonyms_conf` → `get_synonyms_config()` 涉及的文件：

| 文件 | 行号 | 当前引用方式 | 改动 |
|------|------|------------|------|
| `app/services/rag_service.py` | `:18,35,36,37` | `import synonyms_conf` | `from ... import get_synonyms_config` + 调用替换 |

> **注意**：`rag_service.py` 的 `__init__` 中 `self.synonym_map = synonyms_conf.get(...)` 等需要在 `__init__` 内改为函数调用 `self.synonym_map = get_synonyms_config().get(...)`。

#### 2.5 `.env.example` 补充

```bash
# ===== Agent 配置 =====
AGENT_USER_CITY=广州
AGENT_USER_ID=1002

# ===== MySQL 配置 =====
MYSQL_USER=root
MYSQL_PASSWORD=your_password_here
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_DATABASE=zhitong

# ===== JWT 配置 =====
JWT_SECRET_KEY=your_random_secret_key_here
JWT_ALGORITHM=HS256
JWT_EXPIRE_MINUTES=1440

# ===== CORS =====
ALLOWED_ORIGINS=http://localhost:5173

# ===== DashScope (通义千问) =====
DASHSCOPE_API_KEY=your_dashscope_api_key_here

# ===== 日志 (新增) =====
LOG_LEVEL=INFO                # DEBUG/INFO/WARNING/ERROR

# ===== 调试开关 (新增) =====
DEBUG_MODE=false
```

> 变更点：`ALLOWED_ORIGINS` 默认值移除 `http://localhost:8501`（streamlit 端口）

---

### 步骤 3：项目结构调整

#### 3.1 文档归入 docs/

```bash
git mv ARCHITECTURE.md docs/architecture.md
git mv DECISIONS.md docs/decisions.md
git mv DESIGN.md docs/design.md
git mv PRODUCT.md docs/product.md
```

**必须同步更新的交叉引用**：

| 源文件 | 原链接 | 改为 |
|--------|--------|------|
| `README.md:86` | `[DECISIONS.md](./DECISIONS.md)` | `[DECISIONS.md](./docs/decisions.md)` |
| `docs/architecture.md:4` | `详见 DESIGN.md` | `详见 docs/design.md` |
| `docs/architecture.md` | `详见 DECISIONS.md` | `详见 docs/decisions.md` |
| `docs/design.md:101,130,230` | `PRODUCT.md` | `docs/product.md` |

#### 3.2 添加 pyproject.toml

```toml
[project]
name = "fitagent"
version = "2.0.0"
description = "多Agent个性化运动教练"
requires-python = ">=3.11"
dependencies = [
    # 版本号与 requirements.txt 保持一致（== 精确锁定）
    "fastapi==0.136.3",
    "uvicorn==0.47.0",
    "starlette==1.0.0",
    "pydantic==2.13.4",
    "pydantic-settings==2.14.1",
    "python-dotenv==1.2.2",
    "SQLAlchemy==2.0.49",
    "PyMySQL==1.2.0",
    "python-jose==3.5.0",
    "passlib==1.7.4",
    "bcrypt==4.0.1",
    "python-multipart==0.0.29",
    "dashscope==1.25.18",
    "langchain==1.3.1",
    "langchain-core==1.4.0",
    "langchain-community==0.4.1",
    "langchain-chroma==1.1.0",
    "langchain-text-splitters==1.1.2",
    "langgraph==1.2.0",
    "chromadb==1.5.9",
    "httpx==0.28.1",
    "httpx-sse==0.4.3",
    "sse-starlette==3.4.4",
    "pypdf==6.11.0",
    "python-magic==0.4.27",
    "pdf2image==1.17.0",
    "Pillow==11.3.0",
    "rank-bm25==0.2.2",
    "slowapi==0.1.9",
]

[project.optional-dependencies]
dev = [
    "pytest==8.4.2",
    "pytest-asyncio==0.26.0",
    "ruff>=0.9",
    "mypy>=1.15",
]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
# 分阶段启用规则，避免首次运行产生过多告警
# 阶段 1（立即）：E, F  -- 基础错误检查
# 阶段 2（后续）：I, UP -- 导入排序 + pyupgrade
# 阶段 3（后续）：N, W, B, C4, SIM -- 命名+代码质量
select = ["E", "F"]

[tool.ruff.lint.pydocstyle]
convention = "google"

[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["app/tests"]
```

> **ruff 策略说明**：首次仅开 `E` + `F`（预估 < 50 条告警），后续 PR 逐步启用 `I` → `UP` → `N` → `W` → `B/C4/SIM`。避免一次性 100+ 条告警。

> **版本策略说明**：`pyproject.toml` 与 `requirements.txt` 保持一致使用 `==` 精确版本锁定，确保 `pip install -e .` 和 `pip install -r requirements.txt` 产生相同的依赖树。

#### 3.3 删除 pytest.ini

`pyproject.toml` 已有 `[tool.pytest.ini_options]`，删除根目录的 `pytest.ini` 避免重复配置。

#### 3.4 更新 .gitignore

在现有基础上补充：

```gitignore
# 模型文件（AGENTS.md 要求）
models/

# IDE
.idea/
.vscode/
*.iml
```

> 当前 `.gitignore` 已覆盖 `data/` `logs/` `chroma_db/` `storage/` `.env` `__pycache__/` `.venv/` `node_modules/` `dist/`，仅缺 `models/`。

---

### 步骤 4：修复 Windows 兼容性

#### 4.1 python-magic 跨平台

**方案 A（主方案）**：`requirements.txt` 添加注释，README 安装步骤中直接写明：

`requirements.txt` 修改：
```
python-magic==0.4.27
# Windows 用户额外执行：pip install python-magic-bin==0.4.27
```

README Windows 启动步骤第 3 步已直接包含 `pip install python-magic-bin`。

**方案 B（兜底）**：`doc_parser.py` 添加友好错误提示：

```python
# app/services/doc_parser.py 修改
try:
    import magic
except ImportError:
    raise ImportError(
        "缺少 python-magic 依赖。"
        "Windows 用户请执行：pip install python-magic-bin\n"
        "macOS/Linux 用户请执行：brew install libmagic (macOS) 或 apt install libmagic1 (Linux)"
    )
```

#### 4.2 PowerShell 执行策略

README Windows 启动步骤第 0 步已包含：

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

或在 FAQ 中提供 cmd 替代方案（`cmd /k ".venv\Scripts\activate.bat"`）。

#### 4.3 pdf2image 的 poppler 依赖

README "环境要求" 表格已添加 Windows 额外需求行，标注需手动安装 poppler 并加入 PATH。

FAQ 新增对应条目。

#### 4.4 coros_client.py 的 select.select() 兼容性（存量 bug，本次记录不改）

`app/services/coros_client.py:73` 使用 `select.select()` 对 pipe 做超时控制，Windows 上 `select.select()` 仅支持 socket。

**处置**：本方案仅记录此问题。实际修改放入 04-LLMAgent 对话方案（coros_client 归属于工具调用层）。当前影响面：Windows 用户无法使用 COROS 运动数据同步功能，不影响其他模块。

#### 4.5 DATABASE_URL 特殊字符密码

```python
# app/core/database.py 修改
from urllib.parse import quote_plus

DATABASE_URL = (
    f"mysql+pymysql://{MYSQL_USER}:{quote_plus(MYSQL_PASSWORD)}"
    f"@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DATABASE}?charset=utf8mb4"
)
```

---

### 步骤 5：数据库自动建库

#### 5.1 database.py 新增函数

```python
# app/core/database.py 新增

def ensure_database_exists():
    """如果 zhitong 数据库不存在，自动创建。仅本地开发环境使用。"""
    import pymysql
    try:
        conn = pymysql.connect(
            host=MYSQL_HOST,
            port=int(MYSQL_PORT),
            user=MYSQL_USER,
            password=MYSQL_PASSWORD,
            charset='utf8mb4',
        )
    except pymysql.Error as e:
        raise RuntimeError(
            f"无法连接到 MySQL ({MYSQL_HOST}:{MYSQL_PORT})，请确认 MySQL 服务已启动。\n"
            f"错误详情: {e}"
        ) from e

    try:
        with conn.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{MYSQL_DATABASE}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
    finally:
        conn.close()
```

#### 5.2 main.py lifespan 调用

```python
# app/main.py 修改

@asynccontextmanager
async def lifespan(app: FastAPI):
    issues = validate_runtime()
    if issues:
        for issue in issues:
            print(f"[启动检查失败] {issue}")
        raise RuntimeError(f"启动检查未通过，共 {len(issues)} 个问题，请修复后重试")
    
    ensure_database_exists()      # 新增：自动创建数据库
    Base.metadata.create_all(bind=engine)
    yield
```

---

### 步骤 6：异常处理修复

#### 6.1 DEBUG_MODE 改为环境变量

```python
# app/api/exception_handlers.py:23 修改
import os

DEBUG_MODE = os.getenv("DEBUG_MODE", "false").lower() == "true"
# 默认 false，需要调试时在 .env 设置 DEBUG_MODE=true
```

#### 6.2 修复 main.py 标题和 ALLOWED_ORIGINS

```python
# app/main.py:47
app = FastAPI(title="FitAgent API", version="2.0.0", lifespan=lifespan)

# app/main.py:57 — 不再包含 8501（streamlit 端口）
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:5173").split(",")
```

---

### 步骤 7：日志优化

#### 7.1 logger_handler.py 接入环境变量

```python
# app/utils/logger_handler.py 修改

import os

def get_logger(
        name: str = "agent",
        console_level: int = None,
        file_level: int = None,
        log_file = None,
) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    if logger.handlers:
        return logger

    # 读取环境变量 LOG_LEVEL，默认 INFO
    if console_level is None:
        level_name = os.getenv("LOG_LEVEL", "INFO").upper()
        console_level = getattr(logging, level_name, logging.INFO)
    if file_level is None:
        level_name = os.getenv("LOG_LEVEL", "DEBUG").upper()
        file_level = getattr(logging, level_name, logging.DEBUG)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(console_level)
    console_handler.setFormatter(DEFAULT_LOG_FORMAT)
    logger.addHandler(console_handler)

    # 文件日志
    if not log_file:
        log_file = os.path.join(LOG_ROOT, f"{name}_{datetime.now().strftime('%Y%m%d')}.log")

    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(file_level)
    file_handler.setFormatter(DEFAULT_LOG_FORMAT)
    logger.addHandler(file_handler)

    return logger
```

#### 7.2 日志轮转策略（P2，后续方案跟进）

当前日志按天生成永不清理。后续方案（05-数据治理）中将添加 `TimedRotatingFileHandler`，保留 30 天，单文件最大 10MB。

> 不在本次改动范围，记录以备后续。

---

### 步骤 8：移除遗留依赖和死代码

#### 8.1 移除 streamlit 依赖

```diff
- # Frontend (Streamlit - legacy)
- streamlit==1.57.0
```

确认：全仓搜索无 `import streamlit` 残留，`ALLOWED_ORIGINS` 已移除 `8901` 端口。

#### 8.2 移除 config/agent.yml 关联代码

见步骤 2.2，`config_handler.py` 中删除 `load_agent_config()` 函数和 `agent_conf` 变量。

#### 8.3 更新 frontend/README.md

替换 Vite 模板为项目专属内容：

```markdown
# FitAgent 前端

基于 Vue 3 + Vite + Naive UI 构建的运动教练应用前端。

## 启动

```bash
npm install
npm run dev
```

浏览器打开 http://localhost:5173

## 技术栈

- Vue 3 (Composition API + &lt;script setup&gt;)
- Vite
- Pinia (状态管理)
- Naive UI (组件库)
- vue-router (路由)
- ECharts (图表)
- marked + DOMPurify (Markdown 渲染)
- Axios (HTTP 客户端)

## 项目结构

```
src/
├── main.js          # 入口
├── App.vue          # 根组件
├── router/          # 路由配置
├── stores/          # Pinia 状态管理
├── api/             # Axios 封装
├── views/           # 页面组件
├── components/      # 通用组件
└── assets/          # 静态资源
```
```

---

## 三、实施检查清单

### 阶段 1：配置与基础设施（可并行）

- [ ] 1.1 `config/rag.yml` → `config/models.yml` 重命名
- [ ] 1.2 `config_handler.py` 函数重命名 + `yaml.FullLoader` → `yaml.SafeLoader`
- [ ] 1.3 `config_handler.py` 改为惰性加载 + `lru_cache`（删除模块级变量）
- [ ] 1.4 `config_handler.py` 删除 `load_agent_config()` 函数和 `agent_conf` 变量
- [ ] 1.5 删除 `config/agent.yml`
- [ ] 1.6 `.env.example` 补充 `LOG_LEVEL`, `DEBUG_MODE`，移除 `localhost:8501`
- [ ] 1.7 `exception_handlers.py` `DEBUG_MODE` 改为 `os.getenv("DEBUG_MODE", "false").lower() == "true"`
- [ ] 1.8 `main.py` 标题改为 `"FitAgent API"`，`ALLOWED_ORIGINS` 移除 8501
- [ ] 1.9 `database.py` `DATABASE_URL` 使用 `quote_plus()` 编码密码
- [ ] 1.10 `database.py` 新增 `ensure_database_exists()`（含 try/except）
- [ ] 1.11 `main.py` lifespan 调用 `ensure_database_exists()`
- [ ] 1.12 `logger_handler.py` 读取 `LOG_LEVEL` 环境变量

### 阶段 2：全量引用跟踪（依赖阶段 1）

- [ ] 2.1 `factory.py` 5 处引用改为 `get_models_config()`（含 `:45` vl_model_name）
- [ ] 2.2 `bootstrap.py` 3 种配置引用全部改为函数调用
- [ ] 2.3 `vector_store.py` 16 处 `chroma_conf` 改为 `get_chroma_config()`
- [ ] 2.4 `rag_service.py` 10 处配置引用改为函数调用（`chroma_conf` + `synonyms_conf`）
- [ ] 2.5 `prompt_loader.py` 4 处 `prompts_conf` 改为 `get_prompts_config()`

### 阶段 3：文档与结构（可并行）

- [ ] 3.1 README.md 重写（多平台 + 环境要求 + 常见问题 + 项目结构）
- [ ] 3.2 `ARCHITECTURE.md` → `docs/architecture.md`（git mv）
- [ ] 3.3 `DECISIONS.md` → `docs/decisions.md`
- [ ] 3.4 `DESIGN.md` → `docs/design.md`
- [ ] 3.5 `PRODUCT.md` → `docs/product.md`
- [ ] 3.6 更新 4 个文件交叉引用链接
- [ ] 3.7 添加 `pyproject.toml`（ruff 仅开 E+F，版本用 `==`）
- [ ] 3.8 删除 `pytest.ini`（pyproject.toml 已配置）
- [ ] 3.9 更新 `frontend/README.md`
- [ ] 3.10 `.gitignore` 补充 `models/`

### 阶段 4：依赖与环境清理

- [ ] 4.1 `requirements.txt` 删除 `streamlit==1.57.0`
- [ ] 4.2 `requirements.txt` 添加 `python-magic-bin` 注释
- [ ] 4.3 `doc_parser.py` 添加 `import magic` 失败时的友好错误提示

---

## 四、验证标准

1. macOS 用户按 README 能在 15 分钟内启动项目
2. Windows 用户按 README 能在 15 分钟内启动项目（不需要额外 Google 搜索）
3. `ruff check app/` 无 E/F 级别错误
4. 所有现有测试通过（`pytest app/tests/`）
5. `curl http://localhost:8000/api/health` 返回 `{"status": "ok"}`
6. 前端 `http://localhost:5173` 可正常渲染并调用 API
7. 配置惰性加载生效：首次调用后才读取 YAML 文件
8. `DEBUG_MODE=false` 时异常不暴露技术细节
9. `LOG_LEVEL=DEBUG` 时输出调试日志

---

## 五、本次不纳入的已知问题（记录备查）

| 问题 | 说明 | 归属方案 |
|------|------|---------|
| `coros_client.py` `select.select()` Windows 兼容 | Windows 上对 pipe 无效，需改用 threading + timeout | 04-LLMAgent |
| 日志轮转/清理策略 | 按天生成永不删除，需 `TimedRotatingFileHandler` | 05-数据治理 |
| docker-compose.yml | 待完整重构后生成 | 单独 PR |
