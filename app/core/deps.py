"""
依赖注入层 —— 提供可复用的依赖函数。

职责：
  - get_db: 每个 API 请求获取一个独立的数据库事务会话
           请求成功 → 自动 commit，异常 → 自动 rollback，结束 → 自动 close
  - get_agent: 全局单例 ReactAgent 实例
             整个应用共用一个，避免每次请求重新加载模型和向量库

路由函数通过 FastAPI 的 Depends() 注入这些依赖。
"""

from app.core.database import get_db_session
from fastapi import HTTPException
from app.services.react_agent import ReactAgent
from functools import lru_cache
from threading import Lock
from app.services.coros_client import CorosClient
from app.core.settings import get_settings


def get_db():
    """为每个 HTTP 请求提供独立的数据库事务会话。"""
    with get_db_session() as db:
        yield db


def get_agent():
    """每次请求创建新的 ReactAgent 实例（实例级隔离），底层 ChatTongyi 模型
    通过 get_chat_model() 的 @lru_cache 复用连接（连接级复用）。
    避免全局单例在并发请求时 LangChain agent 内部状态交叉污染。"""
    return ReactAgent()


_coros_creation_lock = Lock()


@lru_cache(maxsize=1)
def _coros_singleton() -> CorosClient:
    """在线程竞争的冷启动场景下创建唯一的 Coros MCP 客户端。"""
    # lru_cache 的首次调用可能并发执行，显式锁确保只启动一个 MCP 子进程。
    with _coros_creation_lock:
        settings = get_settings()
        return CorosClient(
            command=settings.coros_mcp_command_parts,
            sync_command=settings.coros_mcp_sync_command_parts,
            working_directory=str(settings.project_root),
            environment={
                "COROS_MCP_TOOLSET": settings.coros_mcp_toolset,
                "COROS_MCP_HIDE_AUTH_TOOLS": "1" if settings.coros_mcp_hide_auth_tools else "0",
                # 运行器只重定向社区包的 SQLite 缓存，认证令牌仍使用真实 Windows 用户配置。
                "FITAGENT_COROS_MCP_CACHE_DIR": str(settings.coros_mcp_cache_home_path),
                # 显式同步负责刷新；只读工具只使用已准备缓存，避免请求中再次拉取上游数据。
                "COROS_STABLE_DAYS": "-1",
            },
        )


def get_coros():
    """CorosClient 单例。用 lru_cache 而非模块级变量，保证线程安全
    （CorosClient 创建子进程耗时 1-2s，并发窗口大，模块级变量方式
    可能创建多个实例导致子进程泄漏）。"""

    try:
        return _coros_singleton()
    except FileNotFoundError as error:
        command = " ".join(get_settings().coros_mcp_command_parts)
        raise HTTPException(
            status_code=503,
            detail=(
                f"Coros MCP 未安装或命令不可执行：{command}。"
                "请先运行 .\\scripts\\install_coros_mcp.ps1 并完成 coros-mcp auth。"
            ),
        ) from error
    except (OSError, RuntimeError) as error:
        raise HTTPException(
            status_code=502,
            detail="Coros MCP 无法启动或初始化；请检查 coros-mcp auth-status 后重试。",
        ) from error


def close_coros() -> None:
    """在应用关闭时终止进程级 Coros 客户端并清空缓存。"""

    if _coros_singleton.cache_info().currsize:
        _coros_singleton().close()
    _coros_singleton.cache_clear()
