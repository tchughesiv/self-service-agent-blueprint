"""Session request orchestration: lock, reclaim, dequeue, process one.

Two-phase flow for durability + FIFO ordering:
  Phase 1 (durable accept): acquire lock → insert RequestLog → register future → release
  Phase 2 (process):        acquire lock → reclaim stuck → dequeue one → release →
                          process → loop until we process our own request.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Optional, cast

from shared_models import configure_logging, get_database_manager
from shared_models.models import NormalizedRequest, RequestLog, RequestStatus
from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from .communication_strategy import (
    _response_futures_registry,
    get_pod_name,
    register_response_future,
)
from .exceptions import SessionLockTimeoutError
from .session_config import (
    AGENT_TIMEOUT,
    POD_HEARTBEAT_GRACE_SECONDS,
    RECLAIM_ACTION,
    SESSION_LOCK_STUCK_BUFFER_SECONDS,
    SESSION_LOCK_WAIT_TIMEOUT,
)
from .session_lock import with_session_lock

logger = configure_logging("request-manager")


def _get_stuck_cutoff() -> datetime:
    """Time-based cutoff: processing_started_at older than this is stuck."""
    return datetime.now(timezone.utc) - timedelta(
        seconds=AGENT_TIMEOUT + SESSION_LOCK_STUCK_BUFFER_SECONDS
    )


def reconstruct_normalized_request(
    request_log: Any,
) -> NormalizedRequest:
    """Reconstruct NormalizedRequest from RequestLog row (e.g. for reclaimed requests).

    Accepts RequestLog or any object with request_id, session_id, request_content,
    normalized_request, created_at attributes (for testing with mocks).
    """
    from shared_models.models import IntegrationType

    nr: dict[str, Any] = request_log.normalized_request or {}
    it = nr.get("integration_type", "WEB")
    if isinstance(it, str):
        it = IntegrationType(it.upper() if it else "WEB")
    user_id = nr.get("user_id", "")
    if not user_id:
        user_id = "unknown"  # Fallback for orphaned/reclaimed rows
    content: str = str(request_log.request_content or "")
    created: datetime = cast(
        datetime,
        request_log.created_at or datetime.now(timezone.utc),
    )
    return NormalizedRequest(
        request_id=str(request_log.request_id),
        session_id=str(request_log.session_id),
        user_id=user_id,
        integration_type=it,
        request_type=nr.get("request_type", "unknown"),
        content=content,
        integration_context=nr.get("integration_context", {}),
        user_context=nr.get("user_context", {}),
        target_agent_id=nr.get("target_agent_id"),
        requires_routing=nr.get("requires_routing", True),
        created_at=created,
    )


def _stuck_where_clause(stuck_cutoff: datetime, grace_cutoff: datetime) -> Any:
    """Build SQLAlchemy WHERE clause for stuck processing rows.

    Stuck = time-based (past cutoff) OR pod heartbeat (pod not in recent check-ins).
    """
    from shared_models.models import PodHeartbeat

    coalesced = func.coalesce(
        RequestLog.processing_started_at,
        RequestLog.updated_at,
        RequestLog.created_at,
    )
    stuck_by_time = coalesced < stuck_cutoff
    recent_pods_subq = select(PodHeartbeat.pod_name).where(
        PodHeartbeat.last_check_in_at >= grace_cutoff
    )
    stuck_by_heartbeat = and_(
        RequestLog.pod_name.isnot(None),
        ~RequestLog.pod_name.in_(recent_pods_subq),
    )
    return or_(stuck_by_time, stuck_by_heartbeat)


async def _handle_lock_timeout(
    session_id: str,
    our_request_id: str,
    db_manager: Any,
) -> None:
    """On lock timeout: mark request failed (if row exists), resolve future, re-raise."""
    async with db_manager.get_session() as update_db:
        await update_db.execute(
            update(RequestLog)
            .where(RequestLog.request_id == our_request_id)
            .values(
                status=RequestStatus.FAILED.value,
                response_content="Session lock timeout - too many concurrent requests",
            )
        )
        await update_db.commit()
    if our_request_id in _response_futures_registry:
        future = _response_futures_registry[our_request_id]
        if not future.done():
            err_data: dict[str, Any] = {
                "request_id": our_request_id,
                "session_id": session_id,
                "content": "Session lock timeout - too many concurrent requests",
                "agent_id": "system",
                "metadata": {},
                "processing_time_ms": None,
            }
            future.set_result(err_data)
    raise SessionLockTimeoutError(f"Session lock timeout for {our_request_id}")


async def reclaim_stuck_processing(session_id: str, db: AsyncSession) -> int:
    """Reset stuck 'processing' requests to 'pending' (or mark 'failed' if RECLAIM_ACTION=fail).

    Stuck = time-based (past cutoff) OR pod heartbeat (pod not checked in).
    Returns number of rows updated.
    """
    stuck_cutoff = _get_stuck_cutoff()
    grace_cutoff = datetime.now(timezone.utc) - timedelta(
        seconds=POD_HEARTBEAT_GRACE_SECONDS
    )
    new_status = (
        RequestStatus.PENDING.value
        if RECLAIM_ACTION == "requeue"
        else RequestStatus.FAILED.value
    )

    stmt = (
        update(RequestLog)
        .where(
            RequestLog.session_id == session_id,
            RequestLog.status == RequestStatus.PROCESSING.value,
            _stuck_where_clause(stuck_cutoff, grace_cutoff),
        )
        .values(
            status=new_status,
            processing_started_at=None,
            pod_name=None,
        )
        .returning(RequestLog.request_id)
    )
    result = await db.execute(stmt)
    rows = result.fetchall()
    count = len(rows)
    for row in rows:
        logger.info(
            "Reclaimed stuck processing request",
            request_id=row[0],
            session_id=session_id,
            new_status=new_status,
        )
    if count > 0:
        await db.commit()
        try:
            from .session_metrics import record_reclaim_on_demand

            record_reclaim_on_demand(count)
        except Exception:  # noqa: BLE001
            pass
    return count


async def wait_for_turn_and_process_one(
    session_id: str,
    our_request_id: str,
    normalized_request: NormalizedRequest,
    db: AsyncSession,
    strategy_send_request: Callable[[NormalizedRequest], Any],
    strategy_wait_for_response: Callable[[str, int, Optional[AsyncSession]], Any],
    timeout: int = AGENT_TIMEOUT,
) -> Dict[str, Any]:
    """Two-phase: durable accept (insert + register) then reclaim/dequeue/process loop.

    Phase 1 ensures RequestLog exists before releasing lock (durability); registering
    the future before release lets the poller resolve if another pod dequeues us.
    Phase 2 reclaims, dequeues, and processes until we handle our own request.

    Args:
        strategy_send_request: strategy.send_request
        strategy_wait_for_response: strategy.wait_for_response

    Returns:
        Response dict for our request.
    """
    pod_name = get_pod_name()
    db_manager = get_database_manager()

    async def _phase1_insert_and_register(lock_db: AsyncSession) -> None:
        """Phase 1: insert RequestLog (if needed) and register future before release."""
        from .database_utils import create_request_log_entry_unified

        check_stmt = select(RequestLog).where(RequestLog.request_id == our_request_id)
        check_result = await lock_db.execute(check_stmt)
        if check_result.scalar_one_or_none() is None:
            async with db_manager.get_session() as insert_db:
                await create_request_log_entry_unified(
                    request_id=normalized_request.request_id,
                    session_id=normalized_request.session_id,
                    user_id=normalized_request.user_id,
                    content=normalized_request.content,
                    request_type=normalized_request.request_type,
                    integration_type=normalized_request.integration_type,
                    integration_context=normalized_request.integration_context,
                    db=insert_db,
                )
        # Register future before release so poller can resolve if another pod processes us
        register_response_future(our_request_id)

    async def _critical_section(lock_db: AsyncSession) -> Optional[RequestLog]:
        """Phase 2: reclaim stuck, dequeue oldest pending. Insert done in Phase 1.

        Use db (request-scoped) for reclaim/dequeue: commit() on lock_db can cause
        SQLAlchemy to affect the connection state, leading to 'Session lock release
        returned false' and stuck locks. The advisory lock on lock_db still provides
        mutual exclusion; db is used only for the actual DB writes.
        """
        await reclaim_stuck_processing(session_id, db)
        return await dequeue_oldest_pending(session_id, db, pod_name)

    # Phase 1: durable accept (insert + register) — row exists before we may wait
    try:
        await with_session_lock(
            session_id,
            db_manager,
            _phase1_insert_and_register,
            timeout_seconds=SESSION_LOCK_WAIT_TIMEOUT,
            request_id=our_request_id,
        )
    except SessionLockTimeoutError:
        # Phase 1 timeout: never inserted, never registered; update no-op, just raise
        await _handle_lock_timeout(session_id, our_request_id, db_manager)

    # Phase 2: reclaim, dequeue, process loop
    while True:
        try:
            dequeued = await with_session_lock(
                session_id,
                db_manager,
                _critical_section,
                timeout_seconds=SESSION_LOCK_WAIT_TIMEOUT,
                request_id=our_request_id,
            )
        except SessionLockTimeoutError:
            await _handle_lock_timeout(session_id, our_request_id, db_manager)

        # Lock released; continue with process (no lock during agent wait)
        if not dequeued:
            # No pending - await our response (someone else may have processed or will process)
            result = await strategy_wait_for_response(our_request_id, timeout, db)
            return cast(Dict[str, Any], result)

        # Process the dequeued request
        normalized = reconstruct_normalized_request(dequeued)
        success = await strategy_send_request(normalized)
        if not success:
            await db.execute(
                update(RequestLog)
                .where(RequestLog.request_id == dequeued.request_id)
                .values(
                    status=RequestStatus.FAILED.value,
                    response_content="Failed to send request to agent",
                )
            )
            await db.commit()
            if str(dequeued.request_id) == our_request_id:
                raise Exception("Failed to send request to agent")
            continue

        try:
            response = await strategy_wait_for_response(
                str(dequeued.request_id), timeout, db
            )
        except Exception as e:
            err_msg = str(e)
            await db.execute(
                update(RequestLog)
                .where(RequestLog.request_id == dequeued.request_id)
                .values(
                    status=RequestStatus.FAILED.value,
                    response_content=err_msg,
                )
            )
            await db.commit()
            if str(dequeued.request_id) == our_request_id:
                if our_request_id in _response_futures_registry:
                    future = _response_futures_registry[our_request_id]
                    if not future.done():
                        future.set_result(
                            {
                                "request_id": our_request_id,
                                "session_id": session_id,
                                "content": err_msg,
                                "agent_id": "system",
                                "metadata": {},
                                "processing_time_ms": None,
                            }
                        )
                raise
            continue

        if str(dequeued.request_id) == our_request_id:
            return cast(Dict[str, Any], response)
        # Processed someone else's request - loop to process ours


async def dequeue_oldest_pending(
    session_id: str, db: AsyncSession, pod_name: Optional[str]
) -> Optional[RequestLog]:
    """Dequeue oldest pending request for session; set status=processing, pod_name, processing_started_at."""
    now = datetime.now(timezone.utc)

    stmt = (
        select(RequestLog)
        .where(
            RequestLog.session_id == session_id,
            RequestLog.status == RequestStatus.PENDING.value,
        )
        .order_by(RequestLog.created_at.asc(), RequestLog.id.asc())
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    result = await db.execute(stmt)
    row = result.scalar_one_or_none()
    if not row:
        return None

    await db.execute(
        update(RequestLog)
        .where(RequestLog.request_id == row.request_id)
        .values(
            status=RequestStatus.PROCESSING.value,
            processing_started_at=now,
            pod_name=pod_name,
        )
    )
    await db.commit()

    # Re-fetch the updated row
    refresh_stmt = select(RequestLog).where(RequestLog.request_id == row.request_id)
    refresh_result = await db.execute(refresh_stmt)
    return refresh_result.scalar_one_or_none()


async def reclaim_stuck_processing_global(db: AsyncSession) -> int:
    """Background reclaim: scan all sessions for stuck 'processing' and reset to pending.

    Same stuck criteria as reclaim_stuck_processing (time-based + heartbeat).
    Uses single SQL UPDATE for scalability (no load-all-then-filter).
    Returns total number of rows updated across all sessions.
    """
    stuck_cutoff = _get_stuck_cutoff()
    grace_cutoff = datetime.now(timezone.utc) - timedelta(
        seconds=POD_HEARTBEAT_GRACE_SECONDS
    )
    new_status = (
        RequestStatus.PENDING.value
        if RECLAIM_ACTION == "requeue"
        else RequestStatus.FAILED.value
    )

    stmt = (
        update(RequestLog)
        .where(
            RequestLog.status == RequestStatus.PROCESSING.value,
            _stuck_where_clause(stuck_cutoff, grace_cutoff),
        )
        .values(
            status=new_status,
            processing_started_at=None,
            pod_name=None,
        )
        .returning(RequestLog.request_id, RequestLog.session_id)
    )
    result = await db.execute(stmt)
    rows = result.fetchall()
    count = len(rows)
    for row in rows:
        logger.info(
            "Background reclaim: reset stuck processing",
            request_id=row[0],
            session_id=row[1],
            new_status=new_status,
        )
    if count > 0:
        await db.commit()
        try:
            from .session_metrics import record_reclaim_background

            record_reclaim_background(count)
        except Exception:  # noqa: BLE001
            pass
    return count
