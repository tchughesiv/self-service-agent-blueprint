"""Shared models and schemas for Self-Service Agent Blueprint."""

__version__ = "0.1.0"

# Export agent utilities
from .agent_types import (
    AgentMapping,
    create_agent_mapping,
    is_agent_name,
    is_agent_uuid,
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
)

# Export error utilities
from .errors import (
    AgentError,
    DatabaseError,
    ErrorResponse,
    IntegrationError,
    ServiceError,
    SessionError,
    create_error_response,
    create_forbidden_error,
    create_not_found_error,
    create_unauthorized_error,
    create_validation_error,
    handle_agent_error,
    handle_database_error,
    handle_generic_error,
    handle_integration_error,
    handle_service_error,
    handle_session_error,
)

# Export CloudEvent utilities
from .events import (
    CloudEventBuilder,
    CloudEventProcessor,
    CloudEventSender,
    CloudEventValidator,
    create_request_event,
    create_response_event,
    extract_event_context,
    validate_event_type,
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

# Export utilities
from .utils import get_enum_value

# Export database utilities


__all__ = [
    "get_enum_value",
    "DatabaseConfig",
    "DatabaseHealthChecker",
    "DatabaseManager",
    "DatabaseUtils",
    "get_database_manager",
    "get_db_config",
    "get_db_session",
    "get_db_session_dependency",
    "AgentMapping",
    "create_agent_mapping",
    "is_agent_name",
    "is_agent_uuid",
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
    "CloudEventProcessor",
    "CloudEventSender",
    "CloudEventValidator",
    "create_request_event",
    "create_response_event",
    "extract_event_context",
    "validate_event_type",
    "AgentError",
    "DatabaseError",
    "ErrorResponse",
    "IntegrationError",
    "ServiceError",
    "SessionError",
    "create_error_response",
    "create_forbidden_error",
    "create_not_found_error",
    "create_unauthorized_error",
    "create_validation_error",
    "handle_agent_error",
    "handle_database_error",
    "handle_generic_error",
    "handle_integration_error",
    "handle_service_error",
    "handle_session_error",
]
