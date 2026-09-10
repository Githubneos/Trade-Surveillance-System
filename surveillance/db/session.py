"""Engine/session plumbing. Kept deliberately thin so detection modules can be tested
without a database."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from surveillance.config import get_settings


@lru_cache
def get_engine(url: str | None = None) -> Engine:
    settings = get_settings()
    return create_engine(url or settings.database_url, pool_pre_ping=True, future=True)


@lru_cache
def get_sessionmaker(url: str | None = None) -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(url), expire_on_commit=False, future=True)


@contextmanager
def session_scope(url: str | None = None) -> Iterator[Session]:
    session = get_sessionmaker(url)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
