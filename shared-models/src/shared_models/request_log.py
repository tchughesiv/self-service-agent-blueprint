"""RequestLog ordering utilities for session FIFO.

Used by agent-service to enforce RequestLog as source of truth for processing order
Before processing, the agent checks
whether any earlier request (by created_at) is still pending or processing.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import RequestLog, RequestStatus


async def has_earlier_pending_or_processing(
    session_id: str,
    created_at: datetime,
    db: AsyncSession,
) -> bool:
    """Check if any earlier request (by created_at) is still pending or processing.

    Returns True if there exists a row in request_logs for this session with
    status in ('pending', 'processing') and created_at < the given created_at.
    Used by the agent before processing to yield (503) when an earlier request
    hasn't completed yet.
    """
    stmt = (
        select(1)
        .select_from(RequestLog)
        .where(
            RequestLog.session_id == session_id,
            RequestLog.status.in_(
                [RequestStatus.PENDING.value, RequestStatus.PROCESSING.value]
            ),
            RequestLog.created_at < created_at,
        )
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none() is not None


async def get_request_created_at(
    request_id: str,
    db: AsyncSession,
) -> Optional[datetime]:
    """Fetch created_at from RequestLog by request_id.

    Returns None if the row doesn't exist. Used when the event payload
    doesn't include created_at (request-manager inserts before sending).
    """
    stmt = select(RequestLog.created_at).where(RequestLog.request_id == request_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()
