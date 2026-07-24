"""
应用组装层 —— FastAPI 入口。

职责：
  - 创建 FastAPI 应用实例
  - lifespan: 执行启动前运行检查
  - 注册异常处理器（全局兜底）
  - 配置 CORS 中间件（允许前端跨域）
  - 注册路由（后续阶段通过 include_router 挂载）

每个请求的执行顺序：CORS → 异常处理器 → 路由 → 依赖注入 → 业务逻辑
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from app.api.response import error_response, success_response
from app.schemas import ApiResponse
from app.api.exception_handlers import register_exception_handlers
from app.utils.bootstrap import validate_runtime
from app.services.vector_store import VectorStoreService
from app.api.routers.chat import router as chat_router
from app.api.routers.auth import router as auth_router
from app.api.routers.sessions import router as sessions_router
from app.api.routers.messages import router as messages_router
from app.api.routers.profile import router as profile_router
from app.api.routers.upload import router as upload_router
from app.api.routers.fitness import router as fitness_router
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.middleware import SlowAPIMiddleware
from app.core.request_context import request_id_var
import uuid
import os
import time

limiter = Limiter(key_func=get_remote_address)


@asynccontextmanager
async def lifespan(app: FastAPI):
    issues = validate_runtime()
    if issues:
        for issue in issues:
            print(f"[启动检查失败] {issue}")
        raise RuntimeError(f"启动检查未通过，共 {len(issues)} 个问题，请修复后重试")
    yield


app = FastAPI(title="FitAgent API", version="2.0.0", lifespan=lifespan)
register_exception_handlers(app)
app.include_router(chat_router)
app.include_router(auth_router)
app.include_router(sessions_router)
app.include_router(messages_router)
app.include_router(profile_router)
app.include_router(upload_router)
app.include_router(fitness_router)

ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:5173").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in ALLOWED_ORIGINS],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)


# request_id 中间件：每次请求生成唯一 ID，注入响应头 + ContextVar（供日志/业务代码使用）
@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    rid = uuid.uuid4().hex[:16]
    request_id_var.set(rid)
    request.state.request_id = rid
    start = time.time()
    response = await call_next(request)
    response.headers["X-Request-Id"] = rid
    # 记录请求耗时（结构化日志，方便排查慢请求）
    duration_ms = (time.time() - start) * 1000
    response.headers["X-Response-Time-Ms"] = f"{duration_ms:.0f}"
    return response


@app.get("/api/health", response_model=ApiResponse[dict[str, str]])
def health_check():
    return success_response({"status": "ok"})


@app.get("/api/health/rag", response_model=ApiResponse[dict[str, object]])
def rag_health_check():
    """检查 Qdrant 就绪状态，不触发文档导入或索引修复。"""
    try:
        return success_response(VectorStoreService().health())
    except Exception:
        return error_response("RAG 服务暂不可用。", status_code=503)
