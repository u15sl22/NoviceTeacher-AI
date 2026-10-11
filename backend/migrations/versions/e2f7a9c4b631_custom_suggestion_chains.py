"""Add repeatable custom suggestion refinement chains.

Revision ID: e2f7a9c4b631
Revises: d8a41c9e72b3
"""
from alembic import op
import sqlalchemy as sa


revision = 'e2f7a9c4b631'
down_revision = 'd8a41c9e72b3'
branch_labels = None
depends_on = None


NAMING = {'uq': 'uq_%(table_name)s_%(column_0_name)s',
          'ck': 'ck_%(table_name)s_%(constraint_name)s'}


def _unique_name(table, columns, offline_default):
    if op.get_context().as_sql:
        return offline_default
    for item in sa.inspect(op.get_bind()).get_unique_constraints(table):
        if item.get('column_names') == columns:
            return item['name']
    raise RuntimeError(f'Cannot find legacy unique constraint on {table}: {columns}')


def _decision_check_name():
    if op.get_context().as_sql:
        return 'decisions_decision_check'
    checks = sa.inspect(op.get_bind()).get_check_constraints('decisions')
    for item in checks:
        if "'ACCEPT'" in item.get('sqltext', '') and "'REJECT'" in item.get('sqltext', ''):
            return item['name']
    raise RuntimeError('Cannot find legacy decision check constraint')


def upgrade():
    old_suggestion_unique = _unique_name('suggestions',
        ['round_id', 'section_id', 'suggestion_index'],
        'suggestions_round_id_section_id_suggestion_index_key')
    old_decision_check = _decision_check_name()

    with op.batch_alter_table('suggestions', naming_convention=NAMING) as batch:
        batch.add_column(sa.Column('thread_id', sa.String(36), nullable=True))
        batch.add_column(sa.Column('parent_suggestion_id', sa.String(36), nullable=True))
        batch.add_column(sa.Column('attempt_number', sa.Integer(), nullable=False, server_default='1'))
        batch.create_foreign_key('fk_suggestions_parent', 'suggestions',
                                 ['parent_suggestion_id'], ['id'])
    op.execute('UPDATE suggestions SET thread_id = id WHERE thread_id IS NULL')
    with op.batch_alter_table('suggestions', naming_convention=NAMING) as batch:
        batch.alter_column('thread_id', existing_type=sa.String(36), nullable=False)
        batch.alter_column('attempt_number', existing_type=sa.Integer(), server_default=None)
        batch.drop_constraint(old_suggestion_unique, type_='unique')
        batch.create_unique_constraint('uq_suggestions_thread_attempt', ['thread_id', 'attempt_number'])
        batch.create_check_constraint('ck_suggestions_attempt_number', 'attempt_number >= 1')
        batch.create_index('ix_suggestions_thread_id', ['thread_id'])

    with op.batch_alter_table('decisions', naming_convention=NAMING) as batch:
        batch.drop_constraint(old_decision_check, type_='check')
        batch.create_check_constraint('ck_decision_value',
                                      "decision IN ('ACCEPT','REJECT','CUSTOM')")

    with op.batch_alter_table('generation_records', naming_convention=NAMING) as batch:
        batch.add_column(sa.Column('generation_kind', sa.String(20), nullable=False,
                                   server_default='INITIAL'))
        batch.add_column(sa.Column('parent_suggestion_id', sa.String(36), nullable=True))
        batch.add_column(sa.Column('custom_prompt_id', sa.String(36), nullable=True))
        batch.create_unique_constraint('uq_generation_records_custom_prompt', ['custom_prompt_id'])
        batch.alter_column('generation_kind', existing_type=sa.String(20), server_default=None)

    with op.batch_alter_table('custom_prompts', naming_convention=NAMING) as batch:
        batch.add_column(sa.Column('request_key', sa.String(36), nullable=True))
        batch.add_column(sa.Column('suggestion_id', sa.String(36), nullable=True))
        batch.add_column(sa.Column('generation_id', sa.String(36), nullable=True))
        batch.create_foreign_key('fk_custom_prompts_suggestion', 'suggestions',
                                 ['suggestion_id'], ['id'])
        batch.create_foreign_key('fk_custom_prompts_generation', 'generation_records',
                                 ['generation_id'], ['id'])
        batch.create_unique_constraint('uq_custom_prompts_user_request', ['user_id', 'request_key'])
        batch.create_unique_constraint('uq_custom_prompts_suggestion', ['suggestion_id'])
        batch.create_unique_constraint('uq_custom_prompts_generation', ['generation_id'])
    # The feature was disabled before this migration, so no legacy custom prompt
    # rows can exist in a supported database. Keep the assertion explicit.
    bind = op.get_bind()
    if not op.get_context().as_sql:
        count = bind.execute(sa.text('SELECT COUNT(*) FROM custom_prompts')).scalar_one()
        if count:
            raise RuntimeError('Unexpected legacy custom_prompts rows require a manual migration')
    with op.batch_alter_table('custom_prompts', naming_convention=NAMING) as batch:
        batch.alter_column('request_key', existing_type=sa.String(36), nullable=False)
        batch.alter_column('suggestion_id', existing_type=sa.String(36), nullable=False)


def downgrade():
    raise RuntimeError('Custom suggestion chains contain research audit history; restore a backup instead.')
