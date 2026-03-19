"""Background outbox publisher for Step 0.25.

Polls event_outbox for status='pending', POSTs to broker with FIFO ordering
(thread_order_key, created_at), marks published on success.
"""

import asyncio
import os
import time
from typing import Any

from shared_models import (
    SOURCE_SERVICE_INTEGRATION_DISPATCHER,
    CloudEventSender,
    EventTypes,
    configure_logging,
    get_database_manager,
    mark_outbox_failed,
    mark_outbox_published,
)
from shared_models.models import EventOutbox
from sqlalchemy import select

from .outbox_metrics import (
    record_batch_duration,
    record_failed,
    record_published,
)

logger = configure_logging("integration-dispatcher")

POLL_INTERVAL_SEC = float(os.getenv("OUTBOX_POLL_INTERVAL_SEC", "5.0"))
MAX_RETRIES = int(os.getenv("OUTBOX_MAX_RETRIES", "20"))
BATCH_SIZE = int(os.getenv("OUTBOX_BATCH_SIZE", "50"))


async def _publish_pending_batch() -> int:
    """Process one batch of pending outbox rows. Returns count published."""
    broker_url = os.getenv("BROKER_URL")
    if not broker_url:
        return 0

    start = time.monotonic()
    db_manager = get_database_manager()
    sender = CloudEventSender(broker_url, "integration-dispatcher")
    published = 0
    failed = 0

    async with db_manager.get_session() as db:
        # ORDER BY thread_order_key NULLS LAST, created_at for FIFO per thread
        result = await db.execute(
            select(EventOutbox)
            .where(
                EventOutbox.source_service == SOURCE_SERVICE_INTEGRATION_DISPATCHER,
                EventOutbox.event_type == EventTypes.REQUEST_CREATED,
                EventOutbox.status == "pending",
                EventOutbox.retry_count < MAX_RETRIES,
            )
            .order_by(
                EventOutbox.thread_order_key.asc().nulls_last(),
                EventOutbox.created_at.asc(),
            )
            .limit(BATCH_SIZE)
        )
        rows = result.scalars().all()

    for row in rows:
        payload: dict[str, Any] = row.payload if isinstance(row.payload, dict) else {}
        try:
            success = await sender.send_request_event(
                request_data=payload,
                request_id=payload.get("request_id"),
                user_id=payload.get("user_id"),
                session_id=payload.get("session_id"),
                max_retries=3,  # Publisher can retry
            )
            if success:
                async with db_manager.get_session() as db:
                    await mark_outbox_published(db, int(row.id))
                published += 1
                logger.debug(
                    "Outbox row published",
                    outbox_id=row.id,
                    idempotency_key=row.idempotency_key,
                )
            else:
                failed += 1
                async with db_manager.get_session() as db:
                    await mark_outbox_failed(
                        db,
                        int(row.id),
                        "Broker returned failure",
                        max_retries=MAX_RETRIES,
                    )
        except Exception as e:
            failed += 1
            logger.warning(
                "Outbox publish failed",
                outbox_id=row.id,
                idempotency_key=row.idempotency_key,
                error=str(e),
            )
            try:
                async with db_manager.get_session() as db:
                    await mark_outbox_failed(
                        db, int(row.id), str(e), max_retries=MAX_RETRIES
                    )
            except Exception:
                pass

    record_published(published)
    record_failed(failed)
    record_batch_duration(time.monotonic() - start)
    return published


async def run_outbox_publisher() -> None:
    """Background loop: poll outbox, publish pending rows."""
    logger.info(
        "Outbox publisher started",
        poll_interval_sec=POLL_INTERVAL_SEC,
        batch_size=BATCH_SIZE,
    )
    while True:
        try:
            n = await _publish_pending_batch()
            if n > 0:
                logger.debug("Outbox publisher published batch", count=n)
        except Exception as e:
            logger.error("Outbox publisher error", error=str(e), exc_info=True)
        await asyncio.sleep(POLL_INTERVAL_SEC)
