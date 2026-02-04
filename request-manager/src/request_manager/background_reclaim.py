"""Background task: periodically reclaim stuck processing requests across all sessions."""

import asyncio

from shared_models import configure_logging, get_database_manager

from .session_config import BACKGROUND_RECLAIM_INTERVAL_SECONDS
from .session_orchestrator import reclaim_stuck_processing_global

logger = configure_logging("request-manager")


async def run_background_reclaim_loop() -> None:
    """Background task: periodically scan for stuck processing and requeue."""
    logger.info(
        "Starting background reclaim task",
        interval_seconds=BACKGROUND_RECLAIM_INTERVAL_SECONDS,
    )

    while True:
        try:
            await asyncio.sleep(BACKGROUND_RECLAIM_INTERVAL_SECONDS)

            db_manager = get_database_manager()
            async with db_manager.get_session() as db:
                count = await reclaim_stuck_processing_global(db)
                if count > 0:
                    logger.info(
                        "Background reclaim completed",
                        reclaimed_count=count,
                    )

        except asyncio.CancelledError:
            logger.info("Background reclaim task cancelled")
            break
        except Exception as e:
            logger.error(
                "Background reclaim failed",
                error=str(e),
            )
            await asyncio.sleep(60)
