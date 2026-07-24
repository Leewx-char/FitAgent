"""
数据库连接层。

职责：
  - 通过 Settings 读取 MySQL 连接参数，拼接连接字符串
  - 创建 SQLAlchemy 引擎（含连接池配置）
  - 提供 SessionLocal 会话工厂，每次 API 请求通过 Depends 获取独立事务会话
  - 提供 Base 声明式基类，所有 ORM 模型继承它
  - 提供仅供本地开发显式调用的 ensure_database_exists() 建库函数

本文件是数据层的唯一入口。换数据库只需改本文件的连接字符串。
"""

import re
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from app.core.settings import Settings, get_settings

DATABASE_URL = get_settings().database_url


def ensure_database_exists(settings: Settings | None = None) -> None:
    """仅在明确开启时为本地开发创建配置中的空数据库。"""
    settings = settings or get_settings()
    if not settings.auto_create_database:
        return

    if not re.fullmatch(r"[A-Za-z0-9_]+", settings.mysql_database):
        raise RuntimeError("MYSQL_DATABASE 只能包含字母、数字和下划线")

    import pymysql

    try:
        conn = pymysql.connect(
            host=settings.mysql_host,
            port=settings.mysql_port,
            user=settings.mysql_user,
            password=settings.mysql_password,
            charset="utf8mb4",
        )
    except pymysql.Error as e:
        raise RuntimeError(
            "无法连接到 MySQL "
            f"({settings.mysql_host}:{settings.mysql_port})，请确认 MySQL 服务已启动。\n"
            f"错误详情: {e}"
        ) from e

    try:
        with conn.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{settings.mysql_database}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
    finally:
        conn.close()


engine = create_engine(
    DATABASE_URL,
    pool_size=5,  # 连接池大小，默认5
    max_overflow=10,  # 额外可创建的连接数
    pool_recycle=3600,  # 连接一小时回收一次，防止 MySQL 断开
    echo=False,  # 不打印 SQL 日志
)
# 会话工厂，之后在 API 里每次请求调用 SessionLocal() 拿到一个临时会话
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@contextmanager
def get_db_session() -> Iterator:
    """提供统一的数据库事务边界。

    HTTP 路由和 Agent 工具都通过该上下文管理器取得会话：正常结束时提交，
    出现异常时回滚，无论结果如何都会关闭会话并归还连接池。
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


class Base(DeclarativeBase):
    pass
