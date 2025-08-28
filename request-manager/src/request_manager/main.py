"""Main FastAPI application for Request Manager."""

import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Dict, List, Union

import structlog
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from . import __version__
from .database import get_database_manager, get_db_session
from .events import EventTypes, get_event_publisher
from .models import IntegrationType, RequestLog
from .normalizer import RequestNormalizer
from .schemas import (
    AgentResponse,
    BaseRequest,
    CLIRequest,
    CloudEventRequest,
    CloudEventResponse,
    ErrorResponse,
    HealthCheck,
    SessionCreate,
    SessionResponse,
    SlackRequest,
    ToolRequest,
    WebRequest,
)
from .session_manager import SessionManager

# Configure structured logging
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
    
    # Initialize database
    db_manager = get_database_manager()
    try:
        await db_manager.create_tables()
        logger.info("Database tables created/verified")
    except Exception as e:
        logger.error("Failed to initialize database", error=str(e))
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


@app.get("/health", response_model=HealthCheck)
async def health_check(db: AsyncSession = Depends(get_db_session)) -> HealthCheck:
    """Health check endpoint."""
    try:
        # Test database connection
        await db.execute("SELECT 1")
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
                "integration_type": session.integration_type.value,
                "created_at": session.created_at.isoformat(),
            },
        )
        
        logger.info(
            "Session created",
            session_id=session.session_id,
            user_id=session.user_id,
            integration_type=session.integration_type.value,
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
    request: SlackRequest,
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Handle Slack integration requests."""
    return await _process_request(request, db)


@app.post("/api/v1/requests/web")
async def handle_web_request(
    request: WebRequest,
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Handle web interface requests."""
    return await _process_request(request, db)


@app.post("/api/v1/requests/cli")
async def handle_cli_request(
    request: CLIRequest,
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Handle CLI requests."""
    return await _process_request(request, db)


@app.post("/api/v1/requests/tool")
async def handle_tool_request(
    request: ToolRequest,
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Handle tool-generated requests."""
    return await _process_request(request, db)


@app.post("/api/v1/requests/generic")
async def handle_generic_request(
    request: BaseRequest,
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    """Handle generic requests."""
    return await _process_request(request, db)


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
            normalized_request=normalized_request.dict(),
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
            integration_type=request.integration_type.value,
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
        from sqlalchemy import select, update
        
        stmt = (
            update(RequestLog)
            .where(RequestLog.request_id == request_id)
            .values(
                response_content=content,
                response_metadata=event_data.get("metadata", {}),
                agent_id=agent_id,
                processing_time_ms=event_data.get("processing_time_ms"),
                completed_at=datetime.utcnow(),
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
            timestamp=datetime.utcnow(),
        ).dict(),
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
            timestamp=datetime.utcnow(),
        ).dict(),
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
        log_level="info",
    )
