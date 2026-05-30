"""
应用组装层 —— FastAPI 入口。

职责：
  - 创建 FastAPI 应用实例
  - lifespan: 启动时自动建表
  - 注册异常处理器（全局兜底）
  - 配置 CORS 中间件（允许前端跨域）
  - 注册路由（后续阶段通过 include_router 挂载）

每个请求的执行顺序：CORS → 异常处理器 → 路由 → 依赖注入 → 业务逻辑
"""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from server.helpers.exception_handlers import register_exception_handlers
from server.database import engine, Base
from server.routers.chat import router as chat_router
from server.routers.auth import router as auth_router
from server.routers.sessions import router as sessions_router
from server.routers.messages import router as messages_router
from server.routers.profile import router as profile_router
import os

@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield

app = FastAPI(title="智扫通 API", version="2.0.0", lifespan=lifespan)
register_exception_handlers(app)
app.include_router(chat_router)
app.include_router(auth_router)
app.include_router(sessions_router)
app.include_router(messages_router)
app.include_router(profile_router)

ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:5173,http://localhost:8501").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in ALLOWED_ORIGINS],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/health")
def health_check():
    return {"status": "ok"}