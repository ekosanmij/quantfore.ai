"""Database helpers for research scripts and tests."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, Optional

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from quantfore_research.config import Settings
from quantfore_research.models import Base


def build_engine(
    settings: Optional[Settings] = None,
    database_url: Optional[str] = None,
) -> Engine:
    """Create a SQLAlchemy engine from settings or an explicit URL."""

    resolved_settings = settings or Settings.from_env()
    resolved_url = database_url or resolved_settings.database_url
    connect_args = {}

    is_sqlite = resolved_url.startswith("sqlite")

    if is_sqlite:
        connect_args["check_same_thread"] = False

    engine = create_engine(
        resolved_url,
        echo=resolved_settings.echo_sql,
        future=True,
        connect_args=connect_args,
    )

    if is_sqlite:
        @event.listens_for(engine, "connect")
        def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):
            del connection_record
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def create_schema(engine: Engine) -> None:
    """Create all currently known research tables."""

    Base.metadata.create_all(bind=engine)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Build a session factory with predictable commit behaviour."""

    return sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
        future=True,
    )


@contextmanager
def session_scope(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    """Provide a transactional scope around a set of database operations."""

    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
