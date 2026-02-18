"""add_pool_post_create_time

Revision ID: add_pool_post_time
Revises: add_huluxia_state
Create Date: 2026-02-18 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "add_pool_post_time"
down_revision: Union[str, None] = "add_huluxia_state"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """添加泳池灌水功能的帖子创建时间字段"""
    op.add_column(
        "huluxia_adapter_state",
        sa.Column(
            "last_pool_post_create_time",
            sa.BigInteger(),
            nullable=True,
            server_default="0",
        ),
    )


def downgrade() -> None:
    """移除泳池灌水功能的帖子创建时间字段"""
    op.drop_column("huluxia_adapter_state", "last_pool_post_create_time")
