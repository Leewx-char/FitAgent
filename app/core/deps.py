"""
依赖注入层 —— 提供可复用的依赖函数。

职责：
  - get_db: 每个 API 请求获取一个独立的数据库事务会话
           请求成功 → 自动 commit，异常 → 自动 rollback，结束 → 自动 close
  - get_agent: 全局单例 ReactAgent 实例
             整个应用共用一个，避免每次请求重新加载模型和向量库

路由函数通过 FastAPI 的 Depends() 注入这些依赖。
"""
from app.core.database import SessionLocal
from app.services.react_agent import ReactAgent
from functools import lru_cache
from app.services.coros_client import CorosClient

def get_db():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


_agent_instance = None


def get_agent():
    global _agent_instance
    if _agent_instance is None:
        _agent_instance = ReactAgent()
    return _agent_instance


@lru_cache(maxsize=1)
def _coros_singleton():
    return CorosClient()


def get_coros():
    """CorosClient 单例。用 lru_cache 而非模块级变量，保证线程安全
    （CorosClient 创建子进程耗时 1-2s，并发窗口大，模块级变量方式
    可能创建多个实例导致子进程泄漏）。"""
    return _coros_singleton()

