"""Per-session advisory lock for request serialization.

PostgreSQL advisory locks ensure one in-flight request per session across all replicas.
Uses pg_try_advisory_lock with polling to avoid the lock_timeout race (PG BUG #17686)
where pg_advisory_lock + lock_timeout can timeout even when the lock was granted.

Connection usage: when multiple requests queue for the same session, holding a DB
connection for the entire poll loop (seconds) can exceed PostgreSQL max_connections.
with_session_lock uses short-lived connections per lock attempt instead.
"""

import asyncio
import hashlib
import time
import uuid
from typing import Any, Awaitable, Callable, TypeVar

from shared_models import configure_logging
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .session_config import (
    SESSION_LOCK_POLL_INTERVAL_SECONDS,
    SESSION_LOCK_WAIT_TIMEOUT,
)

logger = configure_logging("request-manager")

# Default timeout for waiting to acquire lock (seconds)
DEFAULT_LOCK_TIMEOUT = SESSION_LOCK_WAIT_TIMEOUT

T = TypeVar("T")


def _session_id_to_lock_key(session_id: str) -> int:
    """Map session_id to a PostgreSQL advisory lock key (bigint).

    Uses first 16 hex chars of UUID as bigint. Mask to avoid negative.
    Same session_id always produces the same key.

    For non-UUID session_ids: uses SHA-256 hash (first 8 bytes as int) so the
    key is deterministic across processes/pods. Python's hash() is randomized
    across restarts and would break cross-pod locking.
    """
    try:
        key = int(uuid.UUID(session_id).hex[:16], 16) & 0x7FFF_FFFF_FFFF_FFFF
    except (ValueError, TypeError):
        # Deterministic fallback for non-UUID session_ids (hash() varies across
        # process restarts; would break multi-pod locking)
        digest = hashlib.sha256(session_id.encode("utf-8")).digest()
        key = int.from_bytes(digest[:8], "big") & 0x7FFF_FFFF_FFFF_FFFF
    return key


async def acquire_session_lock(
    session_id: str,
    db: AsyncSession,
    timeout_seconds: float = DEFAULT_LOCK_TIMEOUT,
    *,
    request_id: str | None = None,
) -> bool:
    """Acquire session lock via pg_try_advisory_lock with polling.

    Polling avoids PG BUG #17686: pg_advisory_lock + lock_timeout can race—
    the timeout may fire even when the lock was granted, or the grant may be
    delayed past the timeout. pg_try_advisory_lock has no race; we poll until
    we get it or the deadline expires.
    """
    lock_key = _session_id_to_lock_key(session_id)
    deadline = time.monotonic() + timeout_seconds
    last_log_at: float = 0
    _log_interval = 10.0  # Log every 10s while waiting

    while time.monotonic() < deadline:
        result = await db.execute(
            text("SELECT pg_try_advisory_lock(:key)"),
            {"key": lock_key},
        )
        row = result.fetchone()
        if row and row[0]:
            elapsed = time.monotonic() - (deadline - timeout_seconds)
            try:
                from .session_metrics import record_lock_acquire_duration

                record_lock_acquire_duration(elapsed)
            except Exception:  # noqa: BLE001
                pass
            logger.debug(
                "Session lock acquired",
                session_id=session_id,
                lock_key=lock_key,
                request_id=request_id,
            )
            return True
        now = time.monotonic()
        if now - last_log_at >= _log_interval:
            remaining = deadline - now
            logger.debug(
                "Session lock waiting (polling)",
                session_id=session_id,
                request_id=request_id,
                remaining_seconds=round(remaining, 1),
            )
            last_log_at = now
        await asyncio.sleep(SESSION_LOCK_POLL_INTERVAL_SECONDS)

    logger.warning(
        "Session lock timeout",
        session_id=session_id,
        request_id=request_id,
        timeout_seconds=timeout_seconds,
    )
    return False


async def release_session_lock(session_id: str, db: AsyncSession) -> None:
    """Release session lock. Idempotent if lock not held."""
    lock_key = _session_id_to_lock_key(session_id)
    # pg_advisory_unlock returns true iff this connection held and released the lock
    result = await db.execute(
        text(
            "SELECT pg_advisory_unlock(:key) AS released, pg_backend_pid() AS backend_pid"
        ),
        {"key": lock_key},
    )
    row = result.fetchone()
    released = row[0] if row else False
    backend_pid = row[1] if row and len(row) > 1 else None
    if not released:
        logger.warning(
            "Session lock release returned false (connection did not hold lock)",
            session_id=session_id,
            lock_key=lock_key,
            backend_pid=backend_pid,
        )
    else:
        logger.debug(
            "Session lock released",
            session_id=session_id,
            lock_key=lock_key,
        )


async def _try_lock_once(session_id: str, db: AsyncSession) -> bool:
    """Single pg_try_advisory_lock attempt. Returns True if acquired."""
    lock_key = _session_id_to_lock_key(session_id)
    result = await db.execute(
        text("SELECT pg_try_advisory_lock(:key)"),
        {"key": lock_key},
    )
    row = result.fetchone()
    return bool(row and row[0])


async def with_session_lock(
    session_id: str,
    db_manager: Any,
    critical_section: Callable[[AsyncSession], Awaitable[T]],
    timeout_seconds: float = DEFAULT_LOCK_TIMEOUT,
    *,
    request_id: str | None = None,
) -> T:
    """Acquire lock via short-lived connections when polling; run critical_section when acquired.

    Uses a new connection per lock attempt, releasing it immediately if the lock is not
    available. This avoids holding connections during the wait, preventing
    PostgreSQL max_connections exhaustion when many requests queue for the same session.

    Raises SessionLockTimeoutError on timeout.
    """
    lock_key = _session_id_to_lock_key(session_id)
    deadline = time.monotonic() + timeout_seconds
    last_log_at: float = 0
    _log_interval = 10.0

    while time.monotonic() < deadline:
        async with db_manager.get_session() as lock_db:
            if await _try_lock_once(session_id, lock_db):
                elapsed = time.monotonic() - (deadline - timeout_seconds)
                try:
                    from .session_metrics import record_lock_acquire_duration

                    record_lock_acquire_duration(elapsed)
                except Exception:  # noqa: BLE001
                    pass
                logger.debug(
                    "Session lock acquired (short-lived polling)",
                    session_id=session_id,
                    lock_key=lock_key,
                    request_id=request_id,
                )
                try:
                    return await critical_section(lock_db)
                finally:
                    await release_session_lock(session_id, lock_db)
        now = time.monotonic()
        if now - last_log_at >= _log_interval:
            remaining = deadline - now
            logger.debug(
                "Session lock waiting (polling)",
                session_id=session_id,
                request_id=request_id,
                remaining_seconds=round(remaining, 1),
            )
            last_log_at = now
        await asyncio.sleep(SESSION_LOCK_POLL_INTERVAL_SECONDS)

    logger.warning(
        "Session lock timeout",
        session_id=session_id,
        request_id=request_id,
        timeout_seconds=timeout_seconds,
    )
    from .exceptions import SessionLockTimeoutError

    raise SessionLockTimeoutError(
        "Session lock timeout - too many concurrent requests for this session"
    )
