"""Main FastAPI application for Request Manager."""

import hashlib
import hmac

# Configure structured logging
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Union

import structlog
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from shared_db import get_enum_value
from shared_db.models import RequestLog
from shared_db.session import get_database_manager, get_db_session
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession

from . import __version__
from .events import EventTypes, get_event_publisher
from .normalizer import RequestNormalizer
from .schemas import (
    BaseRequest,
    CLIRequest,
    ErrorResponse,
    HealthCheck,
    SessionCreate,
    SessionResponse,
    SlackRequest,
    ToolRequest,
    WebRequest,
)
from .session_manager import SessionManager

# Set up basic logging to stdout
logging.basicConfig(
    level=logging.DEBUG if os.getenv("LOG_LEVEL", "INFO") == "DEBUG" else logging.INFO,
    format="%(message)s",
    stream=sys.stdout,
)

structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer(),
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup
    logger.info("Starting Request Manager", version=__version__)

    # Wait for database migration to complete
    db_manager = get_database_manager()
    try:
        migration_ready = await db_manager.wait_for_migration(timeout=300)
        if not migration_ready:
            raise Exception("Database migration did not complete within timeout")
        logger.info("Database migration verified and ready")
    except Exception as e:
        logger.error("Failed to verify database migration", error=str(e))
        raise

    yield

    # Shutdown
    logger.info("Shutting down Request Manager")

    # Close database connections
    await db_manager.close()

    # Close event publisher
    event_publisher = get_event_publisher()
    await event_publisher.close()


# Create FastAPI application
app = FastAPI(
    title="Self-Service Agent Request Manager",
    description="Request Management Layer for Self-Service Agent Blueprint",
    version=__version__,
    lifespan=lifespan,
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize components
normalizer = RequestNormalizer()
security = HTTPBearer(auto_error=False)

# Service Mesh configuration
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET", "")
API_KEYS = {
    "snow-integration": os.getenv("SNOW_API_KEY", ""),
    "hr-system": os.getenv("HR_API_KEY", ""),
    "monitoring-system": os.getenv("MONITORING_API_KEY", ""),
}


def verify_slack_signature(
    body: bytes, timestamp: str, signature: str, secret: str
) -> bool:
    """Verify Slack request signature."""
    if not secret:
        logger.warning("Slack signing secret not configured")
        return True  # Skip verification if not configured

    # Check timestamp to prevent replay attacks
    current_time = int(time.time())
    request_time = int(timestamp)

    if abs(current_time - request_time) > 300:  # 5 minutes
        return False

    # Verify signature
    basestring = f"v0:{timestamp}:{body.decode('utf-8')}"
    expected_signature = (
        "v0="
        + hmac.new(secret.encode(), basestring.encode(), hashlib.sha256).hexdigest()
    )

    return hmac.compare_digest(expected_signature, signature)


def verify_api_key(api_key: str, tool_id: Optional[str] = None) -> bool:
    """Verify API key for tool integrations."""
    if not api_key:
        return False

    # Check against configured API keys
    for key_name, key_value in API_KEYS.items():
        if key_value and api_key == key_value:
            # Optionally verify tool_id matches key_name
            if tool_id and tool_id not in key_name:
                continue
            return True

    return False


async def get_current_user(
    request: Request,
    authorization: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Optional[Dict[str, Any]]:
    """Extract user information from Service Mesh headers or JWT."""
    # Check for Istio-injected user headers (from JWT)
    user_info = {}

    # Extract user ID from various sources
    user_id = (
        request.headers.get("x-user-id")
        or request.headers.get("x-forwarded-user")
        or request.headers.get("x-remote-user")
    )

    if user_id:
        user_info["user_id"] = user_id

    # Extract additional user context from headers
    if request.headers.get("x-user-email"):
        user_info["email"] = request.headers.get("x-user-email")

    if request.headers.get("x-user-groups"):
        user_info["groups"] = request.headers.get("x-user-groups").split(",")

    # Extract JWT claims if available (passed through by Istio)
    if authorization and authorization.credentials:
        user_info["token"] = authorization.credentials

    return user_info if user_info else None


@app.get("/health", response_model=HealthCheck)
async def health_check(db: AsyncSession = Depends(get_db_session)) -> HealthCheck:
    """Health check endpoint."""
    try:
        # Test database connection
        await db.execute(text("SELECT 1"))
        database_connected = True
    except Exception:
        database_connected = False

    return HealthCheck(
        status="healthy" if database_connected else "degraded",
        database_connected=database_connected,
        services={
            "database": "connected" if database_connected else "disconnected",
            "event_publisher": "ready",
        },
    )


@app.post("/api/v1/sessions", response_model=SessionResponse)
async def create_session(
    session_data: SessionCreate,
    db: AsyncSession = Depends(get_db_session),
) -> SessionResponse:
    """Create a new session."""
    session_manager = SessionManager(db)

    try:
        session = await session_manager.create_session(session_data)

        # Publish session created event
        event_publisher = get_event_publisher()
        await event_publisher.publish_session_event(
            session.session_id,
            EventTypes.SESSION_CREATED,
            {
                "session_id": session.session_id,
                "user_id": session.user_id,
                "integration_type": get_enum_value(session.integration_type),
                "created_at": session.created_at.isoformat(),
            },
        )

        logger.info(
            "Session created",
            session_id=session.session_id,
            user_id=session.user_id,
            integration_type=get_enum_value(session.integration_type),
        )

        return session

    except Exception as e:
        logger.error("Failed to create session", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create session",
        )


@app.get("/api/v1/sessions/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: str,
    db: AsyncSession = Depends(get_db_session),
) -> SessionResponse:
    """Get session information."""
    session_manager = SessionManager(db)

    session = await session_manager.get_session(session_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    return session


@app.post("/api/v1/requests/slack")
async def handle_slack_request(
    slack_request: SlackRequest,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    x_slack_signature: Optional[str] = Header(None, alias="x-slack-signature"),
    x_slack_request_timestamp: Optional[str] = Header(
        None, alias="x-slack-request-timestamp"
    ),
) -> Dict[str, Any]:
    """Handle Slack integration requests with signature verification."""
    # Verify Slack signature if configured
    if SLACK_SIGNING_SECRET and x_slack_signature and x_slack_request_timestamp:
        body = await request.body()
        if not verify_slack_signature(
            body, x_slack_request_timestamp, x_slack_signature, SLACK_SIGNING_SECRET
        ):
            logger.warning("Invalid Slack signature", user_id=slack_request.user_id)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature"
            )

    return await _process_request(slack_request, db)


@app.post("/api/v1/requests/web")
async def handle_web_request(
    web_request: WebRequest,
    db: AsyncSession = Depends(get_db_session),
    current_user: Optional[Dict[str, Any]] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Handle web interface requests with JWT authentication."""
    # Validate user authentication for web requests
    if not current_user or not current_user.get("user_id"):
        logger.warning("Unauthenticated web request", request_user=web_request.user_id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required"
        )

    # Ensure the authenticated user matches the request user
    if current_user["user_id"] != web_request.user_id:
        logger.warning(
            "User ID mismatch",
            authenticated_user=current_user["user_id"],
            request_user=web_request.user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="User ID mismatch"
        )

    return await _process_request(web_request, db)


@app.post("/api/v1/requests/cli")
async def handle_cli_request(
    cli_request: CLIRequest,
    db: AsyncSession = Depends(get_db_session),
    current_user: Optional[Dict[str, Any]] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Handle CLI requests with authentication."""
    # Validate user authentication for CLI requests
    if not current_user or not current_user.get("user_id"):
        logger.warning("Unauthenticated CLI request", request_user=cli_request.user_id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required"
        )

    # Ensure the authenticated user matches the request user
    if current_user["user_id"] != cli_request.user_id:
        logger.warning(
            "User ID mismatch",
            authenticated_user=current_user["user_id"],
            request_user=cli_request.user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="User ID mismatch"
        )

    return await _process_request(cli_request, db)


@app.post("/api/v1/requests/tool")
async def handle_tool_request(
    tool_request: ToolRequest,
    db: AsyncSession = Depends(get_db_session),
    x_api_key: Optional[str] = Header(None, alias="x-api-key"),
) -> Dict[str, Any]:
    """Handle tool-generated requests with API key authentication."""
    # Verify API key for tool requests
    if not verify_api_key(x_api_key or "", tool_request.tool_id):
        logger.warning("Invalid API key for tool request", tool_id=tool_request.tool_id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key"
        )

    return await _process_request(tool_request, db)


@app.post("/api/v1/requests/generic")
async def handle_generic_request(
    request: BaseRequest,
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Handle generic requests."""
    return await _process_request(request, db)


@app.post("/api/v1/requests/generic/sync")
async def handle_generic_request_sync(
    request: BaseRequest,
    db: AsyncSession = Depends(get_db_session),
    timeout: int = 120,
) -> Dict[str, Any]:
    """Handle generic requests synchronously - waits for AI response."""
    return await _process_request_sync(request, db, timeout)


@app.post("/api/v1/events/cloudevents")
async def handle_cloudevent(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Handle incoming CloudEvents (e.g., from agent responses)."""
    try:
        # Parse CloudEvent from request
        headers = dict(request.headers)
        body = await request.body()

        # Handle agent response events
        if headers.get("ce-type") == EventTypes.AGENT_RESPONSE_READY:
            return await _handle_agent_response_event(headers, body, db)

        logger.warning("Unhandled CloudEvent type", event_type=headers.get("ce-type"))
        return {"status": "ignored", "reason": "unhandled event type"}

    except Exception as e:
        logger.error("Failed to handle CloudEvent", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to process CloudEvent",
        )


async def _process_request(
    request: Union[BaseRequest, SlackRequest, WebRequest, CLIRequest, ToolRequest],
    db: AsyncSession,
) -> Dict[str, Any]:
    """Process an incoming request."""
    session_manager = SessionManager(db)
    event_publisher = get_event_publisher()

    try:
        # Find or create session
        session = await session_manager.find_or_create_session(
            user_id=request.user_id,
            integration_type=request.integration_type,
            channel_id=getattr(request, "channel_id", None),
            thread_id=getattr(request, "thread_id", None),
            integration_metadata=request.metadata,
        )

        # Normalize the request
        normalized_request = normalizer.normalize_request(request, session.session_id)

        # Log the request
        request_log = RequestLog(
            request_id=normalized_request.request_id,
            session_id=session.session_id,
            request_type=request.request_type,
            request_content=request.content,
            normalized_request=normalized_request.model_dump(mode="json"),
            agent_id=normalized_request.target_agent_id,
        )

        db.add(request_log)
        await db.commit()

        # Update session request count
        await session_manager.increment_request_count(
            session.session_id, normalized_request.request_id
        )

        # Publish request event to broker
        success = await event_publisher.publish_request_event(
            normalized_request, EventTypes.REQUEST_CREATED
        )

        if not success:
            logger.error("Failed to publish request event")
            # Continue processing even if event publishing fails

        logger.info(
            "Request processed",
            request_id=normalized_request.request_id,
            session_id=session.session_id,
            user_id=request.user_id,
            integration_type=get_enum_value(request.integration_type),
        )

        return {
            "request_id": normalized_request.request_id,
            "session_id": session.session_id,
            "status": "accepted",
            "message": "Request has been queued for processing",
        }

    except Exception as e:
        logger.error("Failed to process request", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process request",
        )


async def _process_request_sync(
    request: Union[BaseRequest, SlackRequest, WebRequest, CLIRequest, ToolRequest],
    db: AsyncSession,
    timeout: int = 120,
) -> Dict[str, Any]:
    """Process a request synchronously and wait for AI response."""
    import asyncio

    session_manager = SessionManager(db)
    event_publisher = get_event_publisher()

    try:
        # Find or create session
        session = await session_manager.find_or_create_session(
            user_id=request.user_id,
            integration_type=request.integration_type,
            channel_id=getattr(request, "channel_id", None),
            thread_id=getattr(request, "thread_id", None),
            integration_metadata=request.metadata,
        )

        # Normalize the request
        normalized_request = normalizer.normalize_request(request, session.session_id)

        # Log the request
        request_log = RequestLog(
            request_id=normalized_request.request_id,
            session_id=session.session_id,
            request_type=request.request_type,
            request_content=request.content,
            normalized_request=normalized_request.model_dump(mode="json"),
            agent_id=normalized_request.target_agent_id,
        )

        db.add(request_log)
        await db.commit()

        # Update session request count
        await session_manager.increment_request_count(
            session.session_id, normalized_request.request_id
        )

        # Publish request event to broker
        success = await event_publisher.publish_request_event(
            normalized_request, EventTypes.REQUEST_CREATED
        )

        if not success:
            logger.error("Failed to publish request event")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to publish request event",
            )

        logger.info(
            "Request processed, waiting for response",
            request_id=normalized_request.request_id,
            session_id=session.session_id,
            user_id=request.user_id,
            timeout=timeout,
        )

        # Wait for AI response with timeout
        response_data = await _wait_for_response(
            normalized_request.request_id, timeout, db
        )

        return {
            "request_id": normalized_request.request_id,
            "session_id": session.session_id,
            "status": "completed",
            "response": response_data,
        }

    except asyncio.TimeoutError:
        logger.warning(
            "Request timeout waiting for response",
            request_id=(
                normalized_request.request_id
                if "normalized_request" in locals()
                else "unknown"
            ),
            timeout=timeout,
        )
        raise HTTPException(
            status_code=status.HTTP_408_REQUEST_TIMEOUT,
            detail=f"Request timed out after {timeout} seconds",
        )
    except Exception as e:
        logger.error("Failed to process synchronous request", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process request",
        )


async def _wait_for_response(
    request_id: str, timeout: int, db: AsyncSession
) -> Dict[str, Any]:
    """Wait for agent response by polling the database."""
    import asyncio
    from datetime import datetime, timedelta

    from shared_db.models import RequestLog
    from sqlalchemy import select

    start_time = datetime.now()
    end_time = start_time + timedelta(seconds=timeout)

    while datetime.now() < end_time:
        # Use a fresh query with explicit session refresh to see committed changes
        await db.rollback()  # Clear any pending transaction

        # Check if response has been received
        stmt = select(RequestLog).where(RequestLog.request_id == request_id)
        result = await db.execute(stmt)
        request_log = result.scalar_one_or_none()

        if request_log:
            logger.debug(
                "Polling database for response",
                request_id=request_id,
                has_response_content=bool(request_log.response_content),
                response_content_length=(
                    len(request_log.response_content)
                    if request_log.response_content
                    else 0
                ),
                completed_at=request_log.completed_at,
            )

            if request_log.response_content:
                logger.info(
                    "Response received for synchronous request",
                    request_id=request_id,
                    elapsed_seconds=(datetime.now() - start_time).total_seconds(),
                )
                return {
                    "agent_id": request_log.agent_id,
                    "content": request_log.response_content,
                    "metadata": request_log.response_metadata or {},
                    "processing_time_ms": request_log.processing_time_ms,
                    "completed_at": (
                        request_log.completed_at.isoformat()
                        if request_log.completed_at
                        else None
                    ),
                }
        else:
            logger.debug(
                "Request log not found yet",
                request_id=request_id,
                elapsed_seconds=(datetime.now() - start_time).total_seconds(),
            )

        # Wait before polling again
        await asyncio.sleep(1)

    # Timeout reached
    raise asyncio.TimeoutError(f"No response received within {timeout} seconds")


async def _handle_agent_response_event(
    headers: Dict[str, str], body: bytes, db: AsyncSession
) -> Dict[str, Any]:
    """Handle agent response CloudEvent."""
    import json

    try:
        event_data = json.loads(body)

        # Extract response information
        request_id = event_data.get("request_id")
        session_id = event_data.get("session_id")
        agent_id = event_data.get("agent_id")
        content = event_data.get("content")

        if not all([request_id, session_id, content]):
            raise ValueError("Missing required fields in agent response")

        # Update request log with response
        stmt = (
            update(RequestLog)
            .where(RequestLog.request_id == request_id)
            .values(
                response_content=content,
                response_metadata=event_data.get("metadata", {}),
                agent_id=agent_id,
                processing_time_ms=event_data.get("processing_time_ms"),
                completed_at=datetime.now(timezone.utc),
                cloudevent_id=headers.get("ce-id"),
                cloudevent_type=headers.get("ce-type"),
            )
        )

        await db.execute(stmt)
        await db.commit()

        # Here you would typically forward the response back to the integration
        # For now, we'll just log it
        logger.info(
            "Agent response received",
            request_id=request_id,
            session_id=session_id,
            agent_id=agent_id,
        )

        return {"status": "processed", "request_id": request_id}

    except Exception as e:
        logger.error("Failed to handle agent response event", error=str(e))
        raise


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Handle HTTP exceptions."""
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(
            error=exc.detail,
            error_code=f"HTTP_{exc.status_code}",
            timestamp=datetime.now(timezone.utc),
        ).model_dump(mode="json"),
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle general exceptions."""
    logger.error("Unhandled exception", error=str(exc), path=str(request.url))

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=ErrorResponse(
            error="Internal server error",
            error_code="INTERNAL_ERROR",
            timestamp=datetime.now(timezone.utc),
        ).model_dump(mode="json"),
    )


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8080"))
    host = os.getenv("HOST", "0.0.0.0")

    uvicorn.run(
        "request_manager.main:app",
        host=host,
        port=port,
        reload=os.getenv("RELOAD", "false").lower() == "true",
        log_level=os.getenv("LOG_LEVEL", "INFO").lower(),
    )
