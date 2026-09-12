"""
数据层 —— SQLAlchemy ORM 模型。

职责：
  - 定义 User / Session / Message 三张表的列、类型、约束、索引
  - 通过 ForeignKey 建立表间关联
  - 通过 relationship 提供 Python 层面的对象导航
  - cascade 配置实现级联删除（删用户 → 删会话 → 删消息）

三张表的关系链：User (1) → (N) Session (1) → (N) Message。
"""

from datetime import date as DateValue
from datetime import datetime

from sqlalchemy import (
    CHAR,
    Boolean,
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
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)  # bcrypt 哈希，不存明文
    city: Mapped[str] = mapped_column(String(50), default="", nullable=True)
    extra_info: Mapped[str] = mapped_column(
        Text, default="", nullable=True
    )  # 扩展字段（JSON字符串，存用户画像）
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=True)
    # 与 Session 建立双向关系；删除用户时级联删除其会话。
    sessions: Mapped[list["Session"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    profile: Mapped["UserProfile | None"] = relationship(
        "UserProfile", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    agent_runs: Mapped[list["AgentRun"]] = relationship(
        "AgentRun", back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    coros_connection: Mapped["CorosConnection | None"] = relationship(
        "CorosConnection", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    coros_authorization_requests: Mapped[list["CorosAuthorizationRequest"]] = relationship(
        "CorosAuthorizationRequest", back_populates="user", cascade="all, delete-orphan"
    )


class UserProfile(Base):
    __tablename__ = "user_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    gender: Mapped[str] = mapped_column(
        String(10), default="", nullable=True
    )  # 性别值，如 male、female 或 other
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 年龄
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 身高(cm)
    weight: Mapped[float | None] = mapped_column(Float, nullable=True)  # 体重(kg)
    goal: Mapped[str] = mapped_column(
        String(20), default="", nullable=True
    )  # 目标值，如减脂、增肌、塑形、耐力或健康管理
    weekly_days: Mapped[int] = mapped_column(Integer, default=3, nullable=True)  # 每周训练天数 1-7
    # 经验等级实际取值：新手、中级或高级。
    experience: Mapped[str] = mapped_column(String(20), default="新手", nullable=True)
    injuries: Mapped[str] = mapped_column(
        Text, default="[]", nullable=True
    )  # JSON 数组，例如 ["膝盖", "腰椎"]
    diet_restrict: Mapped[str] = mapped_column(
        Text, default="[]", nullable=True
    )  # JSON 数组，例如 ["素食", "低碳"]
    preferences: Mapped[str] = mapped_column(
        Text, default="{}", nullable=True
    )  # JSON 对象，例如训练时间和场馆偏好
    health_data: Mapped[str] = mapped_column(
        Text, default="{}", nullable=True
    )  # JSON: 从文档提取的健康指标
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=True
    )

    user: Mapped["User"] = relationship(back_populates="profile")


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(CHAR(32), primary_key=True)  # UUID 的十六进制字符串
    title: Mapped[str] = mapped_column(String(100), default="新对话", nullable=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=True
    )

    user: Mapped["User"] = relationship(back_populates="sessions")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    session_summary: Mapped["SessionSummary | None"] = relationship(
        "SessionSummary", back_populates="session", uselist=False, cascade="all, delete-orphan"
    )
    agent_runs: Mapped[list["AgentRun"]] = relationship(
        "AgentRun", back_populates="session", cascade="all, delete-orphan", passive_deletes=True
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(32), ForeignKey("sessions.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=True)

    session: Mapped["Session"] = relationship(back_populates="messages")


class AgentRun(Base):
    """保存一轮聊天的摘要、问题与最终回答。"""

    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(CHAR(32), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(
        CHAR(32), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    mode: Mapped[str] = mapped_column(String(20), nullable=False)  # 固定聊天运行模式：chat
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # 执行状态：succeeded 或 failed
    elapsed_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    tool_call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    user_question: Mapped[str] = mapped_column(Text, nullable=False, default="")
    assistant_answer: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=True)

    user: Mapped["User"] = relationship(back_populates="agent_runs")
    session: Mapped["Session"] = relationship(back_populates="agent_runs")
    tool_calls: Mapped[list["AgentToolCall"]] = relationship(
        "AgentToolCall",
        back_populates="agent_run",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="AgentToolCall.sequence",
    )


class AgentToolCall(Base):
    """保存 Collector 采集的单次工具输入和输出。"""

    __tablename__ = "agent_tool_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_run_id: Mapped[str] = mapped_column(
        CHAR(32), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    tool_name: Mapped[str] = mapped_column(String(80), nullable=False)
    tool_input: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    elapsed_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    tool_output: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=True)

    agent_run: Mapped["AgentRun"] = relationship(back_populates="tool_calls")

    __table_args__ = (Index("ix_agent_tool_calls_run_sequence", "agent_run_id", "sequence"),)


class CorosConnection(Base):
    """每位 FitAgent 用户的一条加密 COROS 授权连接。"""

    __tablename__ = "coros_connections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    issuer: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    client_id: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    access_token_ciphertext: Mapped[str] = mapped_column(Text, nullable=False, default="")
    refresh_token_ciphertext: Mapped[str] = mapped_column(Text, nullable=False, default="")
    access_token_expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="connected")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=True
    )

    user: Mapped["User"] = relationship(back_populates="coros_connection")


class CorosAuthorizationRequest(Base):
    """浏览器回调前短暂保存的 state 校验与 PKCE 材料。"""

    __tablename__ = "coros_authorization_requests"

    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    issuer: Mapped[str] = mapped_column(String(255), nullable=False)
    client_id: Mapped[str] = mapped_column(String(255), nullable=False)
    verifier_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=True)

    user: Mapped["User"] = relationship(back_populates="coros_authorization_requests")


class SessionSummary(Base):
    """LLM 生成、可重建的早期用户消息缓存，不替换或删除原始聊天记录。"""

    __tablename__ = "session_summaries"

    id: Mapped[str] = mapped_column(CHAR(32), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        CHAR(32), ForeignKey("sessions.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    covered_through_message_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=True
    )

    session: Mapped["Session"] = relationship(back_populates="session_summary")


class MemoryFact(Base):
    """用户可确认、撤销和过期的长期记忆条目。"""

    __tablename__ = "memory_facts"

    id: Mapped[str] = mapped_column(CHAR(32), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_message_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    supersedes_id: Mapped[str | None] = mapped_column(CHAR(32), nullable=True)
    fact_key: Mapped[str] = mapped_column(String(80), nullable=False)
    category: Mapped[str] = mapped_column(String(30), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    display_text: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="proposed")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=True
    )

    __table_args__ = (Index("ix_memory_facts_user_status_key", "user_id", "status", "fact_key"),)


class TrainingPlan(Base):
    """用户显式生成的结构化周训练计划草稿或生效版本。"""

    __tablename__ = "training_plans"

    id: Mapped[str] = mapped_column(CHAR(32), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    week_start: Mapped[DateValue] = mapped_column(Date, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    plan_data: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    safety_data: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=True
    )

    feedbacks: Mapped[list["TrainingFeedback"]] = relationship(
        "TrainingFeedback", back_populates="plan", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_training_plans_user_week", "user_id", "week_start"),)


class TrainingFeedback(Base):
    """计划执行后的用户反馈，供下一版训练计划安全调整使用。"""

    __tablename__ = "training_feedbacks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[str] = mapped_column(
        CHAR(32), ForeignKey("training_plans.id", ondelete="CASCADE"), nullable=False
    )
    day_of_week: Mapped[int] = mapped_column(Integer, nullable=False)
    completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    rpe: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pain_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=True)

    plan: Mapped["TrainingPlan"] = relationship(back_populates="feedbacks")

    __table_args__ = (UniqueConstraint("plan_id", "day_of_week", name="uq_plan_feedback_day"),)
