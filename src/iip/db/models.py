"""Database tables (SQLAlchemy 2 typed models).

Schema changes always go through an Alembic migration in ``migrations/versions``;
these classes describe the tables for queries and must match the migrations.
``tests/integration/test_schema.py`` checks that they do.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Explicit constraint names, so migrations are predictable and renames are clean.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Incident(Base):
    __tablename__ = "incidents"
    __table_args__ = (
        CheckConstraint("status IN ('open', 'resolved')", name="status"),
        Index("ix_incidents_started_at", text("started_at DESC")),
        Index("ix_incidents_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    title: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="open")
    severity: Mapped[str | None] = mapped_column(Text)
    services: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    alerts: Mapped[list["Alert"]] = relationship(back_populates="incident")


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (
        # One row per alert occurrence. Alertmanager re-sends the same alert every
        # repeat_interval and again when it resolves; those must update this row,
        # not add new ones. Same fingerprint + new startsAt = the alert fired again.
        UniqueConstraint("fingerprint", "starts_at", name="uq_alerts_fingerprint_starts_at"),
        CheckConstraint("status IN ('firing', 'resolved')", name="status"),
        Index("ix_alerts_incident_id", "incident_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    source: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    alertname: Mapped[str] = mapped_column(Text)
    service: Mapped[str | None] = mapped_column(Text)
    severity: Mapped[str | None] = mapped_column(Text)
    labels: Mapped[dict[str, Any]] = mapped_column(JSONB)
    annotations: Mapped[dict[str, Any]] = mapped_column(JSONB)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    incident_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id", ondelete="SET NULL")
    )
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB)

    incident: Mapped[Incident | None] = relationship(back_populates="alerts")


class Analysis(Base):
    """One LLM run for an incident. Every field needed to reproduce or audit the
    result is stored: provider, model, prompt version, token counts, latency, error."""

    __tablename__ = "analyses"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'done', 'failed')", name="status"),
        Index("ix_analyses_incident_id_created_at", "incident_id", text("created_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    incident_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    provider: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    redactions: Mapped[int] = mapped_column(Integer, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
