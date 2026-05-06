"""add_metadata_config_versions_table

Revision ID: a1b2c3d4e5f6
Revises: f2bb98a4d001
Create Date: 2026-05-06 15:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = 'a1b2c3d4e5f6'
down_revision = 'f2bb98a4d001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'metadata_config_versions',
        sa.Column('id', mysql.CHAR(36), primary_key=True),
        sa.Column('config_id', mysql.CHAR(36), nullable=False, index=True),
        sa.Column('tenant_id', mysql.CHAR(36), nullable=False, index=True),
        sa.Column('datasource_id', mysql.CHAR(36), nullable=False, index=True),
        sa.Column('version_number', sa.Integer(), nullable=False),
        sa.Column('config_snapshot', sa.Text(), nullable=False),
        sa.Column('change_summary', sa.String(500), server_default=''),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        mysql_charset='utf8mb4',
        mysql_engine='InnoDB',
    )


def downgrade() -> None:
    op.drop_table('metadata_config_versions')
