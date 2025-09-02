"""CloudEvent-driven Agent Service."""

import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx
import structlog
from cloudevents.http import CloudEvent, to_structured
from fastapi import FastAPI, HTTPException, Request, status
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
        self.broker_url = os.getenv("BROKER_URL")
        if not self.broker_url:
            raise ValueError(
                "BROKER_URL environment variable is required but not set. "
                "This should be configured by Helm deployment."
            )
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
    user_id: str  # Add user_id to track which user the response is for
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
        self.agent_name_to_id: Dict[str, str] = {}  # agent_name -> agent_id

    async def initialize(self) -> None:
        """Initialize the agent service."""
        try:
            # Create HTTP client that forces HTTP without TLS
            import httpx

            http_client = httpx.Client(
                verify=False,  # Disable SSL verification
                timeout=30.0,
                follow_redirects=True,
            )

            # Initialize LlamaStackClient with explicit HTTP client
            self.client = LlamaStackClient(
                base_url=self.config.llama_stack_url, http_client=http_client
            )
            logger.info("Connected to Llama Stack", url=self.config.llama_stack_url)

            # Build initial agent name to ID mapping
            await self._build_agent_mapping()
        except Exception as e:
            logger.error("Failed to connect to Llama Stack", error=str(e))
            raise

    async def _build_agent_mapping(self) -> None:
        """Build mapping from agent names to agent IDs."""
        try:
            logger.info("Building agent name to ID mapping...")
            agents_response = self.client.agents.list()
            self.agent_name_to_id = {}

            logger.info(
                "Retrieved agents from LlamaStack", count=len(agents_response.data)
            )

            for agent in agents_response.data:
                # Handle both dict and object formats
                if isinstance(agent, dict):
                    agent_name = agent.get("agent_config", {}).get("name") or agent.get(
                        "name", "unknown"
                    )
                    agent_id = agent.get("agent_id", agent.get("id", "unknown"))
                else:
                    agent_name = (
                        getattr(agent.agent_config, "name", "unknown")
                        if hasattr(agent, "agent_config")
                        else getattr(agent, "name", "unknown")
                    )
                    agent_id = getattr(
                        agent, "agent_id", getattr(agent, "id", "unknown")
                    )

                self.agent_name_to_id[agent_name] = agent_id
                logger.info("Mapped agent", name=agent_name, id=agent_id)

            logger.info(
                "Agent mapping completed", total_agents=len(self.agent_name_to_id)
            )

        except Exception as e:
            logger.error(
                "Failed to build agent mapping",
                error=str(e),
                error_type=type(e).__name__,
            )
            self.agent_name_to_id = {}
            # Continue initialization even if mapping fails

    async def process_request(self, request: NormalizedRequest) -> AgentResponse:
        """Process a normalized request and return agent response."""
        start_time = datetime.now(timezone.utc)

        try:
            # Publish processing started event for user notification
            await self._publish_processing_event(request)

            # Determine which agent to use
            agent_id = await self._determine_agent(request)

            # Get or create session
            llama_stack_session_id = await self._get_or_create_session(
                request.session_id, agent_id
            )

            # Send message to agent
            messages = [{"role": "user", "content": request.content}]

            # Use streaming agent turns (required by LlamaStack)
            response_stream = self.client.agents.turn.create(
                agent_id=agent_id,
                session_id=llama_stack_session_id,
                messages=messages,
                stream=True,  # Enable streaming
            )

            # Collect streaming response using the same pattern as test/chat.py
            content = ""
            chunk_count = 0
            for chunk in response_stream:
                chunk_count += 1
                logger.debug(
                    "Processing stream chunk",
                    chunk_type=type(chunk).__name__,
                    chunk_count=chunk_count,
                )

                # Pattern from test/chat.py - check for turn_complete events
                if hasattr(chunk, "event") and hasattr(chunk.event, "payload"):
                    payload = chunk.event.payload
                    if (
                        hasattr(payload, "event_type")
                        and payload.event_type == "turn_complete"
                    ):
                        if hasattr(payload, "turn") and hasattr(
                            payload.turn, "output_message"
                        ):
                            output_message = payload.turn.output_message
                            if (
                                hasattr(output_message, "stop_reason")
                                and output_message.stop_reason == "end_of_turn"
                            ):
                                if hasattr(output_message, "content"):
                                    content += output_message.content
                                    logger.debug(
                                        "Extracted content from turn_complete",
                                        content_length=len(output_message.content),
                                    )
                            else:
                                # Handle other stop reasons
                                if hasattr(output_message, "stop_reason"):
                                    content += (
                                        f"[Agent stopped: {output_message.stop_reason}]"
                                    )
                                    logger.debug(
                                        "Agent stopped with reason",
                                        stop_reason=output_message.stop_reason,
                                    )
                    else:
                        # Log other event types for debugging
                        event_type = getattr(payload, "event_type", "unknown")
                        logger.debug("Other event type", event_type=event_type)
                else:
                    # Log unexpected chunk structure for debugging
                    logger.debug("Unexpected chunk structure", chunk_attrs=dir(chunk))

            # If no content collected, provide error message instead of stream object
            if not content:
                content = f"No response content received from agent (processed {chunk_count} chunks). This may indicate the agent didn't complete its turn or the stream format has changed."
                logger.warning(
                    "No content collected from stream",
                    chunk_count=chunk_count,
                    message="Check if agent completed its turn successfully",
                )

            # Calculate processing time
            processing_time = int(
                (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            )

            return AgentResponse(
                request_id=request.request_id,
                session_id=request.session_id,
                user_id=request.user_id,  # Include user_id from the request
                agent_id=agent_id,
                content=content,
                processing_time_ms=processing_time,
                created_at=datetime.now(timezone.utc),
            )

        except Exception as e:
            logger.error(
                "Failed to process request", error=str(e), request_id=request.request_id
            )

            # Return error response
            return AgentResponse(
                request_id=request.request_id,
                session_id=request.session_id,
                user_id=request.user_id,  # Include user_id from the request
                agent_id=None,
                content=f"I apologize, but I encountered an error processing your request: {str(e)}",
                response_type="error",
                created_at=datetime.now(timezone.utc),
            )

    async def _determine_agent(self, request: NormalizedRequest) -> str:
        """Determine which agent should handle the request."""
        if request.target_agent_id:
            return request.target_agent_id

        agent_name = None
        if request.requires_routing:
            agent_name = self.config.default_agent_id
        else:
            # Default routing logic based on content
            content_lower = request.content.lower()

            if any(
                keyword in content_lower
                for keyword in ["laptop", "refresh", "computer"]
            ):
                agent_name = "laptop-refresh"
            elif any(
                keyword in content_lower for keyword in ["email", "address", "contact"]
            ):
                agent_name = "email-change"
            else:
                agent_name = self.config.default_agent_id

        # Always refresh agent mapping for each request to ensure we have latest agents
        logger.debug("Refreshing agent mapping for request", agent_name=agent_name)
        await self._build_agent_mapping()

        # Resolve agent name to ID
        logger.debug(
            "Resolving agent name to ID",
            agent_name=agent_name,
            available_agents=list(self.agent_name_to_id.keys()),
        )

        if agent_name in self.agent_name_to_id:
            agent_id = self.agent_name_to_id[agent_name]
            logger.info(
                "Resolved agent name to ID", agent_name=agent_name, agent_id=agent_id
            )
            return agent_id
        else:
            available_agents = list(self.agent_name_to_id.keys())
            logger.error(
                "Agent name not found in mapping",
                agent_name=agent_name,
                available_agents=available_agents,
            )
            raise ValueError(
                f"Agent '{agent_name}' not found in llama-stack. "
                f"Available agents: {available_agents}. "
                f"Please check agent configuration or create the agent in llama-stack."
            )

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

    async def publish_response(self, response: AgentResponse) -> bool:
        """Publish agent response as CloudEvent."""
        try:
            event_data = {
                "request_id": response.request_id,
                "session_id": response.session_id,
                "user_id": response.user_id,  # Include user_id for Integration Dispatcher
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
                    "specversion": "1.0",
                    "type": "com.self-service-agent.agent.response-ready",
                    "source": "agent-service",
                    "id": str(uuid.uuid4()),
                    "time": datetime.now(timezone.utc).isoformat(),
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

    async def _publish_processing_event(self, request: NormalizedRequest) -> bool:
        """Publish processing started event for user notification."""
        try:
            event_data = {
                "request_id": request.request_id,
                "session_id": request.session_id,
                "user_id": request.user_id,
                "integration_type": request.integration_type,
                "request_type": request.request_type,
                "content_preview": (
                    request.content[:100] + "..."
                    if len(request.content) > 100
                    else request.content
                ),
                "target_agent_id": request.target_agent_id,
                "started_at": datetime.now(timezone.utc).isoformat(),
            }

            event = CloudEvent(
                {
                    "specversion": "1.0",
                    "type": "com.self-service-agent.request.processing",
                    "source": "agent-service",
                    "id": str(uuid.uuid4()),
                    "time": datetime.now(timezone.utc).isoformat(),
                    "subject": f"session/{request.session_id}",
                    "datacontenttype": "application/json",
                },
                event_data,
            )

            headers, body = to_structured(event)

            response = await self.http_client.post(
                self.config.broker_url,
                headers=headers,
                content=body,
            )

            response.raise_for_status()

            logger.info(
                "Processing event published",
                request_id=request.request_id,
                session_id=request.session_id,
                user_id=request.user_id,
            )

            return True

        except Exception as e:
            logger.error("Failed to publish processing event", error=str(e))
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
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": __version__,
        "service": "agent-service",
    }


@app.post("/")
async def handle_cloudevent(request: Request) -> Dict[str, Any]:
    """Handle incoming CloudEvents."""
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
