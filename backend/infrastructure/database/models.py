"""SQLAlchemy models for account identity and authenticated workbench sessions."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class AccountModel(Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(80), default="")
    password_hash: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="active")
    role: Mapped[str] = mapped_column(String(24), default="user")
    quota_tokens: Mapped[int] = mapped_column(Integer, default=100_000)
    used_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    sessions: Mapped[list["SessionModel"]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )
    harness_sessions: Mapped[list["HarnessSessionModel"]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )
    materials: Mapped[list["MaterialModel"]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )
    conversations: Mapped[list["ConversationModel"]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )
    projects: Mapped[list["ProjectModel"]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )


class SessionModel(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    account_id: Mapped[str] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    account: Mapped[AccountModel] = relationship(back_populates="sessions")
    harness_sessions: Mapped[list["HarnessSessionModel"]] = relationship(
        back_populates="auth_session", cascade="all, delete-orphan"
    )


class HarnessSessionModel(Base):
    __tablename__ = "harness_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    account_id: Mapped[str] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    auth_session_id: Mapped[str] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(24), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_active_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    account: Mapped[AccountModel] = relationship(back_populates="harness_sessions")
    auth_session: Mapped[SessionModel] = relationship(
        back_populates="harness_sessions"
    )


class MaterialModel(Base):
    __tablename__ = "materials"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    account_id: Mapped[str] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    media_type: Mapped[str] = mapped_column(String(255))
    size_bytes: Mapped[int] = mapped_column(Integer)
    checksum: Mapped[str] = mapped_column(String(64), index=True)
    storage_key: Mapped[str] = mapped_column(Text, unique=True)
    status: Mapped[str] = mapped_column(String(24), default="uploaded")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    account: Mapped[AccountModel] = relationship(back_populates="materials")


class ConversationModel(Base):
    """Account-owned workspace used as the Harness scheduler session key."""

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    account_id: Mapped[str] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), index=True
    )
    title: Mapped[str] = mapped_column(String(200), default="新对话")
    # A conversation has one user-facing mode.  Individual Runs copy this
    # value when accepted so changing the conversation later cannot widen or
    # narrow an already running task.
    mode: Mapped[str] = mapped_column(String(16), default="chat")
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    account: Mapped[AccountModel] = relationship(back_populates="conversations")
    project: Mapped["ProjectModel | None"] = relationship(back_populates="conversations")
    materials: Mapped[list["ConversationMaterialModel"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )
    runs: Mapped[list["ConversationRunModel"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class ProjectModel(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    account_id: Mapped[str] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    account: Mapped[AccountModel] = relationship(back_populates="projects")
    conversations: Mapped[list[ConversationModel]] = relationship(
        back_populates="project"
    )


class ConversationMaterialModel(Base):
    """Join table that scopes an uploaded material to a conversation."""

    __tablename__ = "conversation_materials"

    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), primary_key=True
    )
    material_id: Mapped[str] = mapped_column(
        ForeignKey("materials.id", ondelete="CASCADE"), primary_key=True
    )

    conversation: Mapped[ConversationModel] = relationship(back_populates="materials")
    material: Mapped[MaterialModel] = relationship()


class ConversationRunModel(Base):
    """Durable lightweight Run history; scheduler remains the runtime source."""

    __tablename__ = "conversation_runs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str | None] = mapped_column(Text)
    partial_output: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="queued")
    revision: Mapped[int] = mapped_column(Integer, default=0)
    client_request_id: Mapped[str | None] = mapped_column(String(128))
    retry_of_run_id: Mapped[str | None] = mapped_column(String(64))
    payload_json: Mapped[str | None] = mapped_column(Text)
    material_ids_json: Mapped[str | None] = mapped_column(Text)
    progress_json: Mapped[str | None] = mapped_column(Text)
    last_sequence: Mapped[int] = mapped_column(Integer, default=0)
    first_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_delta_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    conversation: Mapped[ConversationModel] = relationship(back_populates="runs")


class UsageLogModel(Base):
    """One durable usage record per account run."""

    __tablename__ = "usage_logs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(32), index=True)
    run_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(32), index=True)
    model: Mapped[str] = mapped_column(String(80), default="deepseek")
    mode: Mapped[str] = mapped_column(String(16), default="chat")
    status: Mapped[str] = mapped_column(String(24), default="completed")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLogModel(Base):
    """Security and administrative events kept independently of usage cleanup."""

    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    actor_account_id: Mapped[str | None] = mapped_column(String(32), index=True)
    actor_email: Mapped[str | None] = mapped_column(String(320))
    actor_role: Mapped[str] = mapped_column(String(24), default="anonymous")
    target_account_id: Mapped[str | None] = mapped_column(String(32), index=True)
    target_email: Mapped[str | None] = mapped_column(String(320))
    category: Mapped[str] = mapped_column(String(24), index=True)
    action: Mapped[str] = mapped_column(String(128), index=True)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    status_code: Mapped[int] = mapped_column(Integer, default=200)
    request_id: Mapped[str | None] = mapped_column(String(64), index=True)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    details_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


Index("ix_sessions_account_active", SessionModel.account_id, SessionModel.revoked_at)
Index(
    "ix_harness_sessions_account_status",
    HarnessSessionModel.account_id,
    HarnessSessionModel.status,
)
Index("ix_materials_account_session", MaterialModel.account_id, MaterialModel.session_id)
Index("ix_conversations_account_active", ConversationModel.account_id, ConversationModel.archived_at)
Index("ix_conversations_account_project", ConversationModel.account_id, ConversationModel.project_id)
Index("ix_conversations_account_order", ConversationModel.account_id, ConversationModel.pinned, ConversationModel.updated_at)
Index("ix_conversation_runs_conversation_created", ConversationRunModel.conversation_id, ConversationRunModel.created_at)
Index(
    "uq_conversation_runs_idempotency",
    ConversationRunModel.conversation_id,
    ConversationRunModel.client_request_id,
    unique=True,
)
Index("ix_projects_account_order", ProjectModel.account_id, ProjectModel.updated_at)
Index("ix_usage_logs_account_created", UsageLogModel.account_id, UsageLogModel.created_at)
Index("ix_audit_logs_category_created", AuditLogModel.category, AuditLogModel.created_at)
Index("ix_audit_logs_target_created", AuditLogModel.target_account_id, AuditLogModel.created_at)
