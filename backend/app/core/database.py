"""Async SQLAlchemy database engine and session management with logging."""

import logging
import time

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

logger = logging.getLogger("hireai.database")

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,  # We handle our own logging
    future=True,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""
    pass


async def get_db() -> AsyncSession:
    """Dependency that yields an async database session."""
    start = time.time()
    session_id = id(object())  # cheap unique id for tracing
    logger.debug(f"[session:{session_id:x}] Opening database session")

    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
            elapsed = int((time.time() - start) * 1000)
            logger.debug(f"[session:{session_id:x}] Committed & closed | {elapsed}ms")
        except Exception as e:
            await session.rollback()
            elapsed = int((time.time() - start) * 1000)
            logger.error(
                f"[session:{session_id:x}] Rolled back due to error | {elapsed}ms"
                f" | {type(e).__name__}: {e}"
            )
            raise
        finally:
            await session.close()


async def init_db():
    """Create all database tables."""
    logger.info("Initializing database schema...")
    start = time.time()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    elapsed = int((time.time() - start) * 1000)
    logger.info(f"Database schema initialized | {elapsed}ms")
