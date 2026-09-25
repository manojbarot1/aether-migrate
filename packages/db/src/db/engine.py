"""Async SQLAlchemy engine and session factory.

The engine is created lazily on first call to ``get_engine()``.
``DATABASE_URL`` must be set in the environment and use the
``postgresql+asyncpg`` scheme.

Example::

    DATABASE_URL=postgresql+asyncpg://aether:secret@localhost:5432/aether

"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """Return (and lazily create) the shared async engine.

    Call once at application startup; subsequent calls return the cached instance.
    """
    global _engine, _session_factory
    if _engine is None:
        database_url = os.environ["DATABASE_URL"]
        _engine = create_async_engine(
            database_url,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
            echo=os.environ.get("DB_ECHO", "false").lower() == "true",
        )
        _session_factory = async_sessionmaker(
            _engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _engine


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Async context manager that yields a database session.

    Commits on clean exit; rolls back on any exception.

    Usage::

        async with get_session() as session:
            result = await session.execute(select(WorkspaceRow))
    """
    if _session_factory is None:
        get_engine()  # initialise lazily
    assert _session_factory is not None

    async with _session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
