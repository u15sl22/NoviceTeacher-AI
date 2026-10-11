"""Add durable asynchronous model generation jobs.

Revision ID: c4b8a7f6d210
Revises: 6f2d8c1a9b40
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'c4b8a7f6d210'
down_revision = '6f2d8c1a9b40'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('generation_jobs',
        sa.Column('session_id', sa.String(length=36), nullable=False),
        sa.Column('round_id', sa.String(length=36), nullable=False),
        sa.Column('section_id', sa.String(length=36), nullable=False),
        sa.Column('section_version_id', sa.String(length=36), nullable=False),
        sa.Column('generation_id', sa.String(length=36), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('context_json', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
        sa.Column('attempt_count', sa.Integer(), nullable=False),
        sa.Column('lease_expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('error_code', sa.String(length=100), nullable=True),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','STALE','CANCELLED')",
                           name='ck_generation_jobs_status'),
        sa.ForeignKeyConstraint(['generation_id'], ['generation_records.id']),
        sa.ForeignKeyConstraint(['round_id'], ['rounds.id']),
        sa.ForeignKeyConstraint(['section_id'], ['sections.id']),
        sa.ForeignKeyConstraint(['section_version_id'], ['section_versions.id']),
        sa.ForeignKeyConstraint(['session_id'], ['sessions.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('generation_id'))
    op.create_index('ix_generation_jobs_session_id', 'generation_jobs', ['session_id'])
    op.create_index('ix_generation_jobs_status', 'generation_jobs', ['status'])
    op.create_index('ix_generation_jobs_user_id', 'generation_jobs', ['user_id'])


def downgrade():
    raise RuntimeError('Generation jobs contain research audit state; restore a backup instead.')
