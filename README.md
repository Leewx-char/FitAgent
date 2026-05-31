# FitAgent — 多Agent个性化运动教练

基于大语言模型的智能运动教练系统，结合 RAG 知识库检索和用户画像，提供个性化的健身指导、营养建议和损伤预防方案。

## 项目架构

```
智扫通/
├── server/                  # 后端（FastAPI）
│   ├── main.py              # FastAPI 入口 + CORS + lifespan
│   ├── database.py          # MySQL 连接池
│   ├── models.py            # ORM 模型（User/Session/Message/UserProfile）
│   ├── schemas.py           # Pydantic 请求/响应模型
│   ├── auth.py              # JWT 认证 + bcrypt 密码哈希
│   ├── deps.py              # 依赖注入（get_db, get_agent）
│   ├── routers/
│   │   ├── auth.py          # 注册/登录/获取当前用户
│   │   ├── sessions.py      # 会话 CRUD
│   │   ├── messages.py      # 消息查询
│   │   ├── chat.py          # SSE 流式聊天
│   │   └── profile.py       # 用户画像 GET/POST/PUT
│   └── helpers/
│       └── exception_handlers.py
├── agent/                   # Agent 层
│   ├── react_agent.py       # ReAct Agent（LangGraph）
│   └── tools/
│       ├── agent_tools.py   # 7个工具（RAG检索/用户画像/天气/报告触发等）
│       └── middleware.py    # 工具监控 + 动态提示词切换
├── rag/                     # RAG 检索层
│   ├── rag_service.py       # 同义词扩展 + 归一化 + 重排序
│   └── vector_store.py      # ChromaDB 向量存储 + 增量更新
├── prompts/                 # 提示词
│   ├── main_prompt.txt      # 系统提示词（FitAgent 运动教练角色）
│   ├── rag_summarize.txt    # RAG 摘要提示词
│   └── report_prompt.txt    # 运动总结报告提示词
├── data/                    # 知识库（415个问答）
│   ├── 健身基础知识.txt       # 35 问
│   ├── 运动损伤预防.txt       # 80 问
│   ├── 营养学知识.txt         # 100 问
│   ├── 训练计划指南.txt       # 100 问
│   └── 动作指南大全.txt       # 100 问
├── config/                  # 配置文件
│   ├── agent.yml
│   ├── chroma.yml
│   ├── prompts.yml
│   └── rag.yml
├── model/                   # LLM 工厂
├── utils/                   # 工具函数
├── frontend/                 # 前端（Vue3）
│   ├── src/
│   │   ├── api/             # Axios 封装（auth/chat/profile）
│   │   ├── stores/          # Pinia 状态管理（auth/profile/chat）
│   │   ├── router/          # Vue Router 路由配置
│   │   └── views/           # 页面组件
│   │       ├── Login.vue    # 登录
│   │       ├── Register.vue # 注册
│   │       ├── Onboarding.vue # 分步问卷（5步）
│   │       ├── Chat.vue     # 聊天（SSE 流式 + 会话侧边栏）
│   │       └── Profile.vue  # 画像查看/编辑
│   └── vite.config.js       # Vite 配置 + API 代理
├── DECISIONS.md             # 项目决策记录
└── requirements.txt
```

## 技术栈

### 后端

| 技术 | 用途 |
|------|------|
| FastAPI | Web 框架 |
| SQLAlchemy + PyMySQL | ORM + MySQL 驱动 |
| python-jose + passlib + bcrypt | JWT 认证 + 密码哈希 |
| LangGraph | ReAct Agent 框架 |
| DashScope (通义千问) | 大语言模型 |
| LangChain + ChromaDB | RAG 向量检索 |
| SSE (sse-starlette) | 流式响应 |

### 前端

| 技术 | 用途 |
|------|------|
| Vue 3 | 前端框架 |
| Vite | 构建工具 + 开发服务器 |
| Naive UI | UI 组件库 |
| Pinia | 状态管理 |
| Vue Router | 路由 |
| Axios | HTTP 请求 |

## 运行环境

- Python 3.11+
- Node.js 18+
- MySQL 8.0+
- 操作系统：macOS / Linux

## 快速启动

### 1. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入以下配置：
# DASHSCOPE_API_KEY=你的通义千问API Key
# MYSQL_USER=root
# MYSQL_PASSWORD=你的MySQL密码
# MYSQL_HOST=localhost
# MYSQL_PORT=3306
# MYSQL_DATABASE=zhitong
# JWT_SECRET_KEY=随机密钥
```

### 2. 启动后端

```bash
# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate  # macOS/Linux

# 安装依赖
pip install -r requirements.txt

# 启动服务（首次启动自动建表和加载知识库）
python -m uvicorn server.main:app --host 0.0.0.0 --port 8000
```

### 3. 启动前端

```bash
cd frontend

# 安装依赖
npm install

# 开发模式启动（自带 API 代理到 localhost:8000）
npm run dev
```

浏览器打开 http://localhost:5173 即可使用。

### 4. 构建前端生产版本

```bash
cd frontend
npm run build
```

构建产物在 `frontend/dist/`，可配置 Nginx 托管。

## API 接口

| 方法 | 路径 | 说明 | 认证 |
|------|------|------|------|
| POST | `/api/auth/register` | 注册 | 否 |
| POST | `/api/auth/login` | 登录（返回 JWT） | 否 |
| GET | `/api/auth/me` | 获取当前用户 | 是 |
| POST | `/api/profile` | 创建用户画像 | 是 |
| GET | `/api/profile` | 查询用户画像 | 是 |
| PUT | `/api/profile` | 更新用户画像 | 是 |
| POST | `/api/sessions` | 创建会话 | 是 |
| GET | `/api/sessions` | 会话列表 | 是 |
| DELETE | `/api/sessions/:id` | 删除会话 | 是 |
| GET | `/api/sessions/:id/messages` | 会话消息 | 是 |
| POST | `/api/chat` | SSE 流式聊天 | 是 |

## 核心特性

- **用户画像**：注册后填问卷，Agent 自动读取画像提供个性化建议
- **RAG 知识库**：5 个运动科学领域知识库（415 问），支持同义词扩展和查询归一化
- **SSE 流式回复**：实时逐字输出，前端 EventSource 或 ReadableStream 接收
- **工具调用**：Agent 可调用用户画像查询、RAG 检索、天气查询等工具
- **分步问卷**：5 步收集性别/年龄/身高体重/目标/经验/伤病/偏好

## 项目结构说明

更多设计决策和技术细节，请查看 [DECISIONS.md](./DECISIONS.md)。

## License

MIT