"""Shared session lock utilities for cross-pod serialization.

PostgreSQL advisory locks ensure one in-flight operation per session across all
replicas. Used by request-manager (dequeue) and agent-service (processing).

Namespace: request-manager and agent use DIFFERENT lock keys so they don't block
each other. Request-manager uses single-arg pg_try_advisory_lock(key).
Agent uses two-arg pg_try_advisory_lock(1, key_lo) for a separate key space.
"""

import asyncio
import hashlib
import time
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .logging import configure_logging

logger = configure_logging("shared-models")

# Agent lock namespace: use two-arg form (1, key_lo) so it never collides with
# request-manager's single-arg lock. key_lo = lower 32 bits of session key.
_AGENT_NAMESPACE = 1

# Default timeout (seconds) - should be >= AGENT_TIMEOUT so queued requests can wait
DEFAULT_LOCK_TIMEOUT = 180
DEFAULT_POLL_INTERVAL = 0.05


def session_id_to_lock_key(session_id: str) -> int:
    """Map session_id to a PostgreSQL advisory lock key (bigint).

    Same session_id always produces the same key. Used by request-manager
    (single-arg lock) and for agent key derivation (two-arg lock).
    """
    try:
        key = int(uuid.UUID(session_id).hex[:16], 16) & 0x7FFF_FFFF_FFFF_FFFF
    except (ValueError, TypeError):
        digest = hashlib.sha256(session_id.encode("utf-8")).digest()
        key = int.from_bytes(digest[:8], "big") & 0x7FFF_FFFF_FFFF_FFFF
    return key


def session_id_to_agent_lock_key(session_id: str) -> tuple[int, int]:
    """Return (namespace, key_lo) for agent's two-arg advisory lock.

    Uses a different key space than request-manager so they don't block each other.
    """
    key = session_id_to_lock_key(session_id)
    key_lo = key & 0x7FFF_FFFF  # Lower 32 bits for pg_try_advisory_lock(int, int)
    return (_AGENT_NAMESPACE, key_lo)


async def acquire_agent_session_lock(
    session_id: str,
    db: AsyncSession,
    timeout_seconds: float = DEFAULT_LOCK_TIMEOUT,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
) -> bool:
    """Acquire agent session lock via pg_try_advisory_lock with polling.

    Uses two-arg form (namespace, key) so it doesn't block request-manager's lock.
    """
    ns, key_lo = session_id_to_agent_lock_key(session_id)
    deadline = time.monotonic() + timeout_seconds
    last_log_at: float = 0

    while time.monotonic() < deadline:
        result = await db.execute(
            text("SELECT pg_try_advisory_lock(:ns, :key)"),
            {"ns": ns, "key": key_lo},
        )
        row = result.fetchone()
        if row and row[0]:
            logger.debug(
                "Agent session lock acquired",
                session_id=session_id,
                key_lo=key_lo,
            )
            return True
        now = time.monotonic()
        if now - last_log_at >= 10.0:
            logger.debug(
                "Agent session lock waiting",
                session_id=session_id,
                remaining_seconds=round(deadline - now, 1),
            )
            last_log_at = now
        await asyncio.sleep(poll_interval)

    logger.warning(
        "Agent session lock timeout",
        session_id=session_id,
        timeout_seconds=timeout_seconds,
    )
    return False


async def release_agent_session_lock(session_id: str, db: AsyncSession) -> None:
    """Release agent session lock. Idempotent if lock not held."""
    ns, key_lo = session_id_to_agent_lock_key(session_id)
    result = await db.execute(
        text("SELECT pg_advisory_unlock(:ns, :key) AS released"),
        {"ns": ns, "key": key_lo},
    )
    row = result.fetchone()
    released = row[0] if row else False
    if not released:
        logger.warning(
            "Agent session lock release returned false",
            session_id=session_id,
        )
    else:
        logger.debug("Agent session lock released", session_id=session_id)
