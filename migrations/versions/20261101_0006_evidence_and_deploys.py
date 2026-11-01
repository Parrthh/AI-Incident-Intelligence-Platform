"""Add evidence, deploys, and enrichment scheduling on incidents.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("incidents", sa.Column("enrich_after", sa.DateTime(timezone=True)))
    op.add_column("incidents", sa.Column("enriched_at", sa.DateTime(timezone=True)))

    op.create_table(
        "evidence",
        sa.Column("incident_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dedupe_key", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("incident_id", "id", name="pk_evidence"),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name="fk_evidence_incident_id_incidents",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "incident_id", "kind", "dedupe_key", name="uq_evidence_incident_kind_dedupe"
        ),
    )
    op.create_index(
        "ix_evidence_incident_id_observed_at", "evidence", ["incident_id", "observed_at"]
    )

    op.create_table(
        "deploys",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("service", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=True),
        sa.Column("commit_sha", sa.Text(), nullable=True),
        sa.Column("deployed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_deploys"),
    )
    op.create_index("ix_deploys_service_deployed_at", "deploys", ["service", "deployed_at"])


def downgrade() -> None:
    op.drop_index("ix_deploys_service_deployed_at", table_name="deploys")
    op.drop_table("deploys")
    op.drop_index("ix_evidence_incident_id_observed_at", table_name="evidence")
    op.drop_table("evidence")
    op.drop_column("incidents", "enriched_at")
    op.drop_column("incidents", "enrich_after")
