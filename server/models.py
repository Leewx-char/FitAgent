"""
数据层 —— SQLAlchemy ORM 模型。

职责：
  - 定义 User / Session / Message 三张表的列、类型、约束、索引
  - 通过 ForeignKey 建立表间关联
  - 通过 relationship 提供 Python 层面的对象导航
  - cascade 配置实现级联删除（删用户 → 删会话 → 删消息）

三张表的关系链：User (1) → (N) Session (1) → (N) Message。
"""
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, func, CHAR, Float
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
    # 参数一：指向哪个模型类 参数二：对应模型类也有一个指回我，形成双向绑定 参数三：删用户时自动删掉他所有会话
    sessions = relationship("Session", back_populates="user", cascade="all, delete-orphan")
    profile = relationship("UserProfile", back_populates="user", uselist=False, cascade="all, delete-orphan")

class UserProfile(Base):
    __tablename__ = "user_profiles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    gender = Column(String(10), default="")  # male / female / other
    age = Column(Integer, nullable=True)  # 年龄
    height = Column(Integer, nullable=True)  # 身高(cm)
    weight = Column(Float, nullable=True)  # 体重(kg)
    goal = Column(String(20), default="")  # cut/bulk/recomp/endurance/health
    weekly_days = Column(Integer, default=3)  # 每周训练天数 1-7
    experience = Column(String(20), default="beginner")  # beginner/intermediate/advanced
    injuries = Column(Text, default="[]")  # JSON: ["膝盖", "腰椎"]
    diet_restrict = Column(Text, default="[]")  # JSON: ["素食", "低碳"]
    preferences = Column(Text, default="{}")  # JSON: {"preferred_time": "早上", "gym": true}
    health_data = Column(Text, default="{}")  # JSON: 从文档提取的健康指标
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="profile")

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