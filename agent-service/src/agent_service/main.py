"""CloudEvent-driven Agent Service."""

import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Dict, Optional

import httpx
import structlog
from cloudevents.http import CloudEvent, to_structured
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from llama_stack_client import LlamaStackClient
from pydantic import BaseModel

from . import __version__

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


class AgentConfig:
    """Configuration for agent service."""

    def __init__(self) -> None:
        self.llama_stack_url = os.getenv("LLAMA_STACK_URL", "http://llamastack:8321")
        self.broker_url = os.getenv("BROKER_URL", "http://broker-ingress.knative-eventing.svc.cluster.local")
        self.default_agent_id = os.getenv("DEFAULT_AGENT_ID", "routing-agent")
        self.timeout = float(os.getenv("AGENT_TIMEOUT", "120"))


class NormalizedRequest(BaseModel):
    """Normalized request from CloudEvent."""

    request_id: str
    session_id: str
    user_id: str
    integration_type: str
    request_type: str
    content: str
    integration_context: Dict[str, Any]
    user_context: Dict[str, Any]
    target_agent_id: Optional[str] = None
    requires_routing: bool = True
    created_at: datetime


class AgentResponse(BaseModel):
    """Agent response model."""

    request_id: str
    session_id: str
    agent_id: Optional[str]
    content: str
    response_type: str = "message"
    metadata: Dict[str, Any] = {}
    processing_time_ms: Optional[int] = None
    requires_followup: bool = False
    followup_actions: list[str] = []
    created_at: datetime


class AgentService:
    """Service for handling agent interactions."""

    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.client: Optional[LlamaStackClient] = None
        self.http_client = httpx.AsyncClient(timeout=30.0)
        self.sessions: Dict[str, str] = {}  # session_id -> llama_stack_session_id

    async def initialize(self) -> None:
        """Initialize the agent service."""
        try:
            self.client = LlamaStackClient(base_url=self.config.llama_stack_url)
            logger.info("Connected to Llama Stack", url=self.config.llama_stack_url)
        except Exception as e:
            logger.error("Failed to connect to Llama Stack", error=str(e))
            raise

    async def process_request(self, request: NormalizedRequest) -> AgentResponse:
        """Process a normalized request and return agent response."""
        start_time = datetime.utcnow()
        
        try:
            # Determine which agent to use
            agent_id = self._determine_agent(request)
            
            # Get or create session
            llama_stack_session_id = await self._get_or_create_session(
                request.session_id, agent_id
            )
            
            # Send message to agent
            messages = [{"role": "user", "content": request.content}]
            
            response = self.client.agents.turn.create(
                agent_id=agent_id,
                session_id=llama_stack_session_id,
                messages=messages,
            )
            
            # Extract response content
            content = self._extract_response_content(response)
            
            # Calculate processing time
            processing_time = int((datetime.utcnow() - start_time).total_seconds() * 1000)
            
            return AgentResponse(
                request_id=request.request_id,
                session_id=request.session_id,
                agent_id=agent_id,
                content=content,
                processing_time_ms=processing_time,
                created_at=datetime.utcnow(),
            )
            
        except Exception as e:
            logger.error("Failed to process request", error=str(e), request_id=request.request_id)
            
            # Return error response
            return AgentResponse(
                request_id=request.request_id,
                session_id=request.session_id,
                agent_id=None,
                content=f"I apologize, but I encountered an error processing your request: {str(e)}",
                response_type="error",
                created_at=datetime.utcnow(),
            )

    def _determine_agent(self, request: NormalizedRequest) -> str:
        """Determine which agent should handle the request."""
        if request.target_agent_id:
            return request.target_agent_id
        
        if request.requires_routing:
            return self.config.default_agent_id
        
        # Default routing logic based on content
        content_lower = request.content.lower()
        
        if any(keyword in content_lower for keyword in ["laptop", "refresh", "computer"]):
            return "laptop-refresh-agent"
        elif any(keyword in content_lower for keyword in ["email", "address", "contact"]):
            return "email-change-agent"
        else:
            return self.config.default_agent_id

    async def _get_or_create_session(self, session_id: str, agent_id: str) -> str:
        """Get or create a Llama Stack session."""
        if session_id in self.sessions:
            return self.sessions[session_id]
        
        try:
            # Create new session
            session_response = self.client.agents.session.create(
                agent_id=agent_id,
                session_name=f"session-{session_id}",
            )
            
            llama_stack_session_id = session_response.session_id
            self.sessions[session_id] = llama_stack_session_id
            
            logger.info(
                "Created new agent session",
                session_id=session_id,
                llama_stack_session_id=llama_stack_session_id,
                agent_id=agent_id,
            )
            
            return llama_stack_session_id
            
        except Exception as e:
            logger.error("Failed to create agent session", error=str(e))
            raise

    def _extract_response_content(self, response) -> str:
        """Extract content from agent response."""
        if hasattr(response, 'agent_turn') and response.agent_turn:
            if hasattr(response.agent_turn, 'content'):
                return str(response.agent_turn.content)
        
        # Fallback - convert response to string
        return str(response)

    async def publish_response(self, response: AgentResponse) -> bool:
        """Publish agent response as CloudEvent."""
        try:
            event_data = {
                "request_id": response.request_id,
                "session_id": response.session_id,
                "agent_id": response.agent_id,
                "content": response.content,
                "response_type": response.response_type,
                "metadata": response.metadata,
                "processing_time_ms": response.processing_time_ms,
                "requires_followup": response.requires_followup,
                "followup_actions": response.followup_actions,
                "created_at": response.created_at.isoformat(),
            }

            event = CloudEvent(
                {
                    "type": "com.self-service-agent.agent.response-ready",
                    "source": "agent-service",
                    "id": str(uuid.uuid4()),
                    "time": datetime.utcnow().isoformat() + "Z",
                    "subject": f"session/{response.session_id}",
                    "datacontenttype": "application/json",
                },
                event_data,
            )

            headers, body = to_structured(event)
            
            response_http = await self.http_client.post(
                self.config.broker_url,
                headers=headers,
                content=body,
            )
            
            response_http.raise_for_status()
            return True
            
        except Exception as e:
            logger.error("Failed to publish response event", error=str(e))
            return False

    async def close(self) -> None:
        """Close HTTP client."""
        await self.http_client.aclose()


# Global agent service instance
_agent_service: Optional[AgentService] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    global _agent_service
    
    # Startup
    logger.info("Starting Agent Service", version=__version__)
    
    config = AgentConfig()
    _agent_service = AgentService(config)
    
    try:
        await _agent_service.initialize()
        logger.info("Agent Service initialized")
    except Exception as e:
        logger.error("Failed to initialize Agent Service", error=str(e))
        raise
    
    yield
    
    # Shutdown
    logger.info("Shutting down Agent Service")
    if _agent_service:
        await _agent_service.close()


# Create FastAPI application
app = FastAPI(
    title="Self-Service Agent Service",
    description="CloudEvent-driven Agent Service",
    version=__version__,
    lifespan=lifespan,
)


@app.get("/health")
async def health_check() -> Dict[str, Any]:
    """Health check endpoint."""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "version": __version__,
        "service": "agent-service",
    }


@app.post("/")
async def handle_cloudevent(request: Request) -> Dict[str, Any]:
    """Handle incoming CloudEvents."""
    global _agent_service
    
    if not _agent_service:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Agent service not initialized",
        )
    
    try:
        # Parse CloudEvent
        headers = dict(request.headers)
        body = await request.body()
        
        # Handle request events
        if headers.get("ce-type") == "com.self-service-agent.request.created":
            return await _handle_request_event(headers, body, _agent_service)
        
        logger.warning("Unhandled CloudEvent type", event_type=headers.get("ce-type"))
        return {"status": "ignored", "reason": "unhandled event type"}
        
    except Exception as e:
        logger.error("Failed to handle CloudEvent", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to process CloudEvent",
        )


async def _handle_request_event(
    headers: Dict[str, str], body: bytes, agent_service: AgentService
) -> Dict[str, Any]:
    """Handle request CloudEvent."""
    try:
        event_data = json.loads(body)
        
        # Parse normalized request
        request = NormalizedRequest(
            request_id=event_data["request_id"],
            session_id=event_data["session_id"],
            user_id=event_data["user_id"],
            integration_type=event_data["integration_type"],
            request_type=event_data["request_type"],
            content=event_data["content"],
            integration_context=event_data["integration_context"],
            user_context=event_data["user_context"],
            target_agent_id=event_data.get("target_agent_id"),
            requires_routing=event_data.get("requires_routing", True),
            created_at=datetime.fromisoformat(event_data["created_at"]),
        )
        
        # Process the request
        response = await agent_service.process_request(request)
        
        # Publish response event
        success = await agent_service.publish_response(response)
        
        logger.info(
            "Request processed",
            request_id=request.request_id,
            session_id=request.session_id,
            agent_id=response.agent_id,
            response_published=success,
        )
        
        return {
            "status": "processed",
            "request_id": request.request_id,
            "agent_id": response.agent_id,
            "response_published": success,
        }
        
    except Exception as e:
        logger.error("Failed to handle request event", error=str(e))
        raise


if __name__ == "__main__":
    import uvicorn
    
    port = int(os.getenv("PORT", "8080"))
    host = os.getenv("HOST", "0.0.0.0")
    
    uvicorn.run(
        "agent_service.main:app",
        host=host,
        port=port,
        reload=os.getenv("RELOAD", "false").lower() == "true",
        log_level="info",
    )
