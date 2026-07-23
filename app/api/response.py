"""
统一响应格式 —— 所有 API 端点返回一致的结构。

格式: {"code": HTTP状态码, "message": "人类可读消息", "data": 业务数据}

提供两个主动响应函数：success_response（操作成功）、error_response（操作失败）。
路由函数主动判断业务结果后调用它们 —— 你控制什么情况下返回什么。
"""

from typing import Any

from fastapi.responses import JSONResponse


def success_response(message: str = "操作成功", data: Any = None, code: int = 200) -> JSONResponse:
    return JSONResponse(
        status_code=code,
        content={"code": code, "message": message, "data": data},
    )


def error_response(message: str = "操作失败", data: Any = None, code: int = 400) -> JSONResponse:
    return JSONResponse(
        status_code=code,
        content={"code": code, "message": message, "data": data},
    )
