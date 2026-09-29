from alembic import context
from app.models import Base
from app.db import make_engine
from app.settings import settings

if context.is_offline_mode():
    context.configure(url=settings.database_url, target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    with make_engine(settings.database_url).connect() as connection:
        if connection.dialect.name == 'sqlite':
            connection.exec_driver_sql('PRAGMA foreign_keys=OFF')
            connection.commit()
        context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()
        if connection.dialect.name == 'sqlite':
            violations = connection.exec_driver_sql('PRAGMA foreign_key_check').fetchall()
            if violations: raise RuntimeError('Migration introduced foreign key violations')
