"""Async engine/session management and row-level-security scoping.

Every workspace-scoped query must run inside :func:`workspace_scope`, which sets
``app.workspace_id`` for the current transaction. PostgreSQL RLS policies filter on
that setting, so a bug in application-level checks cannot leak another
workspace's rows.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from aether.config import Settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def init_engine(settings: Settings) -> AsyncEngine:
    global _engine, _sessionmaker
    _engine = create_async_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        pool_pre_ping=True,
        connect_args={"server_settings": {"application_name": settings.service_name}},
    )
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


def sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        raise RuntimeError("database engine not initialised")
    return _sessionmaker


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """A session that commits on success and rolls back on error.

    Callers may commit early (e.g. before waiting on a workflow); later statements
    start a new transaction, which no longer carries the RLS workspace setting.
    """
    async with sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


async def set_workspace(session: AsyncSession, workspace_id: uuid.UUID) -> None:
    # set_config(..., is_local => true) scopes the value to the current transaction.
    await session.execute(text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": str(workspace_id)})


@asynccontextmanager
async def workspace_scope(workspace_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
    async with session_scope() as session:
        await set_workspace(session, workspace_id)
        yield session
