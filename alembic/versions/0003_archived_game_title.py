"""archived game title

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-30
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("archived_games", sa.Column("title", sa.String(80), nullable=True))


def downgrade() -> None:
    op.drop_column("archived_games", "title")
