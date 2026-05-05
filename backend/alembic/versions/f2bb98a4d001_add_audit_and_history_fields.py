"""add_audit_and_history_fields

Revision ID: f2bb98a4d001
Revises: e1aa87f3ee5f
Create Date: 2026-05-05 12:00:00.000000

Add structured audit fields and query history metadata.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'f2bb98a4d001'
down_revision: Union[str, None] = 'e1aa87f3ee5f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # AuditLog: add structured query audit fields
    op.add_column('audit_logs', sa.Column('sql_text', sa.Text(), nullable=True))
    op.add_column('audit_logs', sa.Column('result_count', sa.Integer(), nullable=True))
    op.add_column('audit_logs', sa.Column('execution_time_ms', sa.Integer(), nullable=True))
    op.add_column('audit_logs', sa.Column('error_message', sa.String(500), nullable=True))

    # SavedQuery: add execution metadata fields
    op.add_column('saved_queries', sa.Column('success', sa.Boolean(), nullable=True))
    op.add_column('saved_queries', sa.Column('execution_time_ms', sa.Integer(), nullable=True))
    op.add_column('saved_queries', sa.Column('row_count', sa.Integer(), nullable=True))
    op.add_column('saved_queries', sa.Column('error', sa.String(500), nullable=True))
    op.add_column('saved_queries', sa.Column('chart_type', sa.String(50), nullable=True))

    # SavedQuery: make name and generated_sql nullable
    op.alter_column('saved_queries', 'name', existing_type=sa.String(200), nullable=True)
    op.alter_column('saved_queries', 'generated_sql', existing_type=sa.Text(), nullable=True)

    # User: add role check constraint
    op.create_check_constraint('ck_user_role', 'users', "role IN ('admin', 'user', 'read_only')")


def downgrade() -> None:
    # User: drop role check constraint
    op.drop_constraint('ck_user_role', 'users', type_='check')

    # SavedQuery: revert nullable changes
    op.alter_column('saved_queries', 'generated_sql', existing_type=sa.Text(), nullable=False)
    op.alter_column('saved_queries', 'name', existing_type=sa.String(200), nullable=False)

    # SavedQuery: drop execution metadata fields
    op.drop_column('saved_queries', 'chart_type')
    op.drop_column('saved_queries', 'error')
    op.drop_column('saved_queries', 'row_count')
    op.drop_column('saved_queries', 'execution_time_ms')
    op.drop_column('saved_queries', 'success')

    # AuditLog: drop structured fields
    op.drop_column('audit_logs', 'error_message')
    op.drop_column('audit_logs', 'execution_time_ms')
    op.drop_column('audit_logs', 'result_count')
    op.drop_column('audit_logs', 'sql_text')