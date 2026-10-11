"""Allow a stale custom prompt to create a new audited generation attempt.

Revision ID: f6c3b9d1e824
Revises: e2f7a9c4b631
"""
from alembic import op


revision = 'f6c3b9d1e824'
down_revision = 'e2f7a9c4b631'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('generation_records') as batch:
        batch.drop_constraint('uq_generation_records_custom_prompt', type_='unique')


def downgrade():
    raise RuntimeError('Refinement retry attempts contain audit history; restore a backup instead.')
