#!/usr/bin/env python3
"""
Background reclaim integration test.

Validates that the background reclaim task periodically reclaims stuck 'processing'
requests even when no new request arrives for that session.
Inserts a stuck row, does NOT send a follow-up request, polls until the row is
reclaimed (or times out).

Designed to run via kubectl/oc exec inside the request-manager pod (has DB env vars).
Background task runs every BACKGROUND_RECLAIM_INTERVAL_SECONDS (default 45).

Usage:
  kubectl exec deploy/self-service-agent-request-manager -n test -- \
    env REQUEST_MANAGER_URL=http://self-service-agent-request-manager:80 \
    /app/.venv/bin/python /app/test/session_background_reclaim_integration.py
"""

import argparse
import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

# shared_clients and shared_models are in container PYTHONPATH
from shared_clients import RequestManagerClient
from shared_models import get_database_manager
from shared_models.models import RequestLog, RequestSession, RequestStatus
from sqlalchemy import select

REQUEST_MANAGER_URL = os.environ.get("REQUEST_MANAGER_URL", "http://localhost:8080")
MESSAGE_TIMEOUT = 90  # Seconds per request (agent may be slow in CI)

# BACKGROUND_RECLAIM_INTERVAL_SECONDS defaults to 45; task sleeps that long before first run.
# Poll every 5s for up to 65s to catch the first reclaim cycle.
POLL_INTERVAL_SECONDS = 5
POLL_TIMEOUT_SECONDS = 65

STUCK_PROCESSING_AGE_SECONDS = 200
FAKE_POD_NAME = "fake-dead-pod-background-reclaim-test"


async def get_session_id_for_user(
    user_id: str, integration_type: str = "CLI"
) -> str | None:
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
) -> str:
    """Insert a stuck 'processing' RequestLog row. Returns request_id."""
    stuck_started = datetime.now(timezone.utc) - timedelta(
        seconds=STUCK_PROCESSING_AGE_SECONDS
    )
    created_at = stuck_started - timedelta(seconds=50)
    request_id = str(uuid.uuid4())
    content = "Background reclaim test: stuck row, no follow-up request."

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
        pod_name=FAKE_POD_NAME,
        created_at=created_at,
        updated_at=stuck_started,
    )

    db_manager = get_database_manager()
    async with db_manager.get_session() as db:
        db.add(request_log)
        await db.commit()

    return request_id


async def get_request_status(request_id: str) -> str | None:
    """Get current status of a RequestLog row."""
    db_manager = get_database_manager()
    async with db_manager.get_session() as db:
        stmt = select(RequestLog.status).where(RequestLog.request_id == request_id)
        result = await db.execute(stmt)
        row = result.fetchone()
        return row[0] if row else None


async def run_background_reclaim_test(request_manager_url: str) -> bool:
    """
    Create session, insert stuck row, do NOT send follow-up request.
    Poll until background reclaim resets the row, or timeout.
    """
    user_id = str(uuid.uuid4())
    client = RequestManagerClient(
        request_manager_url=request_manager_url,
        user_id=user_id,
        timeout=MESSAGE_TIMEOUT,
    )

    # 1. Send request to create session
    try:
        result = await client.send_request(
            content="Setup: create session for background reclaim test.",
            integration_type="CLI",
            request_type="message",
            endpoint="generic",
        )
    except Exception as e:
        print(f"FAIL: Setup request failed: {e}", file=sys.stderr)
        await client.close()
        return False

    if isinstance(result, dict) and result.get("error"):
        print(f"FAIL: Setup request returned error: {result['error']}", file=sys.stderr)
        await client.close()
        return False

    await client.close()

    # 2. Get session_id and insert stuck row
    session_id = await get_session_id_for_user(user_id)
    if not session_id:
        print("FAIL: Could not find session after setup request", file=sys.stderr)
        return False

    try:
        stuck_request_id = await insert_stuck_processing_row(session_id, user_id)
    except Exception as e:
        print(f"FAIL: Failed to insert stuck row: {e}", file=sys.stderr)
        return False

    # 3. Poll until background reclaim resets the row (no follow-up request)
    elapsed = 0
    while elapsed < POLL_TIMEOUT_SECONDS:
        status = await get_request_status(stuck_request_id)
        if status != RequestStatus.PROCESSING.value:
            print(
                f"PASS: Background reclaim test – stuck row reclaimed by background task "
                f"after ~{elapsed}s (status={status})"
            )
            return True
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
        elapsed += POLL_INTERVAL_SECONDS

    status = await get_request_status(stuck_request_id)
    print(
        f"FAIL: Stuck row still in processing after {POLL_TIMEOUT_SECONDS}s "
        f"(status={status})",
        file=sys.stderr,
    )
    return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Background reclaim integration test (periodic reclaim without new traffic)"
    )
    parser.add_argument(
        "--request-manager-url",
        default=REQUEST_MANAGER_URL,
        help="Request Manager URL",
    )
    args = parser.parse_args()

    success = asyncio.run(
        run_background_reclaim_test(request_manager_url=args.request_manager_url)
    )
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
