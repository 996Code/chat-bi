"""add_sql_execution_time_to_audit_logs

Revision ID: 2252be2e8f9f
Revises: a1b2c3d4e5f6
Create Date: 2026-05-06 16:45:50.471470
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2252be2e8f9f'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('audit_logs', sa.Column('sql_execution_time_ms', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('audit_logs', 'sql_execution_time_ms')
