"""
数据库连接层。

职责：
  - 从环境变量读取 MySQL 连接参数，拼接连接字符串
  - 创建 SQLAlchemy 引擎（含连接池配置）
  - 提供 SessionLocal 会话工厂，每次 API 请求通过 Depends 获取独立事务会话
  - 提供 Base 声明式基类，所有 ORM 模型继承它
  - 提供 ensure_database_exists() 自动建库

本文件是数据层的唯一入口。换数据库只需改本文件的连接字符串。
"""
from dotenv import load_dotenv
load_dotenv()

import os
from urllib.parse import quote_plus
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost")
MYSQL_PORT = os.getenv("MYSQL_PORT", "3306")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "zhitong")

DATABASE_URL = f"mysql+pymysql://{MYSQL_USER}:{quote_plus(MYSQL_PASSWORD)}@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DATABASE}?charset=utf8mb4"

def ensure_database_exists():
    import pymysql
    try:
        conn = pymysql.connect(
            host=MYSQL_HOST,
            port=int(MYSQL_PORT),
            user=MYSQL_USER,
            password=MYSQL_PASSWORD,
            charset='utf8mb4',
        )
    except pymysql.Error as e:
        raise RuntimeError(
            f"无法连接到 MySQL ({MYSQL_HOST}:{MYSQL_PORT})，请确认 MySQL 服务已启动。\n"
            f"错误详情: {e}"
        ) from e

    try:
        with conn.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{MYSQL_DATABASE}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
    finally:
        conn.close()

engine = create_engine(
    DATABASE_URL,
    pool_size=5, # 连接池大小，默认5
    max_overflow=10, # 额外可创建的连接数
    pool_recycle=3600, # 连接一小时回收一次，防止 MySQL 断开
    echo=False, # 不打印 SQL 日志
)
# 会话工厂，之后在 API 里每次请求调用 SessionLocal() 拿到一个临时会话
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

class Base(DeclarativeBase):
    pass