# FitAgent — 多Agent个性化运动教练

基于大语言模型的智能运动教练系统，结合 RAG 知识库检索、用户画像和多模态健康文档上传，提供个性化健身指导。

## 技术栈

**后端**：FastAPI + SQLAlchemy + MySQL + LangGraph + DashScope (通义千问) + ChromaDB + rank-bm25 + SSE

**前端**：Vue 3 + Vite + Pinia + Naive UI + Axios

**文档处理**：千问VL（图片识别）+ PyPDF + pdf2image + python-magic

## 快速启动

```bash
# 1. 配置环境变量
cp .env.example .env
# 编辑 .env，填入 DASHSCOPE_API_KEY、MySQL 配置、JWT_SECRET_KEY

# 2. 启动后端
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# 3. 启动前端
cd frontend && npm install && npm run dev
```

浏览器打开 http://localhost:5173

## 项目结构

```
智扫通/
├── app/                        # 后端代码（FastAPI 标准结构）
│   ├── main.py                 # 应用入口 + CORS + lifespan
│   ├── models.py               # ORM 模型（User/Session/Message/UserProfile）
│   ├── schemas.py              # Pydantic 请求/响应模型
│   ├── core/                   # 基础设施
│   │   ├── database.py         # MySQL 连接 + SessionLocal
│   │   ├── auth.py             # JWT 认证 + bcrypt 密码哈希
│   │   └── deps.py             # 依赖注入（get_db, get_agent）
│   ├── api/                    # HTTP 层
│   │   ├── routers/            # 路由（auth/chat/sessions/messages/profile/upload）
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
├── config/                     # YAML 配置（agent/chroma/rag/synonyms/prompts）
├── prompts/                    # 系统提示词 + 健康数据提取提示词
├── data/                       # 知识库（5个txt）
├── frontend/                   # Vue 3 前端
│   └── src/views/              # Login/Register/Onboarding/Chat/Profile
├── storage/uploads/            # 上传文件临时目录
├── chroma_db/                  # ChromaDB 持久化存储
└── DECISIONS.md                # 项目决策记录
```

## RAG 检索流程

```
用户提问
  ├── 查询处理：归一化（口语→术语）+ 同义词扩展
  ├── 双路并行检索：
  │   ├── ChromaDB 向量检索（语义，余弦相似度 + 阈值过滤）
  │   └── BM25 关键词检索（字级分词 + TF-IDF + 长度归一化）
  ├── RRF 排名融合
  ├── Jaccard 去重（阈值 0.8）
  └── 取 Top-6 返回
```

更多设计决策和技术细节请查看 [DECISIONS.md](./DECISIONS.md)。

## License

MIT