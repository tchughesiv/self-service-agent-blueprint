"""
PostgreSQL checkpoint implementation for LangGraph.

This module provides PostgreSQL-based checkpointing for LangGraph state machines.
The LangGraph tables are set up by the database migration job, so this module
just creates PostgresSaver instances with proper connection management.
"""

import logging
from typing import Optional

from langgraph.checkpoint.postgres import PostgresSaver
from shared_models.database import get_database_manager

logger = logging.getLogger(__name__)

# Global checkpointer instance for connection reuse
_checkpointer: Optional[PostgresSaver] = None


def get_postgres_checkpointer() -> PostgresSaver:
    """Get a PostgresSaver instance with proper connection management using shared configuration.

    Uses a singleton pattern to reuse the same checkpointer instance across the application,
    which improves performance by reusing the underlying database connection pool.
    """
    global _checkpointer

    if _checkpointer is None:
        # LangGraph tables should already be set up by the database migration job
        # Get a connection from the pool for the checkpointer
        db_manager = get_database_manager()
        conn = db_manager.get_sync_connection()
        _checkpointer = PostgresSaver(conn)
        logger.debug(
            "Created PostgresSaver with shared configuration and connection pooling"
        )
    else:
        logger.debug("Reusing existing PostgresSaver instance")

    return _checkpointer


def close_postgres_checkpointer() -> None:
    """Close the PostgresSaver instance and clean up resources."""
    global _checkpointer

    if _checkpointer is not None:
        try:
            # PostgresSaver doesn't have an explicit close method, but we can clear our reference
            # The underlying connection will be managed by the DatabaseManager
            _checkpointer = None
            logger.debug("PostgresSaver instance cleared")
        except Exception as e:
            logger.warning(f"Error closing PostgresSaver: {e}")
    else:
        logger.debug("No PostgresSaver instance to close")
