"""add_conversation_id_to_audit_logs

Revision ID: f98283dae874
Revises: 2252be2e8f9f
Create Date: 2026-05-06 17:54:03.786346
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f98283dae874'
down_revision: Union[str, None] = '2252be2e8f9f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('audit_logs', sa.Column('conversation_id', sa.String(100), nullable=True))
    op.create_index('ix_audit_logs_conversation_id', 'audit_logs', ['conversation_id'])


def downgrade() -> None:
    op.drop_index('ix_audit_logs_conversation_id', 'audit_logs')
    op.drop_column('audit_logs', 'conversation_id')
