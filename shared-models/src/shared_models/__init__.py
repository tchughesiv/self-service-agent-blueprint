"""Shared models and schemas for Self-Service Agent Blueprint."""

__version__ = "0.1.0"

# Export advisory lock (request-manager, integration-dispatcher)
from .advisory_lock import with_advisory_lock

# Export CloudEvent utilities
from .cloudevent_utils import (
    CloudEventHandler,
    create_cloudevent_response,
    parse_cloudevent_from_request,
)
from .database import (
    DatabaseConfig,
    DatabaseHealthChecker,
    DatabaseManager,
    DatabaseUtils,
    get_database_manager,
    get_db_config,
    get_db_session,
    get_db_session_dependency,
    get_db_utc_now,
)

# Export CloudEvent utilities
from .events import (
    CloudEventBuilder,
    CloudEventSender,
    EventTypes,
    agent_response_event_id,
)

# Export FastAPI utilities
from .fastapi_utils import (
    create_health_check_dependency,
    create_health_check_endpoint,
    create_shared_lifespan,
    create_standard_fastapi_app,
)

# Export health utilities
from .health import HealthChecker, HealthCheckResult, simple_health_check

# Export logging utilities
from .logging import (
    LoggingConfig,
    ServiceLogger,
    configure_logging,
    get_service_logger,
    log_database_operation,
    log_error,
    log_health_check,
    log_integration_event,
    log_request,
    log_response,
)

# Export outbox (Step 0.25)
from .outbox import (
    SOURCE_SERVICE_INTEGRATION_DISPATCHER,
    insert_outbox_event,
    mark_outbox_failed,
    mark_outbox_published,
    reset_outbox_for_retry,
)

# Export request log ordering utilities
from .request_log import (
    get_request_created_at,
    has_earlier_pending_or_processing,
)

# Export security utilities
from .security import verify_slack_signature

# Export session lock (agent cross-pod serialization)
from .session_lock import (
    acquire_agent_session_lock,
    release_agent_session_lock,
    session_id_to_lock_key,
)

# Export session management
from .session_manager import BaseSessionManager, get_or_create_zammad_ticket_session
from .session_schemas import SessionCreate, SessionResponse, SessionUpdate

# Export user utilities
from .user_utils import (
    get_or_create_canonical_user,
    is_uuid,
    resolve_canonical_user_id,
)

# Export utilities
from .utils import generate_fallback_user_id, get_enum_value

__all__ = [
    "verify_slack_signature",
    "create_health_check_dependency",
    "create_health_check_endpoint",
    "create_shared_lifespan",
    "create_standard_fastapi_app",
    "parse_cloudevent_from_request",
    "create_cloudevent_response",
    "get_enum_value",
    "generate_fallback_user_id",
    "get_or_create_canonical_user",
    "is_uuid",
    "resolve_canonical_user_id",
    "CloudEventHandler",
    "DatabaseConfig",
    "DatabaseHealthChecker",
    "DatabaseManager",
    "DatabaseUtils",
    "get_database_manager",
    "get_db_config",
    "get_db_session",
    "get_db_session_dependency",
    "get_db_utc_now",
    "acquire_agent_session_lock",
    "release_agent_session_lock",
    "session_id_to_lock_key",
    "with_advisory_lock",
    "get_request_created_at",
    "has_earlier_pending_or_processing",
    "HealthChecker",
    "HealthCheckResult",
    "simple_health_check",
    "LoggingConfig",
    "ServiceLogger",
    "configure_logging",
    "get_service_logger",
    "log_database_operation",
    "log_error",
    "log_health_check",
    "log_integration_event",
    "log_request",
    "log_response",
    "CloudEventBuilder",
    "CloudEventSender",
    "EventTypes",
    "agent_response_event_id",
    "BaseSessionManager",
    "get_or_create_zammad_ticket_session",
    "SessionCreate",
    "SessionResponse",
    "SessionUpdate",
    "SOURCE_SERVICE_INTEGRATION_DISPATCHER",
    "insert_outbox_event",
    "mark_outbox_failed",
    "mark_outbox_published",
    "reset_outbox_for_retry",
]
