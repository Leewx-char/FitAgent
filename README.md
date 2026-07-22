# FitAgent — 多Agent个性化运动教练

基于大语言模型的智能运动教练系统，结合 RAG 知识库检索、用户画像和多模态健康文档上传，提供个性化健身指导。

## 环境要求

| 依赖 | 版本 | 说明 |
|------|------|------|
| Python | 3.11+ | |
| Node.js | 20+ | |
| MySQL | 8.0+ | |
| [Windows] Visual C++ | 14.0+ | 编译 python-magic-bin 所需 |
| [Windows] poppler | 最新版 | pdf2image 依赖,[下载地址](https://github.com/oschwartz10612/poppler-windows/releases),将 `bin/` 加入系统 PATH |

## 技术栈

| 层次 | 技术 |
|------|------|
| 后端框架 | FastAPI 0.136 + Uvicorn 0.47 |
| 数据库 | MySQL 8.0 + SQLAlchemy 2.0 |
| 认证 | JWT (python-jose) + bcrypt |
| LLM | DashScope (deepseek-v4-pro / text-embedding-v4) |
| Agent | LangGraph + LangChain |
| 向量数据库 | ChromaDB |
| 关键词检索 | rank-bm25 (BM25) |
| 文档处理 | PyPDF + pdf2image + python-magic + Pillow |
| 前端框架 | Vue 3 + Vite + Pinia + Naive UI |
| 图表 | ECharts |

## Windows 启动指南

```powershell
# 0. 允许 PowerShell 脚本执行（仅首次需要）
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

# 1. 克隆并配置环境变量
cp .env.example .env
# 编辑 .env,填入 DASHSCOPE_API_KEY、MySQL 配置、JWT_SECRET_KEY

# 2. 创建数据库
# 如果 mysql 不在 PATH 中,使用完整路径,例如:
# & "C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe" -u root -p -e "CREATE DATABASE IF NOT EXISTS zhitong CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
mysql -u root -p -e "CREATE DATABASE IF NOT EXISTS zhitong CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"

# 3. Python 环境 & 依赖
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install python-magic-bin   # Windows 必需,提供 libmagic DLL

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

## 项目结构

```
FitAgent/
├── app/                        # 后端代码（FastAPI 标准结构）
│   ├── main.py                 # 应用入口 + CORS + lifespan
│   ├── models.py               # ORM 模型
│   ├── schemas.py              # Pydantic 请求/响应模型
│   ├── core/                   # 基础设施
│   │   ├── database.py         # MySQL 连接
│   │   ├── auth.py             # JWT 认证
│   │   └── deps.py             # 依赖注入
│   ├── api/                    # HTTP 层
│   │   ├── routers/            # auth/chat/sessions/messages/profile/upload/fitness
│   │   ├── exception_handlers.py
│   │   └── response.py
│   ├── services/               # 业务逻辑层
│   │   ├── factory.py          # LLM/VL/Embedding 模型工厂
│   │   ├── react_agent.py      # LangGraph ReAct Agent
│   │   ├── agent_tools.py      # 工具定义
│   │   ├── middleware.py       # Agent 中间件
│   │   ├── rag_service.py      # RAG 检索服务
│   │   ├── vector_store.py     # ChromaDB 向量库
│   │   ├── bm25_retriever.py   # BM25 关键词检索
│   │   └── doc_parser.py       # 多模态文档解析
│   └── utils/                  # 工具函数
│       ├── config_handler.py
│       ├── logger_handler.py
│       ├── file_handler.py
│       ├── prompt_loader.py
│       └── bootstrap.py
├── config/                     # YAML 配置
├── prompts/                    # 系统提示词
├── data/                       # 知识库（txt）
├── frontend/                   # Vue 3 前端
├── docs/                       # 架构文档
│   ├── architecture.md
│   ├── decisions.md
│   ├── design.md
│   └── product.md
├── storage/uploads/            # 上传文件临时目录
└── chroma_db/                  # ChromaDB 持久化存储
```

## RAG 检索流程

```
用户提问
  ├── 查询处理：归一化（口语→术语）+ 同义词扩展
  ├── 双路并行检索：
  │   ├── ChromaDB 向量检索（语义,余弦相似度 + 阈值过滤）
  │   └── BM25 关键词检索（字级分词 + TF-IDF + 长度归一化）
  ├── RRF 排名融合
  ├── Jaccard 去重（阈值 0.8）
  └── 取 Top-6 返回
```

更多设计决策和技术细节请查看 [docs/decisions.md](./docs/decisions.md)。

## 常见问题

### Q: Windows 上 `.\.venv\Scripts\Activate.ps1` 报错 "running scripts is disabled"

PowerShell 默认禁止执行脚本。以管理员身份运行 PowerShell,执行：
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```
或者使用 cmd 替代：在 cmd 中运行 `.venv\Scripts\activate.bat`

### Q: Windows 上启动报 `ImportError: No module named 'magic'`

`python-magic` 在 Windows 上需要额外安装 DLL：
```
pip install python-magic-bin
```

### Q: 上传 PDF 时报 "Is poppler installed and in PATH?"

Windows 需要手动安装 poppler。从 [poppler-windows](https://github.com/oschwartz10612/poppler-windows/releases) 下载最新版,解压后将 `bin/` 目录加入系统 PATH 环境变量,重启终端即可。

### Q: 启动报错 "Can't connect to MySQL server"

1. 确认 MySQL 服务已启动（Windows：服务管理器查找 MySQL80）
2. 确认已创建 zhitong 数据库：
```sql
CREATE DATABASE IF NOT EXISTS zhitong CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```
3. 检查 `.env` 中的 `MYSQL_HOST` 和 `MYSQL_PORT` 是否正确

### Q: `mysql` 命令找不到 (Windows)

MySQL 安装路径默认不在 PATH 中。使用完整路径运行：
```powershell
& "C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe" -u root -p
```
或者将 MySQL bin 目录添加到系统 PATH。

### Q: ChromaDB 向量库损坏

删除 `chroma_db/` 目录后重启,应用会自动重建向量库。

### Q: `npm install` 失败或前端启动报错

1. 确认 Node.js 版本 ≥ 20：`node --version`
2. 删除 `node_modules/` 和 `package-lock.json`,重新 `npm install`
3. Windows 防火墙弹出时点"允许"

## License

MIT
