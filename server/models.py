"""
数据层 —— SQLAlchemy ORM 模型。

职责：
  - 定义 User / Session / Message 三张表的列、类型、约束、索引
  - 通过 ForeignKey 建立表间关联
  - 通过 relationship 提供 Python 层面的对象导航
  - cascade 配置实现级联删除（删用户 → 删会话 → 删消息）

三张表的关系链：User (1) → (N) Session (1) → (N) Message。
"""
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, func, CHAR
from sqlalchemy.orm import relationship
from server.database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    password_hash = Column(String(128), nullable=False)  # bcrypt 哈希，不存明文
    city = Column(String(50), default="")
    extra_info = Column(Text, default="")  # 扩展字段（JSON字符串，存用户画像）
    created_at = Column(DateTime, server_default=func.now())
    # 参数一：指向哪个模型类 参数二：和模型类属性双向绑定 参数三：删用户时自动删掉他所有会话
    sessions = relationship("Session", back_populates="user", cascade="all, delete-orphan")

class Session(Base):
    __tablename__ = "sessions"

    id = Column(CHAR(32), primary_key=True)  # uuid hex
    title = Column(String(100), default="新对话")
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="sessions")
    messages = relationship("Message", back_populates="session", cascade="all, delete-orphan")

class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(32), ForeignKey("sessions.id"), nullable=False)
    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    session = relationship("Session", back_populates="messages")