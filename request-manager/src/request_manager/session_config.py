"""Session serialization configuration.

Centralizes env vars for lock, heartbeat, reclaim. Validates RECLAIM_ACTION
at load; logs warning for invalid values and defaults to requeue.
"""

import os

from shared_models import configure_logging

logger = configure_logging("request-manager")

# Session lock - must be >= AGENT_TIMEOUT so queued requests can wait for current one
SESSION_LOCK_WAIT_TIMEOUT = int(os.getenv("SESSION_LOCK_WAIT_TIMEOUT", "180"))
SESSION_LOCK_POLL_INTERVAL_SECONDS = float(
    os.getenv("SESSION_LOCK_POLL_INTERVAL_SECONDS", "0.05")
)
SESSION_LOCK_STUCK_BUFFER_SECONDS = int(
    os.getenv("SESSION_LOCK_STUCK_BUFFER_SECONDS", "30")
)

# Agent timeout (used for reclaim cutoff)
AGENT_TIMEOUT = int(os.getenv("AGENT_TIMEOUT", "120"))

# Pod heartbeat
POD_HEARTBEAT_INTERVAL_SECONDS = int(os.getenv("POD_HEARTBEAT_INTERVAL_SECONDS", "15"))
POD_HEARTBEAT_GRACE_SECONDS = int(os.getenv("POD_HEARTBEAT_GRACE_SECONDS", "30"))

# Background reclaim
BACKGROUND_RECLAIM_INTERVAL_SECONDS = int(
    os.getenv("BACKGROUND_RECLAIM_INTERVAL_SECONDS", "45")
)

# Reclaim action: requeue (reset to pending) or fail (mark failed)
_raw_reclaim_action = os.getenv("RECLAIM_ACTION", "requeue").lower().strip()
if _raw_reclaim_action not in ("requeue", "fail"):
    logger.warning(
        "Invalid RECLAIM_ACTION, defaulting to requeue",
        value=_raw_reclaim_action,
        allowed=("requeue", "fail"),
    )
    RECLAIM_ACTION = "requeue"
else:
    RECLAIM_ACTION = _raw_reclaim_action
