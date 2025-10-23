#!/usr/bin/env python3
"""Database migration script for init containers."""

import asyncio
import logging
import os
import sys
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config

# Add the src directory to Python path and import shared_models modules
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
try:
    from shared_models.database import get_database_manager, get_db_config
except ImportError:
    # If running in container, try direct import
    from shared_models.database import get_database_manager, get_db_config  # noqa: F401

# Configure logging
logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Also enable Alembic logging
alembic_logger = logging.getLogger("alembic")
alembic_logger.setLevel(logging.DEBUG)


async def wait_for_database(max_retries: int = 150, retry_delay: int = 2) -> bool:
    """Wait for database to become available."""
    logger.info("Waiting for database to become available...")

    db_manager = get_database_manager()

    for attempt in range(max_retries):
        try:
            if await db_manager.health_check():
                logger.info("Database is available")
                return True
        except Exception as e:
            logger.debug(
                f"Database not ready (attempt {attempt + 1}/{max_retries}): {e}"
            )

        if attempt < max_retries - 1:
            await asyncio.sleep(retry_delay)

    logger.error("Database failed to become available")
    return False


def run_migrations() -> None:
    """Run Alembic migrations."""
    logger.info("Running database migrations...")

    # Get the directory containing this script
    script_dir = Path(__file__).parent.parent
    alembic_cfg_path = script_dir / "alembic.ini"

    logger.debug(f"Script directory: {script_dir}")
    logger.debug(f"Alembic config path: {alembic_cfg_path}")

    if not alembic_cfg_path.exists():
        logger.error(f"Alembic configuration not found: {alembic_cfg_path}")
        sys.exit(1)

    # Create Alembic config
    logger.debug("Creating Alembic configuration...")
    alembic_cfg = Config(str(alembic_cfg_path))

    # Override database URL from environment
    db_config = get_db_config()
    logger.debug(
        f"Database config: host={db_config.host}, port={db_config.port}, db={db_config.database}"
    )

    # Only log connection string in debug mode
    if os.getenv("SQL_DEBUG", "false").lower() == "true":
        logger.debug(f"Connection string: {db_config.sync_connection_string}")

    alembic_cfg.set_main_option("sqlalchemy.url", db_config.sync_connection_string)

    try:
        logger.info("Starting Alembic upgrade to head...")
        # Change to the shared-db directory so Alembic can find the alembic/ folder
        original_cwd = os.getcwd()
        os.chdir(script_dir)
        logger.debug(f"Changed working directory to: {script_dir}")

        # Run migrations
        command.upgrade(alembic_cfg, "head")
        logger.info("Database migrations completed successfully")

        # Setup LangGraph PostgreSQL checkpoint tables
        print("Setting up LangGraph PostgreSQL checkpoint tables...")
        logger.info("Setting up LangGraph PostgreSQL checkpoint tables...")
        try:
            from langgraph.checkpoint.postgres import PostgresSaver

            # Create a connection for PostgresSaver setup
            conn = psycopg.connect(
                db_config.sync_connection_string.replace(
                    "postgresql+psycopg://", "postgresql://"
                ),
                row_factory=psycopg.rows.dict_row,
                autocommit=True,
                connect_timeout=60,  # 60 second timeout for migration job
            )

            try:
                # Setup PostgresSaver with extended timeout
                print("Running PostgresSaver.setup()...")
                logger.info("Running PostgresSaver.setup()...")
                postgres_saver = PostgresSaver(conn)
                postgres_saver.setup()
                print(
                    "✅ LangGraph PostgreSQL checkpoint tables setup completed successfully"
                )
                logger.info(
                    "✅ LangGraph PostgreSQL checkpoint tables setup completed successfully"
                )
            except psycopg.Error as db_error:
                logger.error(f"Database error during PostgresSaver setup: {db_error}")
                logger.error(f"Error code: {getattr(db_error, 'pgcode', 'unknown')}")
                raise
            except Exception as setup_error:
                logger.error(
                    f"Unexpected error during PostgresSaver setup: {setup_error}"
                )
                logger.error(f"Exception type: {type(setup_error).__name__}")
                raise
            finally:
                try:
                    conn.close()
                    logger.debug("PostgresSaver connection closed successfully")
                except Exception as close_error:
                    logger.warning(
                        f"Error closing PostgresSaver connection: {close_error}"
                    )

        except Exception as e:
            logger.error(f"Failed to setup LangGraph checkpoint tables: {e}")
            logger.error(f"Exception type: {type(e).__name__}")
            logger.exception("Full PostgresSaver setup error traceback:")
            # Don't fail the migration job - this is not critical for basic functionality
            logger.warning("Continuing without LangGraph checkpoint setup...")

        # Restore original working directory
        os.chdir(original_cwd)
    except Exception as e:
        logger.error(f"Migration failed: {e}")
        logger.exception("Full migration error traceback:")
        sys.exit(1)


async def main() -> None:
    """Main migration function."""
    logger.info("Starting database migration process")

    try:
        # Wait for database to be available
        logger.debug("Checking database availability...")
        if not await wait_for_database():
            logger.error("Database is not available, exiting")
            sys.exit(1)

        # Run migrations
        logger.debug("Database is ready, starting migrations...")
        run_migrations()

        # Close database connections
        logger.debug("Closing database connections...")
        db_manager = get_database_manager()
        await db_manager.close()

        logger.info("Migration process completed successfully")
    except Exception as e:
        logger.error(f"Migration process failed: {e}")
        logger.exception("Full process error traceback:")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
