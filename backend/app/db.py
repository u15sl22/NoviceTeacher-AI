from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from .settings import settings


def make_engine(url):
    engine = create_engine(url, pool_pre_ping=True,
                           connect_args={'check_same_thread': False, 'timeout': 60}
                           if url.startswith('sqlite') else {})
    if url.startswith('sqlite'):
        @event.listens_for(engine, 'connect')
        def sqlite_setup(connection, _):
            connection.execute('PRAGMA foreign_keys=ON')
            connection.execute('PRAGMA journal_mode=WAL')
    return engine


engine = make_engine(settings.database_url)
SessionLocal = sessionmaker(engine, expire_on_commit=False)
