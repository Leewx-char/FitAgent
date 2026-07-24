"""普通 HTTP JSON 接口统一响应格式。"""

from typing import TypeVar

from fastapi.responses import JSONResponse

from app.schemas import ApiResponse

ResponseData = TypeVar("ResponseData")


def success_response(
    data: ResponseData | None = None,
    messages: list[str] | None = None,
    *,
    status_code: int = 200,
) -> ApiResponse[ResponseData]:
    """构造成功响应；code 与实际 HTTP 状态码保持一致。"""

    return ApiResponse(code=status_code, messages=messages or [], data=data)


def error_response(
    messages: object,
    *,
    status_code: int,
) -> JSONResponse:
    """构造失败响应；响应体 code 与 HTTP 状态码保持一致。"""

    if isinstance(messages, str):
        normalized_messages = [messages]
    elif isinstance(messages, list):
        normalized_messages = [str(message) for message in messages]
    else:
        normalized_messages = [str(messages)]
    return JSONResponse(
        status_code=status_code,
        content={
            "code": status_code,
            "messages": normalized_messages,
            "data": None,
        },
    )
