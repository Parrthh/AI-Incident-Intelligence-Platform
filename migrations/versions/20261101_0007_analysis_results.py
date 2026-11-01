"""Store structured analysis results; key analyses by their input too.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("analyses", sa.Column("input_hash", sa.Text(), server_default="", nullable=False))
    op.add_column(
        "analyses", sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )
    op.drop_constraint("uq_analyses_incident_kind_prompt", "analyses", type_="unique")
    op.create_unique_constraint(
        "uq_analyses_incident_kind_prompt_input",
        "analyses",
        ["incident_id", "kind", "prompt_version", "input_hash"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_analyses_incident_kind_prompt_input", "analyses", type_="unique")
    op.execute("DELETE FROM analyses WHERE input_hash <> ''")
    op.create_unique_constraint(
        "uq_analyses_incident_kind_prompt", "analyses", ["incident_id", "kind", "prompt_version"]
    )
    op.drop_column("analyses", "result")
    op.drop_column("analyses", "input_hash")
