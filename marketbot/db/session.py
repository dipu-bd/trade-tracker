import logging
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .models import Base

_log = logging.getLogger(__name__)


class Database:
    def __init__(self, url: str, echo: bool = False):
        self.url = url
        kwargs = {'echo': echo, 'future': True}
        if url.startswith('sqlite'):
            # The scheduler touches the database from a worker thread.
            kwargs['connect_args'] = {'check_same_thread': False}

        self.engine = create_engine(url, **kwargs)
        if url.startswith('sqlite'):
            self._enable_sqlite_pragmas()

        self._session_factory = sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
            future=True,
        )
        Base.metadata.create_all(self.engine)
        _log.info(f'Database ready: {url}')

    def _enable_sqlite_pragmas(self):
        @event.listens_for(self.engine, 'connect')
        def _set_pragmas(conn, _record):
            cursor = conn.cursor()
            cursor.execute('PRAGMA journal_mode=WAL')
            cursor.execute('PRAGMA foreign_keys=ON')
            cursor.close()

    def new_session(self) -> Session:
        return self._session_factory()

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Transactional scope: commits on success, rolls back on failure."""
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    def close(self):
        self.engine.dispose()
