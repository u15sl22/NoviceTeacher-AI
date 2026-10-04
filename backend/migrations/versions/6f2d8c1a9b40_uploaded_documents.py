"""Persist uploaded source documents.

Revision ID: 6f2d8c1a9b40
Revises: f3cc62ac42cf
"""
from alembic import op
import sqlalchemy as sa

revision = '6f2d8c1a9b40'
down_revision = 'f3cc62ac42cf'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('uploaded_documents',
        sa.Column('session_id', sa.String(length=36), nullable=True),
        sa.Column('original_filename', sa.Text(), nullable=False),
        sa.Column('storage_key', sa.Text(), nullable=False),
        sa.Column('media_type', sa.String(length=100), nullable=False),
        sa.Column('byte_size', sa.Integer(), nullable=False),
        sa.Column('sha256', sa.String(length=64), nullable=False),
        sa.Column('parse_status', sa.String(length=20), nullable=False),
        sa.Column('parser_name', sa.String(length=100), nullable=False),
        sa.Column('extracted_text', sa.Text(), nullable=False),
        sa.Column('page_count', sa.Integer(), nullable=True),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("parse_status IN ('parsed')", name='ck_uploaded_document_parse_status'),
        sa.ForeignKeyConstraint(['session_id'], ['sessions.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('session_id'),
        sa.UniqueConstraint('storage_key'))
    op.create_index('ix_uploaded_documents_user_id', 'uploaded_documents', ['user_id'])


def downgrade():
    raise RuntimeError('Uploaded source records are persistent research data; restore a backup instead.')
