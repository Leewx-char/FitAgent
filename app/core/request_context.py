"""请求级上下文 —— 存放跨模块共享的 ContextVar。

单独成模块是为了避免循环导入：main.py 注册中间件时写入 request_id，
audit / 日志等下游模块只需读取，不必反向依赖 main.py。
"""
from contextvars import ContextVar

# 每个请求唯一的追踪 ID，由 main.py 的中间件在请求入口设置
request_id_var: ContextVar[str] = ContextVar("request_id", default="")
