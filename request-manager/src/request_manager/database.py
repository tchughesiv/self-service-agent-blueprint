"""Database configuration and connection management."""

import os
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from .models import Base


class DatabaseConfig:
    """Database configuration."""

    def __init__(self) -> None:
        # Use the existing llama_agents database
        self.host = os.getenv("POSTGRES_HOST", "pgvector")
        self.port = int(os.getenv("POSTGRES_PORT", "5432"))
        self.database = os.getenv("POSTGRES_DB", "llama_agents")
        self.user = os.getenv("POSTGRES_USER", "pgvector")
        self.password = os.getenv("POSTGRES_PASSWORD", "pgvector")
        
        self.database_url = (
            f"postgresql+asyncpg://{self.user}:{self.password}"
            f"@{self.host}:{self.port}/{self.database}"
        )


class DatabaseManager:
    """Database connection and session management."""

    def __init__(self, config: DatabaseConfig) -> None:
        self.config = config
        self.engine = create_async_engine(
            config.database_url,
            poolclass=NullPool,  # Use NullPool for serverless environments
            echo=os.getenv("SQL_DEBUG", "false").lower() == "true",
        )
        self.async_session_maker = async_sessionmaker(
            bind=self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    async def create_tables(self) -> None:
        """Create database tables if they don't exist."""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def get_session(self) -> AsyncGenerator[AsyncSession, None]:
        """Get an async database session."""
        async with self.async_session_maker() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()

    async def close(self) -> None:
        """Close the database engine."""
        await self.engine.dispose()


# Global database manager instance
_db_manager: DatabaseManager | None = None


def get_database_manager() -> DatabaseManager:
    """Get the global database manager instance."""
    global _db_manager
    if _db_manager is None:
        config = DatabaseConfig()
        _db_manager = DatabaseManager(config)
    return _db_manager


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for getting database sessions."""
    db_manager = get_database_manager()
    async for session in db_manager.get_session():
        yield session
