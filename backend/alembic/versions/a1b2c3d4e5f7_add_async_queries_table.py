"""add async_queries table for background query execution

Revision ID: a1b2c3d4e5f7
Revises: f98283dae874
Create Date: 2026-05-07 10:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f7'
down_revision: Union[str, None] = 'f98283dae874'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'async_queries',
        sa.Column('id', sa.String(36), primary_key=True, nullable=False),
        sa.Column('tenant_id', sa.String(36), nullable=False),
        sa.Column('user_id', sa.String(36), nullable=False),
        sa.Column('datasource_id', sa.String(36), nullable=False),
        sa.Column('question', sa.Text(), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default=sa.text("'pending'")),
        sa.Column('generated_sql', sa.Text(), nullable=True),
        sa.Column('columns', sa.Text(), nullable=True),
        sa.Column('rows', sa.Text(), nullable=True),
        sa.Column('row_count', sa.Integer(), nullable=True),
        sa.Column('error', sa.String(500), nullable=True),
        sa.Column('chart_type', sa.String(50), nullable=True),
        sa.Column('execution_time_ms', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=True),
    )
    op.create_index('ix_async_queries_tenant_id', 'async_queries', ['tenant_id'])
    op.create_index('ix_async_queries_user_id', 'async_queries', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_async_queries_user_id', table_name='async_queries')
    op.drop_index('ix_async_queries_tenant_id', table_name='async_queries')
    op.drop_table('async_queries')
