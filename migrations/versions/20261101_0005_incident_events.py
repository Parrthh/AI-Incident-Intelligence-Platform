"""Add incident_events (the incident timeline).

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "incident_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("incident_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_incident_events"),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name="fk_incident_events_incident_id_incidents",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_incident_events_incident_id_at", "incident_events", ["incident_id", "at"])
    # Backfill: every existing incident gets an "opened" event, so old incidents
    # have a timeline too.
    op.execute(
        """
        INSERT INTO incident_events (incident_id, at, kind, payload)
        SELECT id, started_at, 'incident_opened',
               jsonb_build_object('reason', 'Opened before grouping existed.')
        FROM incidents
        """
    )
    op.execute(
        """
        INSERT INTO incident_events (incident_id, at, kind, payload)
        SELECT id, resolved_at, 'incident_resolved',
               jsonb_build_object('reason', 'Its alert resolved.')
        FROM incidents
        WHERE status = 'resolved' AND resolved_at IS NOT NULL
        """
    )
    # Correlation looks up open incidents by service (`services && ARRAY[...]`).
    op.create_index("ix_incidents_services", "incidents", ["services"], postgresql_using="gin")


def downgrade() -> None:
    op.drop_index("ix_incidents_services", table_name="incidents")
    op.drop_index("ix_incident_events_incident_id_at", table_name="incident_events")
    op.drop_table("incident_events")
