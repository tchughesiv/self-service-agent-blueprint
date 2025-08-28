"""Database models for Request Manager."""

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

from sqlalchemy import JSON, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(AsyncAttrs, DeclarativeBase):
    """Base class for all database models."""

    pass


class SessionStatus(str, Enum):
    """Session status enumeration."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    COMPLETED = "completed"
    ERROR = "error"


class IntegrationType(str, Enum):
    """Integration type enumeration."""

    SLACK = "slack"
    WEB = "web"
    CLI = "cli"
    TOOL = "tool"
    API = "api"


class RequestSession(Base):
    """Session model for tracking conversations and context.
    
    Based on the gist schema: https://gist.github.com/tchughesiv/5153cb449e29123dba294812da888884
    """

    __tablename__ = "request_sessions"

    # Primary identifiers
    session_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    integration_type: Mapped[IntegrationType] = mapped_column(nullable=False)
    
    # Integration-specific identifiers
    channel_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    thread_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    external_session_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    
    # Agent and conversation state
    current_agent_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    llama_stack_session_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status: Mapped[SessionStatus] = mapped_column(default=SessionStatus.ACTIVE)
    
    # Context and metadata
    conversation_context: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    integration_metadata: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    user_context: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    
    # Request tracking
    total_requests: Mapped[int] = mapped_column(default=0)
    last_request_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)


class RequestLog(Base):
    """Log model for tracking individual requests and responses."""

    __tablename__ = "request_logs"

    # Primary identifiers
    request_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    session_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), nullable=False, index=True
    )
    
    # Request details
    request_type: Mapped[str] = mapped_column(String(100), nullable=False)
    request_content: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_request: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False)
    
    # Response details
    response_content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    response_metadata: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    
    # Processing details
    agent_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    processing_time_ms: Mapped[Optional[int]] = mapped_column(nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # CloudEvent tracking
    cloudevent_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    cloudevent_type: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class IntegrationConfig(Base):
    """Configuration model for different integrations."""

    __tablename__ = "integration_configs"

    # Primary identifiers
    config_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    integration_type: Mapped[IntegrationType] = mapped_column(nullable=False)
    integration_name: Mapped[str] = mapped_column(String(255), nullable=False)
    
    # Configuration
    config_data: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True)
    
    # Metadata
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
