"""Per-thread advisory lock for integration-dispatcher FIFO.

PostgreSQL advisory locks serialize publish per thread so events reach the broker
in receipt order. Delegates to shared_models.with_advisory_lock.

Lock key format (namespaced):
- Slack: integration:slack:{team_id}:{channel_id}:{thread_ts}
- Email: integration:email:{from_address}:{in_reply_to or message_id}
"""

import os
from typing import Any, Awaitable, Callable, Optional, TypeVar

from shared_models import configure_logging, with_advisory_lock

logger = configure_logging("integration-dispatcher")

# 10s default: fail fast for Slack 3s window; configurable for Email/high-contention
DEFAULT_LOCK_TIMEOUT = float(os.getenv("INTEGRATION_THREAD_LOCK_TIMEOUT", "10.0"))
DEFAULT_POLL_INTERVAL = 0.05

T = TypeVar("T")


def build_slack_thread_key(team_id: str, channel_id: str, thread_ts: str) -> str:
    """Build lock key for Slack thread. All args required."""
    return f"integration:slack:{team_id}:{channel_id}:{thread_ts}"


def build_email_thread_key(
    from_address: str,
    in_reply_to: Optional[str] = None,
    message_id: Optional[str] = None,
) -> str:
    """Build lock key for email thread. Fallback when both in_reply_to and message_id missing."""
    part = in_reply_to or message_id or "first"
    return f"integration:email:{from_address}:{part}"


async def with_thread_lock(
    thread_key: str,
    db_manager: Any,
    critical_section: Callable[[], Awaitable[T]],
    timeout_seconds: float = DEFAULT_LOCK_TIMEOUT,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
) -> T:
    """Acquire per-thread lock; run critical_section when acquired; release after.

    Delegates to shared_models.with_advisory_lock with pass_connection=False.
    """

    # Wrap no-arg critical_section to match Callable[[Optional[AsyncSession]], Awaitable[T]]
    async def _wrapped(_db: Optional[Any]) -> T:
        return await critical_section()

    return await with_advisory_lock(
        thread_key,
        db_manager,
        _wrapped,
        pass_connection=False,
        timeout_seconds=timeout_seconds,
        poll_interval=poll_interval,
    )
