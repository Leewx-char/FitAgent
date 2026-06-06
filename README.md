# FitAgent — 多Agent个性化运动教练

基于大语言模型的智能运动教练系统，结合 RAG 知识库检索、用户画像和多模态健康文档上传，提供个性化健身指导。

## 技术栈

**后端**：FastAPI + SQLAlchemy + MySQL + LangGraph + DashScope (通义千问) + ChromaDB + SSE

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
uvicorn server.main:app --reload --port 8000

# 3. 启动前端
cd frontend && npm install && npm run dev
```

浏览器打开 http://localhost:5173

## 项目结构

```
智扫通/
├── server/                  # FastAPI 后端
│   ├── main.py              # 入口 + CORS + lifespan
│   ├── models.py            # ORM 模型（User/Session/Message/UserProfile）
├── agent/                   # ReAct Agent + 7工具
├── rag/                     # RAG 检索（同义词+归一化+重排序）
├── model/                   # LLM/VL/Embedding 工厂
├── prompts/                 # 系统提示词 + 健康数据提取提示词
├── config/                  # YAML 配置（agent/chroma/rag/synonyms/prompts）
├── data/                    # 知识库（5个txt，415问答）
├── frontend/                # Vue3 前端
│   └── src/views/           # Login/Register/Onboarding/Chat/Profile
├── storage/uploads/         # 上传文件临时目录
└── DECISIONS.md             # 项目决策记录
```

更多设计决策和技术细节请查看 [DECISIONS.md](./DECISIONS.md)。

## License

MIT