"""Pod heartbeat: periodic UPSERT to pod_heartbeats for reclaim liveness."""

import asyncio
from datetime import datetime, timezone

from shared_models import configure_logging
from shared_models.models import PodHeartbeat
from sqlalchemy.dialects.postgresql import insert

from .communication_strategy import get_pod_name
from .session_config import POD_HEARTBEAT_INTERVAL_SECONDS

logger = configure_logging("request-manager")


async def run_pod_heartbeat_loop() -> None:
    """Background task: periodically update pod_heartbeats for this pod."""
    pod_name = get_pod_name()
    if not pod_name:
        logger.warning("Pod heartbeat skipped: no HOSTNAME/POD_NAME")
        return

    logger.info(
        "Starting pod heartbeat task",
        pod_name=pod_name,
        interval_seconds=POD_HEARTBEAT_INTERVAL_SECONDS,
    )

    while True:
        try:
            await asyncio.sleep(POD_HEARTBEAT_INTERVAL_SECONDS)

            from shared_models import get_database_manager

            db_manager = get_database_manager()
            async with db_manager.get_session() as db:
                now = datetime.now(timezone.utc)
                stmt = insert(PodHeartbeat).values(
                    pod_name=pod_name,
                    last_check_in_at=now,
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=["pod_name"],
                    set_={"last_check_in_at": now},
                )
                await db.execute(stmt)
                await db.commit()
                logger.debug("Pod heartbeat written", pod_name=pod_name)

        except asyncio.CancelledError:
            logger.info("Pod heartbeat task cancelled", pod_name=pod_name)
            break
        except Exception as e:
            logger.error(
                "Pod heartbeat failed",
                pod_name=pod_name,
                error=str(e),
            )
            await asyncio.sleep(5)
