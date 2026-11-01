"""Add the outbox table and one-row-per-run claims on analyses.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "outbox",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("stream", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_outbox"),
    )
    op.create_index(
        "ix_outbox_unpublished",
        "outbox",
        ["created_at"],
        postgresql_where=sa.text("published_at IS NULL"),
    )
    op.create_index("ix_outbox_published_at", "outbox", ["published_at"])

    op.add_column("analyses", sa.Column("claimed_by", sa.Text(), nullable=True))
    op.add_column("analyses", sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True))
    # M1 could, rarely, write two rows for one incident. Keep the best one (a
    # finished summary first, then the newest) before adding the unique constraint.
    op.execute(
        """
        DELETE FROM analyses a
        USING analyses b
        WHERE a.incident_id = b.incident_id
          AND a.kind = b.kind
          AND a.prompt_version = b.prompt_version
          AND (a.status = 'done', a.created_at, a.id) < (b.status = 'done', b.created_at, b.id)
        """
    )
    op.create_unique_constraint(
        "uq_analyses_incident_kind_prompt", "analyses", ["incident_id", "kind", "prompt_version"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_analyses_incident_kind_prompt", "analyses", type_="unique")
    op.drop_column("analyses", "claimed_at")
    op.drop_column("analyses", "claimed_by")
    op.drop_index("ix_outbox_published_at", table_name="outbox")
    op.drop_index("ix_outbox_unpublished", table_name="outbox")
    op.drop_table("outbox")
