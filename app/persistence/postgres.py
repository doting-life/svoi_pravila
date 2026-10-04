from __future__ import annotations

from datetime import datetime
from typing import Any

from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class UserModel(Base):
    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    first_name: Mapped[str] = mapped_column(String(256), nullable=False)
    last_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    language_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    default_relationship_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    relationships: Mapped[list["RelationshipModel"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class RelationshipModel(Base):
    __tablename__ = "relationships"

    relationship_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.user_id", ondelete="CASCADE"), index=True, nullable=False
    )
    relation_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    communication_style: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    ruleset_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user: Mapped[UserModel] = relationship(back_populates="relationships")
    rules: Mapped[list["RelationshipRuleModel"]] = relationship(
        back_populates="relationship",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="RelationshipRuleModel.priority.desc()",
    )


class RelationshipRuleModel(Base):
    __tablename__ = "relationship_rules"
    __table_args__ = (
        UniqueConstraint("relationship_id", "id", name="uq_relationship_rule_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    relationship_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("relationships.relationship_id", ondelete="CASCADE"), index=True, nullable=False
    )
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    relationship: Mapped[RelationshipModel] = relationship(back_populates="rules")


class ServiceEventModel(Base):
    """Append-only, privacy-safe service telemetry. No message, prompt, output or rule text columns."""

    __tablename__ = "service_events"
    __table_args__ = (
        Index("ix_service_events_at_stage", "at", "stage"),
        Index("ix_service_events_request_id", "request_id"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    request_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    relationship_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    workflow: Mapped[str] = mapped_column(String(32), nullable=False)
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    provider_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    generation_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    validation_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    generate_attempts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ruleset_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    self_check_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(64), nullable=True)


def build_async_engine(database_url: str, *, echo: bool = False) -> AsyncEngine:
    return create_async_engine(database_url, pool_pre_ping=True, echo=echo)


def build_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def create_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
