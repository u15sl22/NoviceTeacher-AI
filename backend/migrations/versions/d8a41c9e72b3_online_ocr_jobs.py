"""Add durable online OCR document jobs.

Revision ID: d8a41c9e72b3
Revises: c4b8a7f6d210
"""
from alembic import op
import sqlalchemy as sa

revision = 'd8a41c9e72b3'
down_revision = 'c4b8a7f6d210'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('uploaded_documents') as batch:
        batch.drop_constraint('ck_uploaded_document_parse_status', type_='check')
        batch.alter_column('parser_name', existing_type=sa.String(length=100), nullable=True)
        batch.alter_column('extracted_text', existing_type=sa.Text(), nullable=True)
        batch.add_column(sa.Column('parse_error', sa.Text(), nullable=True))
        batch.add_column(sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True))
        batch.create_check_constraint('ck_uploaded_document_parse_status',
            "parse_status IN ('queued','processing','parsed','failed')")
    op.execute("UPDATE uploaded_documents SET updated_at = created_at WHERE updated_at IS NULL")
    with op.batch_alter_table('uploaded_documents') as batch:
        batch.alter_column('updated_at', existing_type=sa.DateTime(timezone=True), nullable=False)

    op.create_table('document_processing_jobs',
        sa.Column('document_id', sa.String(length=36), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('attempt_count', sa.Integer(), nullable=False),
        sa.Column('current_page', sa.Integer(), nullable=False),
        sa.Column('total_pages', sa.Integer(), nullable=True),
        sa.Column('lease_expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('error_code', sa.String(length=100), nullable=True),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELLED')",
            name='ck_document_processing_job_status'),
        sa.ForeignKeyConstraint(['document_id'], ['uploaded_documents.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('document_id'))
    op.create_index('ix_document_processing_jobs_document_id', 'document_processing_jobs', ['document_id'])
    op.create_index('ix_document_processing_jobs_status', 'document_processing_jobs', ['status'])
    op.create_index('ix_document_processing_jobs_user_id', 'document_processing_jobs', ['user_id'])


def downgrade():
    raise RuntimeError('OCR job records are persistent research data; restore a backup instead.')
