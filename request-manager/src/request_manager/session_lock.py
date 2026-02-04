"""Per-session advisory lock for request serialization.

PostgreSQL advisory locks ensure one in-flight request per session across all replicas.
Delegates to shared_models.with_advisory_lock with pass_connection=True.
"""

import asyncio
import time
from typing import Any, Awaitable, Callable, TypeVar

from shared_models import configure_logging, session_id_to_lock_key, with_advisory_lock
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

    Prefer with_session_lock for request handling; it uses short-lived connections
    per attempt to avoid holding connections during wait. Use acquire_session_lock
    only when you already hold a long-lived connection and need the lock.
    """
    lock_key = session_id_to_lock_key(session_id)
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
    lock_key = session_id_to_lock_key(session_id)
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


async def with_session_lock(
    session_id: str,
    db_manager: Any,
    critical_section: Callable[[AsyncSession], Awaitable[T]],
    timeout_seconds: float = DEFAULT_LOCK_TIMEOUT,
    *,
    request_id: str | None = None,
) -> T:
    """Acquire lock via short-lived connections when polling; run critical_section when acquired.

    Uses shared_models.with_advisory_lock. Raises SessionLockTimeoutError on timeout.
    """

    def _on_lock_acquired(elapsed: float) -> None:
        try:
            from .session_metrics import record_lock_acquire_duration

            record_lock_acquire_duration(elapsed)
        except Exception:  # noqa: BLE001
            pass

    try:
        return await with_advisory_lock(
            session_id,
            db_manager,
            critical_section,  # type: ignore[arg-type]
            pass_connection=True,
            timeout_seconds=timeout_seconds,
            poll_interval=SESSION_LOCK_POLL_INTERVAL_SECONDS,
            on_lock_acquired=_on_lock_acquired,
        )
    except TimeoutError:
        logger.warning(
            "Session lock timeout",
            session_id=session_id,
            request_id=request_id,
            timeout_seconds=timeout_seconds,
        )
        from .exceptions import SessionLockTimeoutError

        raise SessionLockTimeoutError(
            "Session lock timeout - too many concurrent requests for this session"
        ) from None
