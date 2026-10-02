"""account nickname

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-29
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing accounts get the part of their email before the @ as a first nickname.
    op.add_column(
        "accounts", sa.Column("nickname", sa.String(20), nullable=False, server_default="")
    )
    op.execute(
        "UPDATE accounts SET nickname = COALESCE(NULLIF(left(split_part(email, '@', 1), 20), ''), 'Player')"
    )
    op.alter_column("accounts", "nickname", server_default=None)


def downgrade() -> None:
    op.drop_column("accounts", "nickname")
