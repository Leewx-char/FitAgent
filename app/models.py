"""
数据层 —— SQLAlchemy ORM 模型。

职责：
  - 定义 User / Session / Message 三张表的列、类型、约束、索引
  - 通过 ForeignKey 建立表间关联
  - 通过 relationship 提供 Python 层面的对象导航
  - cascade 配置实现级联删除（删用户 → 删会话 → 删消息）

三张表的关系链：User (1) → (N) Session (1) → (N) Message。
"""

from sqlalchemy import (
    CHAR,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import relationship
from app.core.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    password_hash = Column(String(128), nullable=False)  # bcrypt 哈希，不存明文
    city = Column(String(50), default="")
    extra_info = Column(Text, default="")  # 扩展字段（JSON字符串，存用户画像）
    created_at = Column(DateTime, server_default=func.now())
    # 与 Session 建立双向关系；删除用户时级联删除其会话。
    sessions = relationship("Session", back_populates="user", cascade="all, delete-orphan")
    profile = relationship(
        "UserProfile", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    agent_runs = relationship(
        "AgentRun", back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )


class UserProfile(Base):
    __tablename__ = "user_profiles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    gender = Column(String(10), default="")  # 性别值，如 male、female 或 other
    age = Column(Integer, nullable=True)  # 年龄
    height = Column(Integer, nullable=True)  # 身高(cm)
    weight = Column(Float, nullable=True)  # 体重(kg)
    goal = Column(String(20), default="")  # 目标值，如减脂、增肌、塑形、耐力或健康管理
    weekly_days = Column(Integer, default=3)  # 每周训练天数 1-7
    # 经验等级实际取值：beginner、intermediate 或 advanced。
    experience = Column(String(20), default="beginner")
    injuries = Column(Text, default="[]")  # JSON 数组，例如 ["膝盖", "腰椎"]
    diet_restrict = Column(Text, default="[]")  # JSON 数组，例如 ["素食", "低碳"]
    preferences = Column(Text, default="{}")  # JSON 对象，例如训练时间和场馆偏好
    health_data = Column(Text, default="{}")  # JSON: 从文档提取的健康指标
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="profile")


class Session(Base):
    __tablename__ = "sessions"

    id = Column(CHAR(32), primary_key=True)  # UUID 的十六进制字符串
    title = Column(String(100), default="新对话")
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="sessions")
    messages = relationship("Message", back_populates="session", cascade="all, delete-orphan")
    session_summary = relationship(
        "SessionSummary", back_populates="session", uselist=False, cascade="all, delete-orphan"
    )
    agent_runs = relationship(
        "AgentRun", back_populates="session", cascade="all, delete-orphan", passive_deletes=True
    )


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(32), ForeignKey("sessions.id"), nullable=False)
    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    session = relationship("Session", back_populates="messages")


class AgentRun(Base):
    """一轮 Agent 执行的无敏感轨迹摘要。"""

    __tablename__ = "agent_runs"

    id = Column(CHAR(32), primary_key=True)
    request_id = Column(String(32), nullable=False, index=True)
    session_id = Column(CHAR(32), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    mode = Column(String(20), nullable=False)  # 执行模式：agent 或 direct_rag
    status = Column(String(20), nullable=False)  # 执行状态：succeeded 或 failed
    elapsed_ms = Column(Integer, nullable=False)
    tool_call_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, server_default=func.now())

    user = relationship("User", back_populates="agent_runs")
    session = relationship("Session", back_populates="agent_runs")
    tool_calls = relationship(
        "AgentToolCall",
        back_populates="agent_run",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="AgentToolCall.sequence",
    )


class AgentToolCall(Base):
    """Agent 运行中单次工具调用的安全审计信息。"""

    __tablename__ = "agent_tool_calls"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_run_id = Column(CHAR(32), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False)
    sequence = Column(Integer, nullable=False)
    tool_name = Column(String(80), nullable=False)
    argument_shape = Column(Text, nullable=False, default="{}")
    status = Column(String(20), nullable=False)
    elapsed_ms = Column(Integer, nullable=False)
    detail = Column(String(120), nullable=False, default="")
    created_at = Column(DateTime, server_default=func.now())

    agent_run = relationship("AgentRun", back_populates="tool_calls")

    __table_args__ = (Index("ix_agent_tool_calls_run_sequence", "agent_run_id", "sequence"),)


class FitnessData(Base):
    __tablename__ = "fitness_data"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    date = Column(Date, nullable=False)
    data_type = Column(String(20), nullable=False)
    # 来自 Coros 的稳定记录键。日指标/睡眠按日期幂等，活动按 activity id 幂等，
    # 因而同一天的多次活动不会再互相覆盖。
    external_id = Column(String(128), nullable=False)
    data = Column(Text, default="{}")
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_fitness_user_type_external", "user_id", "data_type", "external_id", unique=True),
    )


class SessionSummary(Base):
    """可重建的会话状态摘要，不替换或删除原始聊天记录。"""

    __tablename__ = "session_summaries"

    id = Column(CHAR(32), primary_key=True)
    session_id = Column(
        CHAR(32), ForeignKey("sessions.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    content = Column(Text, nullable=False, default="{}")
    covered_through_message_id = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    session = relationship("Session", back_populates="session_summary")


class MemoryFact(Base):
    """用户可确认、撤销和过期的长期记忆条目。"""

    __tablename__ = "memory_facts"

    id = Column(CHAR(32), primary_key=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_message_id = Column(
        Integer, ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    supersedes_id = Column(CHAR(32), nullable=True)
    fact_key = Column(String(80), nullable=False)
    category = Column(String(30), nullable=False)
    value = Column(Text, nullable=False, default="{}")
    display_text = Column(String(300), nullable=False)
    status = Column(String(20), nullable=False, default="proposed")
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (Index("ix_memory_facts_user_status_key", "user_id", "status", "fact_key"),)


class TrainingPlan(Base):
    """用户显式生成的结构化周训练计划草稿或生效版本。"""

    __tablename__ = "training_plans"

    id = Column(CHAR(32), primary_key=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    week_start = Column(Date, nullable=False)
    version = Column(Integer, nullable=False, default=1)
    status = Column(String(20), nullable=False, default="draft")
    plan_data = Column(Text, nullable=False, default="{}")
    safety_data = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    feedbacks = relationship(
        "TrainingFeedback", back_populates="plan", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_training_plans_user_week", "user_id", "week_start"),)


class TrainingFeedback(Base):
    """计划执行后的用户反馈，供下一版训练计划安全调整使用。"""

    __tablename__ = "training_feedbacks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    plan_id = Column(CHAR(32), ForeignKey("training_plans.id", ondelete="CASCADE"), nullable=False)
    day_of_week = Column(Integer, nullable=False)
    completed = Column(Boolean, nullable=False, default=False)
    rpe = Column(Integer, nullable=True)
    pain_score = Column(Integer, nullable=True)
    notes = Column(String(500), nullable=False, default="")
    created_at = Column(DateTime, server_default=func.now())

    plan = relationship("TrainingPlan", back_populates="feedbacks")

    __table_args__ = (UniqueConstraint("plan_id", "day_of_week", name="uq_plan_feedback_day"),)
