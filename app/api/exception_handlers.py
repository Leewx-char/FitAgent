"""
全局异常兜底 —— 捕获路由函数中未处理的异常并返回统一格式。

职责：
  - HTTPException: 标准 HTTP 异常，转换为统一响应信封
  - RequestValidationError: 请求字段校验失败，返回可读的字段错误
  - IntegrityError: 数据库约束冲突（重复键、外键不存在），翻译为中文提示
  - SQLAlchemyError: 数据库通用错误，生产环境不暴露 SQL 细节
  - Exception: 所有未捕获异常的兜底，防止白屏或 panic

异常处理器和 response.py 的关系：
  response.py → 你主动调（正常流程中的错误提示）
  本文件     → 被动拦截（你没想到的异常自动兜底）

DEBUG_MODE = True 时返回技术细节，生产环境设为 False。
"""

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from slowapi.errors import RateLimitExceeded
from starlette.exceptions import HTTPException

import os

from app.api.response import error_response
from app.utils.logger_handler import logger

DEBUG_MODE = os.getenv("DEBUG_MODE", "false").lower() == "true"


async def http_exception_handler(request: Request, exc: HTTPException):
    """将 HTTP 异常转换为统一错误响应。"""
    return error_response(exc.detail, status_code=exc.status_code)


async def request_validation_exception_handler(request: Request, exc: RequestValidationError):
    """汇总请求字段校验错误并返回 422 响应。"""
    messages = [
        f"{'.'.join(str(part) for part in error['loc'])} 参数不合法" for error in exc.errors()
    ]
    return error_response(messages or ["请求参数不合法"], status_code=422)


async def integrity_error_handler(request: Request, exc: IntegrityError):
    """记录数据库约束冲突，并返回安全的客户端提示。"""
    logger.warning(f"数据库约束冲突: {str(exc)}")
    return error_response("数据已存在或关联数据不完整，请检查输入。", status_code=400)


async def sqlalchemy_error_handler(request: Request, exc: SQLAlchemyError):
    """记录 SQLAlchemy 错误，并按调试开关隐藏或展示详情。"""
    logger.error(f"数据库错误: {str(exc)}", exc_info=True)
    msg = "数据库服务异常，请稍后重试。" if not DEBUG_MODE else f"数据库错误: {str(exc)}"
    return error_response(msg, status_code=500)


async def global_exception_handler(request: Request, exc: Exception):
    """记录未处理异常，并返回统一的服务器错误响应。"""
    logger.error(f"未处理异常: {str(exc)}", exc_info=True)
    message = "服务器内部错误，请稍后重试。" if not DEBUG_MODE else f"内部错误: {str(exc)}"
    return error_response(message, status_code=500)


async def rate_limit_exception_handler(request: Request, exc: RateLimitExceeded):
    """将 SlowAPI 的限流错误转换为普通 JSON 契约。"""

    return error_response("请求过于频繁，请稍后重试。", status_code=429)


def register_exception_handlers(app):
    """为应用注册 HTTP、校验、数据库、限流及全局异常处理器。"""
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, request_validation_exception_handler)
    app.add_exception_handler(IntegrityError, integrity_error_handler)
    app.add_exception_handler(SQLAlchemyError, sqlalchemy_error_handler)
    app.add_exception_handler(Exception, global_exception_handler)
    app.add_exception_handler(RateLimitExceeded, rate_limit_exception_handler)
