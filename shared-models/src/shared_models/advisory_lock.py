"""Shared advisory lock for cross-pod serialization.

PostgreSQL advisory locks with short-lived connections per poll attempt.
Reuses session_id_to_lock_key for key derivation. Callers pass a string key
(session_id or prefixed thread_key) that maps to a unique lock.

Call sites:
  - request-manager/session_lock.py: with_session_lock (per-session FIFO dequeue)
  - integration-dispatcher/thread_lock.py: with_thread_lock (per-thread FIFO publish)
  - agent-service: uses shared_models.session_lock (separate two-arg namespace)
"""

import asyncio
import time
from typing import Any, Awaitable, Callable, Optional, TypeVar

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .logging import configure_logging
from .session_lock import session_id_to_lock_key

logger = configure_logging("shared-models")

DEFAULT_LOCK_TIMEOUT = 180.0
DEFAULT_POLL_INTERVAL = 0.05

T = TypeVar("T")


async def _try_lock_once(lock_key_str: str, db: AsyncSession) -> bool:
    """Single pg_try_advisory_lock attempt. Returns True if acquired."""
    lock_key = session_id_to_lock_key(lock_key_str)
    result = await db.execute(
        text("SELECT pg_try_advisory_lock(:key)"),
        {"key": lock_key},
    )
    row = result.fetchone()
    return bool(row and row[0])


async def _release_lock(lock_key_str: str, db: AsyncSession) -> None:
    """Release advisory lock. Logs warning if release returns false."""
    lock_key = session_id_to_lock_key(lock_key_str)
    result = await db.execute(
        text("SELECT pg_advisory_unlock(:key) AS released"),
        {"key": lock_key},
    )
    row = result.fetchone()
    released = row[0] if row else False
    if not released:
        logger.warning(
            "Advisory lock release returned false (connection did not hold lock)",
            lock_key_str=lock_key_str[:80] if lock_key_str else "(empty)",
        )


async def with_advisory_lock(
    lock_key_str: str,
    db_manager: Any,
    critical_section: Callable[[Optional[AsyncSession]], Awaitable[T]],
    *,
    pass_connection: bool = False,
    timeout_seconds: float = DEFAULT_LOCK_TIMEOUT,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    on_lock_acquired: Optional[Callable[[float], None]] = None,
) -> T:
    """Acquire advisory lock; run critical_section; release.

    Uses short-lived connections per poll attempt to avoid holding connections
    during wait.

    Args:
        lock_key_str: String key (e.g. session_id or "integration:slack:...")
            used to derive pg advisory lock key.
        db_manager: From get_database_manager(); provides get_session().
        critical_section: Async callable. When pass_connection=True, receives
            the lock_db (AsyncSession holding the lock). When False, receives None.
        pass_connection: If True, pass lock_db to critical_section; else pass None.
        timeout_seconds: Max wait for lock.
        poll_interval: Seconds between lock attempts.
        on_lock_acquired: Optional callback(elapsed_seconds) when lock acquired.

    Returns:
        Result of critical_section.

    Raises:
        TimeoutError: If lock not acquired within timeout_seconds.
    """
    deadline = time.monotonic() + timeout_seconds
    last_log_at: float = 0
    _log_interval = 10.0

    while time.monotonic() < deadline:
        async with db_manager.get_session() as lock_db:
            if await _try_lock_once(lock_key_str, lock_db):
                elapsed = time.monotonic() - (deadline - timeout_seconds)
                if on_lock_acquired:
                    try:
                        on_lock_acquired(elapsed)
                    except Exception:  # noqa: BLE001
                        pass
                logger.debug(
                    "Advisory lock acquired",
                    lock_key_str=lock_key_str[:80] if lock_key_str else "(empty)",
                )
                try:
                    if pass_connection:
                        return await critical_section(lock_db)
                    else:
                        return await critical_section(None)
                finally:
                    await _release_lock(lock_key_str, lock_db)

        now = time.monotonic()
        if now - last_log_at >= _log_interval:
            logger.debug(
                "Advisory lock waiting",
                lock_key_str=lock_key_str[:80] if lock_key_str else "(empty)",
                remaining_seconds=round(deadline - now, 1),
            )
            last_log_at = now
        await asyncio.sleep(poll_interval)

    logger.warning(
        "Advisory lock timeout",
        lock_key_str=lock_key_str[:80] if lock_key_str else "(empty)",
        timeout_seconds=timeout_seconds,
    )
    raise TimeoutError(
        f"Advisory lock timeout after {timeout_seconds}s for {lock_key_str[:80]!r}"
    )
