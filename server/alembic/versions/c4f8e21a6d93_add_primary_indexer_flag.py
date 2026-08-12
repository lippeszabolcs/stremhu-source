"""add_primary_indexer_flag

Revision ID: c4f8e21a6d93
Revises: a7c31d90b4e2
Create Date: 2026-08-12 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4f8e21a6d93"
down_revision: str | None = "a7c31d90b4e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("indexer_accounts", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "is_primary",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("1"),
            )
        )

    # A meglévő egyéni (Torznab) indexerek alapból NE legyenek elsődlegesek
    op.execute(
        "UPDATE indexer_accounts SET is_primary = 0 WHERE indexer_id IN "
        "(SELECT id FROM indexer_definitions WHERE kind != 'builtin')"
    )


def downgrade() -> None:
    with op.batch_alter_table("indexer_accounts", schema=None) as batch_op:
        batch_op.drop_column("is_primary")
