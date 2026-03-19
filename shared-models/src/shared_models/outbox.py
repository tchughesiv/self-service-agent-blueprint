"""Transactional outbox for durable event publishing.

Step 0.25: Store events in event_outbox before broker publish.
Background publisher polls pending rows, POSTs to broker, retries with backoff.

Schema (migration 001): event_outbox(id, source_service, event_type, idempotency_key,
thread_order_key, payload, status, last_error, retry_count, created_at)
"""

from typing import Any, Dict, Optional

from sqlalchemy import case, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .logging import configure_logging
from .models import EventOutbox

logger = configure_logging("shared-models")

SOURCE_SERVICE_INTEGRATION_DISPATCHER = "integration-dispatcher"


async def insert_outbox_event(
    db: AsyncSession,
    source_service: str,
    event_type: str,
    idempotency_key: str,
    payload: Dict[str, Any],
    *,
    thread_order_key: Optional[str] = None,
) -> Optional[int]:
    """Insert event into outbox. Returns row id on success, None on duplicate.

    Uses ON CONFLICT DO NOTHING on (source_service, event_type, idempotency_key)
    to handle race with duplicate webhooks.
    """
    stmt = (
        insert(EventOutbox)
        .values(
            source_service=source_service,
            event_type=event_type,
            idempotency_key=idempotency_key,
            thread_order_key=thread_order_key,
            payload=payload,
            status="pending",
            retry_count=0,
        )
        .on_conflict_do_nothing(
            index_elements=["source_service", "event_type", "idempotency_key"]
        )
        .returning(EventOutbox.id)
    )
    result = await db.execute(stmt)
    row = result.scalar_one_or_none()
    await db.commit()
    return int(row) if row is not None else None


async def mark_outbox_published(db: AsyncSession, outbox_id: int) -> None:
    """Mark outbox row as published."""
    await db.execute(
        update(EventOutbox)
        .where(EventOutbox.id == outbox_id)
        .values(status="published")
    )
    await db.commit()


async def mark_outbox_failed(
    db: AsyncSession,
    outbox_id: int,
    error_message: str,
    *,
    max_retries: int,
) -> None:
    """Update outbox row with failure for retry. Sets status='exhausted' when retries exceeded."""
    new_retry = EventOutbox.retry_count + 1
    status_val = case(
        (new_retry >= max_retries, "exhausted"),
        else_=EventOutbox.status,
    )
    await db.execute(
        update(EventOutbox)
        .where(EventOutbox.id == outbox_id)
        .values(
            last_error=error_message[:1000] if error_message else None,
            retry_count=new_retry,
            status=status_val,
        )
    )
    await db.commit()


async def reset_outbox_for_retry(db: AsyncSession, outbox_id: int) -> bool:
    """Reset an exhausted outbox row for reprocessing.

    Sets status='pending', retry_count=0, clears last_error.
    Only affects rows with status='exhausted'. Returns True if row was updated.
    """
    result = await db.execute(
        update(EventOutbox)
        .where(
            EventOutbox.id == outbox_id,
            EventOutbox.status == "exhausted",
        )
        .values(
            status="pending",
            retry_count=0,
            last_error=None,
        )
        .returning(EventOutbox.id)
    )
    updated_id = result.scalar_one_or_none()
    await db.commit()
    return updated_id is not None
