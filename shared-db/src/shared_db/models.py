"""Consolidated database models for all services."""

from enum import Enum

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
)
from sqlalchemy import Enum as SQLEnum
from sqlalchemy import (
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import relationship

from .base import Base, TimestampMixin


# Enums used across services
class IntegrationType(str, Enum):
    """Integration types for both request sources and delivery channels."""

    SLACK = "SLACK"
    WEB = "WEB"
    CLI = "CLI"
    TOOL = "TOOL"
    EMAIL = "EMAIL"
    SMS = "SMS"
    WEBHOOK = "WEBHOOK"
    TEAMS = "TEAMS"
    DISCORD = "DISCORD"
    TEST = "TEST"


class SessionStatus(str, Enum):
    """Session status for request management."""

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    EXPIRED = "EXPIRED"
    ARCHIVED = "ARCHIVED"


class DeliveryStatus(str, Enum):
    """Delivery status for integration messages."""

    PENDING = "PENDING"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    RETRYING = "RETRYING"
    EXPIRED = "EXPIRED"


# Request Manager Models
class RequestSession(Base, TimestampMixin):
    """User conversation sessions."""

    __tablename__ = "request_sessions"

    id = Column(Integer, primary_key=True)
    session_id = Column(String(36), unique=True, nullable=False, index=True)
    user_id = Column(String(255), nullable=False, index=True)
    integration_type = Column(SQLEnum(IntegrationType), nullable=False)
    status = Column(
        SQLEnum(SessionStatus), default=SessionStatus.ACTIVE.value, nullable=False
    )

    # Session context
    channel_id = Column(String(255))  # Slack channel, Teams channel, etc.
    thread_id = Column(String(255))  # Thread/conversation ID
    external_session_id = Column(String(255))  # External platform session ID

    # Agent tracking
    current_agent_id = Column(String(255))  # Currently assigned agent
    llama_stack_session_id = Column(String(255))  # LlamaStack session ID

    # Session metadata
    integration_metadata = Column(JSON, default=dict)
    user_context = Column(JSON, default=dict)  # User context from platform
    conversation_context = Column(JSON, default=dict)  # Conversation state

    # Session statistics
    total_requests = Column(Integer, default=0, nullable=False)
    last_request_id = Column(String(36))  # Most recent request ID
    last_request_at = Column(TIMESTAMP(timezone=True))
    expires_at = Column(TIMESTAMP(timezone=True))

    # Relationships
    request_logs = relationship(
        "RequestLog", back_populates="session", cascade="all, delete-orphan"
    )


class RequestLog(Base, TimestampMixin):
    """Log of all requests processed."""

    __tablename__ = "request_logs"

    id = Column(Integer, primary_key=True)
    request_id = Column(String(36), unique=True, nullable=False, index=True)
    session_id = Column(
        String(36),
        ForeignKey("request_sessions.session_id"),
        nullable=False,
        index=True,
    )

    # Request details
    request_type = Column(String(50), nullable=False)  # "slack", "web", "cli", "tool"
    request_content = Column(Text, nullable=False)
    normalized_request = Column(JSON)  # Normalized request structure

    # Agent processing
    agent_id = Column(String(255))
    processing_time_ms = Column(Integer)

    # Response details
    response_content = Column(Text)
    response_metadata = Column(JSON, default=dict)

    # CloudEvent tracking
    cloudevent_id = Column(String(36))
    cloudevent_type = Column(String(100))

    # Timing
    completed_at = Column(TIMESTAMP(timezone=True))

    # Relationships
    session = relationship("RequestSession", back_populates="request_logs")


# Integration Dispatcher Models
class UserIntegrationConfig(Base, TimestampMixin):
    """Per-user integration configuration."""

    __tablename__ = "user_integration_configs"

    id = Column(Integer, primary_key=True)
    user_id = Column(String(255), nullable=False, index=True)
    integration_type = Column(SQLEnum(IntegrationType), nullable=False)
    enabled = Column(Boolean, default=True, nullable=False)

    # Integration-specific configuration
    config = Column(JSON, nullable=False, default=dict)

    # Delivery preferences
    priority = Column(Integer, default=0, nullable=False)  # Higher = more important
    retry_count = Column(Integer, default=3, nullable=False)
    retry_delay_seconds = Column(Integer, default=60, nullable=False)

    # Metadata
    created_by = Column(String(255))  # Who configured this integration

    # Relationships
    delivery_logs = relationship("DeliveryLog", back_populates="integration_config")

    # Ensure one config per user per integration type
    __table_args__ = (
        UniqueConstraint("user_id", "integration_type", name="uq_user_integration"),
    )


class IntegrationTemplate(Base, TimestampMixin):
    """Templates for different integration types."""

    __tablename__ = "integration_templates"

    id = Column(Integer, primary_key=True)
    integration_type = Column(SQLEnum(IntegrationType), nullable=False)
    template_name = Column(String(100), nullable=False)

    # Template content
    subject_template = Column(Text)  # For email/notification title
    body_template = Column(Text, nullable=False)

    # Template variables and metadata
    required_variables = Column(JSON, default=list)  # List of required template vars
    optional_variables = Column(JSON, default=list)  # List of optional template vars

    # Template configuration
    is_default = Column(Boolean, default=False, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)

    # Unique template per integration type and name
    __table_args__ = (
        UniqueConstraint(
            "integration_type", "template_name", name="uq_integration_template"
        ),
    )


class DeliveryLog(Base, TimestampMixin):
    """Log of integration message deliveries."""

    __tablename__ = "delivery_logs"

    id = Column(Integer, primary_key=True)

    # Request/session context
    request_id = Column(String(36), nullable=False, index=True)
    session_id = Column(String(36), nullable=False, index=True)
    user_id = Column(String(255), nullable=False, index=True)

    # Integration details
    integration_config_id = Column(
        Integer, ForeignKey("user_integration_configs.id"), nullable=False
    )
    integration_type = Column(SQLEnum(IntegrationType), nullable=False)

    # Message content
    subject = Column(Text)
    content = Column(Text, nullable=False)
    template_used = Column(String(100))

    # Delivery tracking
    status = Column(
        SQLEnum(DeliveryStatus), default=DeliveryStatus.PENDING.value, nullable=False
    )
    attempts = Column(Integer, default=0, nullable=False)
    max_attempts = Column(Integer, default=3, nullable=False)

    # Timing
    first_attempt_at = Column(TIMESTAMP(timezone=True))
    last_attempt_at = Column(TIMESTAMP(timezone=True))
    delivered_at = Column(TIMESTAMP(timezone=True))
    expires_at = Column(TIMESTAMP(timezone=True))  # When to stop retrying

    # Error tracking
    error_message = Column(Text)
    error_details = Column(JSON)

    # Integration-specific metadata
    integration_metadata = Column(JSON, default=dict)  # Channel IDs, message IDs, etc.

    # Relationships
    integration_config = relationship(
        "UserIntegrationConfig", back_populates="delivery_logs"
    )


class IntegrationCredential(Base, TimestampMixin):
    """Secure storage for integration credentials."""

    __tablename__ = "integration_credentials"

    id = Column(Integer, primary_key=True)
    integration_type = Column(SQLEnum(IntegrationType), nullable=False)
    credential_name = Column(
        String(100), nullable=False
    )  # e.g., "slack_bot_token", "smtp_password"

    # Encrypted credential value (encrypt before storing)
    encrypted_value = Column(Text, nullable=False)

    # Metadata
    description = Column(Text)
    created_by = Column(String(255))

    # Unique credential per integration type and name
    __table_args__ = (
        UniqueConstraint(
            "integration_type", "credential_name", name="uq_integration_credential"
        ),
    )
