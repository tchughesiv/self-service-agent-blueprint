"""Shared helpers for session reclaim integration tests.

Used by session_reclaim_integration.py (on-demand reclaim) and
session_background_reclaim_integration.py (background reclaim).
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from shared_models import get_database_manager
from shared_models.models import RequestLog, RequestSession, RequestStatus
from sqlalchemy import select

# Stuck cutoff: AGENT_TIMEOUT (120) + SESSION_LOCK_STUCK_BUFFER_SECONDS (30) = 150s default.
# We use 200s ago to ensure time-based reclaim triggers.
STUCK_PROCESSING_AGE_SECONDS = 200


def fake_pod_name_for_test(test_suffix: str) -> str:
    """Return a unique fake pod name for reclaim tests (not in pod_heartbeats)."""
    return f"fake-dead-pod-{test_suffix}"


async def get_session_id_for_user(
    user_id: str, integration_type: str = "CLI"
) -> Optional[str]:
    """Get session_id from request_sessions for user_id + integration_type."""
    db_manager = get_database_manager()
    async with db_manager.get_session() as db:
        stmt = (
            select(RequestSession.session_id)
            .where(
                RequestSession.user_id == user_id,
                RequestSession.integration_type == integration_type,
                RequestSession.status == "ACTIVE",
            )
            .order_by(RequestSession.last_request_at.desc())
            .limit(1)
        )
        result = await db.execute(stmt)
        row = result.fetchone()
        return row[0] if row else None


async def insert_stuck_processing_row(
    session_id: str,
    user_id: str,
    *,
    content: str = "Reclaim test: stuck row.",
    fake_pod_name: str = "fake-dead-pod-reclaim-test",
) -> str:
    """Insert a stuck 'processing' RequestLog row. Returns request_id."""
    stuck_started = datetime.now(timezone.utc) - timedelta(
        seconds=STUCK_PROCESSING_AGE_SECONDS
    )
    created_at = stuck_started - timedelta(seconds=50)
    request_id = str(uuid.uuid4())

    request_log = RequestLog(
        request_id=request_id,
        session_id=session_id,
        request_type="message",
        request_content=content,
        normalized_request={
            "user_id": user_id,
            "integration_type": "CLI",
            "content": content,
            "request_type": "message",
            "integration_context": {},
        },
        status=RequestStatus.PROCESSING.value,
        processing_started_at=stuck_started,
        pod_name=fake_pod_name,
        created_at=created_at,
        updated_at=stuck_started,
    )

    db_manager = get_database_manager()
    async with db_manager.get_session() as db:
        db.add(request_log)
        await db.commit()

    return request_id
