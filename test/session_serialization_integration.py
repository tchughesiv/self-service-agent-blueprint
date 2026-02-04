#!/usr/bin/env python3
"""
Session serialization integration test.

Validates one-per-session serialization (all concurrent requests complete,
no 503), concurrent request handling, cross-pod response delivery, and
response order by created_at. Messages are staggered per user so receive
order matches msg_idx, enabling the response-order assertion.

Run via kubectl exec; REQUEST_MANAGER_URL should point at the load-balanced
service (http://self-service-agent-request-manager:80) so with 2+ replicas,
cross-pod scenarios are exercised.

Usage:
  make test-session-serialization-integration NAMESPACE=test

  # Or directly:
  kubectl exec deploy/self-service-agent-request-manager -n test -- \
    env REQUEST_MANAGER_URL=http://self-service-agent-request-manager:80 \
    /app/.venv/bin/python /app/test/session_serialization_integration.py

  uv run python test/session_serialization_integration.py -n 2

Local dev (request-manager on localhost:8080):
- DB_STATEMENT_TIMEOUT >= SESSION_LOCK_WAIT_TIMEOUT * 1000 (ms)
- SESSION_LOCK_WAIT_TIMEOUT >= AGENT_TIMEOUT
- 15 concurrent requests (5 per user × 3 users) need ~30 DB connections: set DB_POOL_SIZE=8, DB_MAX_OVERFLOW=8
- Check request-manager logs for "Failed to create RequestLog", "Session lock timeout", "pool", "connection"
"""

import argparse
import asyncio
import os
import sys
import time
import uuid

# shared_clients is in container PYTHONPATH
from shared_clients import RequestManagerClient

REQUEST_MANAGER_URL = os.environ.get("REQUEST_MANAGER_URL", "http://localhost:8080")
CONCURRENT_REQUESTS = int(os.environ.get("CONCURRENT_REQUESTS", "5"))
NUM_USERS = int(os.environ.get("NUM_USERS", "3"))
MESSAGE_TIMEOUT = int(os.environ.get("MESSAGE_TIMEOUT", "120"))
DEFAULT_STAGGER_MS = 1200.0


async def send_one(
    client: RequestManagerClient, user_idx: int, msg_idx: int
) -> tuple[int, int, dict | str | None, Exception | None]:
    """Send a single request; return (user_idx, msg_idx, result_or_error, exception)."""
    content = f"Reply briefly: test message {msg_idx} for user {user_idx}."
    try:
        result = await client.send_request(
            content=content,
            integration_type="CLI",
            request_type="message",
            endpoint="generic",
        )
        return (user_idx, msg_idx, result, None)
    except Exception as e:
        return (user_idx, msg_idx, None, e)


async def run_concurrent_session_requests(
    request_manager_url: str,
    num_users: int = NUM_USERS,
    requests_per_user: int = CONCURRENT_REQUESTS,
    stagger_ms: float | None = None,
) -> bool:
    """
    Send concurrent requests across multiple users/sessions.

    Each user has their own session (no lock contention between users).
    requests_per_user requests share a session (serialized via lock).
    stagger_ms: delay between sends per user so receive order matches msg_idx.
    msg_idx 0 at 0ms, 1 at stagger_ms, 2 at 2*stagger_ms, etc.
    Default 1200ms to reduce reorder flakiness with 2+ replicas and cross-pod races.
    Override: --stagger-ms N, STAGGER_MS=N, or Make STAGGER_MS=N.
    """
    if stagger_ms is None:
        env_val = os.environ.get("STAGGER_MS")
        stagger_ms = float(env_val) if env_val else DEFAULT_STAGGER_MS
    total = num_users * requests_per_user
    tasks = []
    clients = []
    warmup_durations_ms: list[float] = []

    async def send_with_stagger(
        client: RequestManagerClient, user_idx: int, msg_idx: int, delay: float
    ) -> tuple[int, int, dict | str | None, Exception | None, float]:
        await asyncio.sleep(delay / 1000.0)  # msg_idx 0: delay=0 (yield only)
        req_start = time.perf_counter()
        out = await send_one(client, user_idx, msg_idx)
        duration_ms = (time.perf_counter() - req_start) * 1000
        return (*out, duration_ms)

    for u in range(num_users):
        uid = str(uuid.uuid4())
        client = RequestManagerClient(
            request_manager_url=request_manager_url,
            user_id=uid,
            timeout=MESSAGE_TIMEOUT,
        )
        clients.append(client)
        # Warmup: /health establishes connection without full agent round-trip (~ms vs 4+s)
        warmup_start = time.perf_counter()
        r = await client.client.get(f"{client.request_manager_url}/health")
        r.raise_for_status()
        warmup_durations_ms.append((time.perf_counter() - warmup_start) * 1000.0)
        for r in range(requests_per_user):
            tasks.append(send_with_stagger(client, u, r, r * stagger_ms))

    total_start = time.perf_counter()
    results = await asyncio.gather(*tasks, return_exceptions=False)
    total_duration_ms = (time.perf_counter() - total_start) * 1000
    for c in clients:
        await c.close()

    successes = 0
    errors = []
    for user_idx, msg_idx, result_or_err, exc, duration_ms in results:
        label = f"user{user_idx}/msg{msg_idx}"
        if exc is not None:
            msg = str(exc) or f"{type(exc).__name__}"
            # Add 503 response body for diagnostics (session lock timeout, etc.)
            if hasattr(exc, "response") and exc.response is not None:
                try:
                    body = exc.response.json()
                    detail = body.get("detail", "")
                    if detail:
                        msg = f"{msg} (detail: {detail})"
                except Exception:
                    pass
            errors.append((label, msg))
            continue
        if result_or_err is None:
            errors.append((label, "None result"))
            continue
        # Check for error response (e.g. 503 in body)
        if isinstance(result_or_err, dict) and "error" in result_or_err:
            err_val = result_or_err.get("error")
            msg = (
                str(err_val)
                if err_val is not None
                else result_or_err.get("detail") or str(result_or_err)
            )
            errors.append((label, msg))
            continue
        # Check for detail (FastAPI style)
        if isinstance(result_or_err, dict) and "detail" in result_or_err:
            errors.append((label, str(result_or_err.get("detail", result_or_err))))
            continue
        # Valid response: has content or response key
        if isinstance(result_or_err, dict) and (
            "content" in result_or_err or "response" in result_or_err
        ):
            successes += 1
        else:
            successes += 1  # Accept any non-error dict

    # Timing: warmup, per-request, and total (always printed)
    print("\n--- Timing ---")
    warmup_total_ms = sum(warmup_durations_ms)
    if warmup_durations_ms:
        warmup_str = ", ".join(
            f"user{u}: {d:.0f}ms" for u, d in enumerate(warmup_durations_ms)
        )
        print(
            f"  warmup ({num_users} users): {warmup_str} (total {warmup_total_ms:.0f}ms)"
        )
    for user_idx, msg_idx, result_or_err, exc, duration_ms in sorted(
        results, key=lambda r: (r[0], r[1])
    ):
        status = "ok" if exc is None else "fail"
        print(f"  user{user_idx}/msg{msg_idx}: {duration_ms:.0f}ms ({status})")
    print(f"  total (concurrent phase): {total_duration_ms:.0f}ms")
    if warmup_durations_ms:
        print(
            f"  wall clock (incl. warmup): {warmup_total_ms + total_duration_ms:.0f}ms"
        )
    print()

    if errors:
        print(f"FAIL: {len(errors)} request(s) failed:", file=sys.stderr)
        for label, msg in errors:
            print(f"  {label}: {msg}", file=sys.stderr)
        exc_list = [e for _, _, _, e, _ in results if e is not None]
        if (
            requests_per_user > 1
            and exc_list
            and all(
                "503" in str(e) or "Service Unavailable" in str(e) for e in exc_list
            )
        ):
            hint = (
                "\nHint: Concurrent 503s may be caused by: (1) DB_STATEMENT_TIMEOUT < SESSION_LOCK_WAIT_TIMEOUT*1000, "
                "(2) connection pool too small for concurrent requests, (3) SESSION_LOCK_WAIT_TIMEOUT too short for queue depth, or "
                "(4) PostgreSQL max_connections exceeded (check logs for 'sorry, too many clients already')."
            )
            if "localhost" in (os.environ.get("REQUEST_MANAGER_URL") or ""):
                hint += " Local dev: start request-manager with DB_POOL_SIZE=8 DB_MAX_OVERFLOW=8."
            hint += " See test docstring."
            print(hint, file=sys.stderr)
        elif (
            exc_list and any("too many clients" in str(e).lower() for e in exc_list)
        ) or any("too many clients" in str(msg).lower() for _, msg in errors):
            print(
                "\nHint: 500 'too many clients already' = PostgreSQL max_connections exceeded. "
                "See docs/SESSION_SERIALIZATION_RUNBOOK.md for connection budget.",
                file=sys.stderr,
            )

    if successes != total:
        print(f"FAIL: Expected {total} successes, got {successes}", file=sys.stderr)
        return False

    # Response order: by created_at and agent_received_at (staggered sends ensure order matches msg_idx)
    for user_idx in range(num_users):
        user_results = [
            (msg_idx, result_or_err)
            for u, msg_idx, result_or_err, exc, _ in results
            if u == user_idx and exc is None and isinstance(result_or_err, dict)
        ]
        user_results.sort(key=lambda x: x[0])  # by msg_idx for stable ordering

        # Check created_at order (accept order)
        order_vals = []
        for msg_idx, res in user_results:
            created_at = res.get("created_at") or (res.get("response") or {}).get(
                "created_at"
            )
            if created_at is None:
                print(
                    f"FAIL: Response for user{user_idx}/msg{msg_idx} missing created_at",
                    file=sys.stderr,
                )
                return False
            order_vals.append((msg_idx, created_at))

        order_vals.sort(key=lambda x: x[1])
        msg_order = [m for m, _ in order_vals]
        expected = list(range(requests_per_user))
        if msg_order != expected:
            print(
                f"FAIL: user{user_idx} created_at order is {msg_order}, expected {expected}",
                file=sys.stderr,
            )
            return False

        # Check agent_received_at order if present (agent processing order)
        agent_order_vals = []
        for msg_idx, res in user_results:
            agent_received_at = res.get("agent_received_at") or (
                res.get("response") or {}
            ).get("agent_received_at")
            if agent_received_at is not None:
                agent_order_vals.append((msg_idx, agent_received_at))
        if agent_order_vals:
            agent_order_vals.sort(key=lambda x: x[1])
            agent_msg_order = [m for m, _ in agent_order_vals]
            # FIFO: when sorted by agent_received_at, msg_idx should be ascending
            if agent_msg_order != sorted(agent_msg_order):
                print(
                    f"FAIL: user{user_idx} agent_received_at order is {agent_msg_order}, "
                    f"expected ascending msg_idx (agent processing order)",
                    file=sys.stderr,
                )
                return False

    print(
        f"PASS: All {total} concurrent requests ({num_users} users, {requests_per_user} per user) "
        "completed successfully; created_at and agent_received_at order verified"
    )
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Session serialization integration test (FIFO, concurrent requests)"
    )
    parser.add_argument(
        "--request-manager-url",
        default=REQUEST_MANAGER_URL,
        help="Request Manager URL (default: REQUEST_MANAGER_URL or localhost:8080)",
    )
    parser.add_argument(
        "-u",
        "--num-users",
        type=int,
        default=NUM_USERS,
        help="Number of users/sessions (default 3)",
    )
    parser.add_argument(
        "-n",
        "--requests-per-user",
        type=int,
        default=CONCURRENT_REQUESTS,
        help=f"Requests per user (default {CONCURRENT_REQUESTS})",
    )
    parser.add_argument(
        "--stagger-ms",
        type=float,
        default=None,
        help="Ms between sends per user (default: 1200 or STAGGER_MS env)",
    )
    args = parser.parse_args()

    success = asyncio.run(
        run_concurrent_session_requests(
            request_manager_url=args.request_manager_url,
            num_users=args.num_users,
            requests_per_user=args.requests_per_user,
            stagger_ms=args.stagger_ms,
        )
    )
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
