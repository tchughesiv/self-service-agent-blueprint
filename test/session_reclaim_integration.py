#!/usr/bin/env python3
"""
Session reclaim integration test.

Validates on-demand reclaim of stuck 'processing' requests (time-based and heartbeat).
Inserts a stuck row, sends a new request for the same session, and asserts the new request
completes (proving reclaim ran and unblocked the session).

Designed to run via kubectl/oc exec inside the request-manager pod (has DB env vars).

Usage:
  kubectl exec deploy/self-service-agent-request-manager -n test -- \
    env REQUEST_MANAGER_URL=http://self-service-agent-request-manager:80 \
    /app/.venv/bin/python /app/test/session_reclaim_integration.py
"""

import argparse
import asyncio
import os
import sys
import uuid

from session_reclaim_helpers import (
    fake_pod_name_for_test,
    get_session_id_for_user,
    insert_stuck_processing_row,
)

# shared_clients and shared_models are in container PYTHONPATH
from shared_clients import RequestManagerClient
from shared_models import get_database_manager
from shared_models.models import RequestLog, RequestStatus
from sqlalchemy import select

REQUEST_MANAGER_URL = os.environ.get("REQUEST_MANAGER_URL", "http://localhost:8080")
MESSAGE_TIMEOUT = 90  # Seconds per request (agent may be slow in CI)


async def run_reclaim_test(request_manager_url: str) -> bool:
    """
    Send request 1 to create session, insert stuck row, send request 2.
    Assert request 2 completes (reclaim ran and unblocked session).
    """
    user_id = str(uuid.uuid4())
    client = RequestManagerClient(
        request_manager_url=request_manager_url,
        user_id=user_id,
        timeout=MESSAGE_TIMEOUT,
    )

    # 1. Send first request to create session and establish it
    try:
        result = await client.send_request(
            content="Setup: create session for reclaim test.",
            integration_type="CLI",
            request_type="message",
            endpoint="generic",
        )
    except Exception as e:
        print(f"FAIL: First request failed: {e}", file=sys.stderr)
        await client.close()
        return False

    if isinstance(result, dict) and result.get("error"):
        print(f"FAIL: First request returned error: {result['error']}", file=sys.stderr)
        await client.close()
        return False

    # 2. Get session_id from DB
    session_id = await get_session_id_for_user(user_id)
    if not session_id:
        print("FAIL: Could not find session after first request", file=sys.stderr)
        await client.close()
        return False

    # 3. Insert stuck processing row
    try:
        stuck_request_id = await insert_stuck_processing_row(
            session_id,
            user_id,
            content="Reclaim test: this request was inserted as stuck and should be reclaimed.",
            fake_pod_name=fake_pod_name_for_test("reclaim-test"),
        )
    except Exception as e:
        print(f"FAIL: Failed to insert stuck row: {e}", file=sys.stderr)
        await client.close()
        return False

    # 4. Send second request – should trigger reclaim, then process (stuck row first, then ours)
    try:
        result2 = await client.send_request(
            content="Reclaim test: this request should complete after reclaim runs.",
            integration_type="CLI",
            request_type="message",
            endpoint="generic",
        )
    except Exception as e:
        print(f"FAIL: Second request (post-reclaim) failed: {e}", file=sys.stderr)
        await client.close()
        return False

    await client.close()

    if isinstance(result2, dict) and result2.get("error"):
        print(
            f"FAIL: Second request returned error: {result2['error']}", file=sys.stderr
        )
        return False

    # 5. Optionally verify stuck row was reclaimed (status no longer processing)
    db_manager = get_database_manager()
    async with db_manager.get_session() as db:
        stmt = select(RequestLog.status).where(
            RequestLog.request_id == stuck_request_id
        )
        r = await db.execute(stmt)
        row = r.fetchone()
        status_after = row[0] if row else None

    if status_after == RequestStatus.PROCESSING.value:
        print("FAIL: Stuck row still in processing after reclaim", file=sys.stderr)
        return False

    print(
        "PASS: Reclaim integration test – stuck row reclaimed, new request completed "
        f"(stuck request status={status_after})"
    )
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Session reclaim integration test (on-demand reclaim of stuck processing)"
    )
    parser.add_argument(
        "--request-manager-url",
        default=REQUEST_MANAGER_URL,
        help="Request Manager URL",
    )
    args = parser.parse_args()

    success = asyncio.run(
        run_reclaim_test(request_manager_url=args.request_manager_url)
    )
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
