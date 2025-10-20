"""CloudEvent-driven Agent Service."""

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, NewType, Optional

import httpx
from cloudevents.http import CloudEvent, to_structured
from fastapi import Depends, FastAPI, HTTPException, Request, status
from llama_stack_client import LlamaStackClient
from shared_clients.stream_processor import LlamaStackStreamProcessor
from shared_models import (
    AgentMapping,
    CloudEventBuilder,
    CloudEventHandler,
    EventTypes,
    configure_logging,
    create_agent_mapping,
    create_cloudevent_response,
    create_not_found_error,
    create_shared_lifespan,
    generate_fallback_user_id,
    get_database_manager,
    get_db_session_dependency,
    parse_cloudevent_from_request,
    simple_health_check,
)
from shared_models.models import AgentResponse, NormalizedRequest, SessionStatus
from sqlalchemy.ext.asyncio import AsyncSession

from . import __version__
from .schemas import SessionCreate, SessionResponse, SessionUpdate
from .session_manager import BaseSessionManager, ResponsesSessionManager

# Configure structured logging
logger = configure_logging("agent-service")

# Type aliases for clarity
RequestManagerSessionId = NewType("RequestManagerSessionId", str)
LlamaStackSessionId = NewType("LlamaStackSessionId", str)
AgentId = NewType("AgentId", str)


class AgentConfig:
    """Configuration for agent service."""

    def __init__(self) -> None:
        self.llama_stack_url = os.getenv("LLAMA_STACK_URL", "http://llamastack:8321")
        self.broker_url = os.getenv("BROKER_URL")
        self.eventing_enabled = os.getenv("EVENTING_ENABLED", "true").lower() == "true"

        # If eventing is disabled, we don't need a broker URL
        if self.eventing_enabled and not self.broker_url:
            raise ValueError(
                "BROKER_URL environment variable is required when eventing is enabled. "
                "Set EVENTING_ENABLED=false to disable eventing or configure BROKER_URL."
            )

        self.default_agent_id = os.getenv("DEFAULT_AGENT_ID", "routing-agent")
        self.timeout = float(os.getenv("AGENT_TIMEOUT", "120"))


class AgentService:
    """Service for handling agent interactions."""

    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.client: LlamaStackClient
        self.http_client = httpx.AsyncClient(timeout=30.0)
        self.agent_mapping: AgentMapping = create_agent_mapping(
            {}
        )  # Type-safe agent mapping
        self.always_refresh_mapping = (
            os.getenv("ALWAYS_REFRESH_AGENT_MAPPING", "true").lower() == "true"
        )
        # Request-level cache to avoid multiple mapping refreshes per request
        self._refreshed_requests: set[str] = set()
        # Cache cleanup threshold - clear cache when it gets too large
        self._max_cached_requests = 1000

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
            logger.debug("Connected to Llama Stack", url=self.config.llama_stack_url)

            # Build initial agent name to ID mapping
            await self._build_agent_mapping()
        except Exception as e:
            logger.error("Failed to connect to Llama Stack", exc_info=e)
            raise RuntimeError(f"AgentService initialization failed: {e}") from e

    def _is_reset_command(self, content: str) -> bool:
        """Check if the content is a reset command."""
        if not content:
            return False

        content_lower = content.strip().lower()
        reset_commands = ["reset", "clear", "restart", "new session"]
        return content_lower in reset_commands

    def _is_tokens_command(self, content: str) -> bool:
        """Check if the content is a tokens command."""
        if not content:
            return False

        content_lower = content.strip().lower()
        tokens_commands = ["**tokens**", "tokens", "token stats", "usage stats"]
        return content_lower in tokens_commands

    async def _handle_reset_command(self, request: NormalizedRequest) -> AgentResponse:
        """Handle reset command by clearing the session."""
        try:
            # Get database session for session management
            from shared_models import get_database_manager

            db_manager = get_database_manager()

            async with db_manager.get_session() as db:
                session_manager = BaseSessionManager(db)

                # Clear the session by setting it to INACTIVE
                await session_manager.update_session(
                    request.session_id,
                    status=SessionStatus.INACTIVE,
                    agent_id=None,
                    conversation_thread_id=None,
                )

                logger.info(
                    "Session reset completed",
                    session_id=request.session_id,
                    user_id=request.user_id,
                    integration_type=request.integration_type,
                )

                # Return a simple reset confirmation
                return self._create_system_response(
                    request=request,
                    content="Session cleared. Starting fresh!",
                )

        except Exception as e:
            logger.error(
                "Failed to reset session", error=str(e), session_id=request.session_id
            )
            return self._create_error_response(
                request=request,
                content="Failed to reset session. Please try again.",
            )

    async def _handle_tokens_command(self, request: NormalizedRequest) -> AgentResponse:
        """Handle tokens command by fetching token statistics from asset_manager."""
        try:
            from asset_manager.token_counter import get_token_stats

            # Use session-specific token stats, with fallback to global stats
            # The get_stats method now falls back to global stats when context doesn't exist
            from .session_manager import get_session_token_context

            token_context = get_session_token_context(request.session_id)

            # Debug logging to see what context is being used
            logger.debug(
                "Retrieving token stats",
                session_id=request.session_id,
                token_context=token_context,
            )

            context_stats = get_token_stats(context=token_context)

            # Format the response similar to the original chat.py
            token_summary = f"TOKEN_SUMMARY:INPUT:{context_stats.total_input_tokens}:OUTPUT:{context_stats.total_output_tokens}:TOTAL:{context_stats.total_tokens}:CALLS:{context_stats.call_count}:MAX_SINGLE_INPUT:{context_stats.max_input_tokens}:MAX_SINGLE_OUTPUT:{context_stats.max_output_tokens}:MAX_SINGLE_TOTAL:{context_stats.max_total_tokens}"

            logger.info(
                "Token statistics retrieved with fallback",
                request_id=request.request_id,
                session_id=request.session_id,
                token_context=token_context,
                total_tokens=context_stats.total_tokens,
                call_count=context_stats.call_count,
            )

            return self._create_agent_response(
                request=request,
                content=token_summary,
                agent_id="system",
                response_type="tokens",
                metadata={
                    "total_input_tokens": context_stats.total_input_tokens,
                    "total_output_tokens": context_stats.total_output_tokens,
                    "total_tokens": context_stats.total_tokens,
                    "call_count": context_stats.call_count,
                    "max_input_tokens": context_stats.max_input_tokens,
                    "max_output_tokens": context_stats.max_output_tokens,
                    "max_total_tokens": context_stats.max_total_tokens,
                },
                processing_time_ms=0,
            )

        except Exception as e:
            logger.error(
                "Failed to get token statistics",
                error=str(e),
                request_id=request.request_id,
            )
            return self._create_error_response(
                request=request,
                content="Failed to retrieve token statistics. Please try again.",
            )

    async def _build_agent_mapping(self, request_id: Optional[str] = None) -> None:
        """Build mapping from agent names to agent IDs.

        Args:
            request_id: Optional request ID for request-level caching.
                       If provided and mapping was already refreshed for this request,
                       the refresh will be skipped.
        """
        # Check if we've already refreshed for this request
        if request_id and request_id in self._refreshed_requests:
            logger.debug(
                "Skipping agent mapping refresh - already done for this request",
                request_id=request_id,
            )
            return

        try:
            logger.info("Building agent name to ID mapping...", request_id=request_id)
            agents_response = self.client.agents.list()
            raw_mapping = {}

            # Handle both dict and object formats from the API
            agents_data = agents_response.data

            logger.info("Retrieved agents from LlamaStack", count=len(agents_data))

            for agent in agents_data:
                # The API returns dict format, so we can safely access as dict
                agent_name = agent.get("agent_config", {}).get("name") or agent.get(  # type: ignore[attr-defined]
                    "name", "unknown"
                )
                agent_id = agent.get("agent_id", agent.get("id", "unknown"))

                raw_mapping[agent_name] = agent_id
                logger.debug("Mapped agent", name=agent_name, id=agent_id)

            # Create type-safe mapping
            self.agent_mapping = create_agent_mapping(dict(raw_mapping))  # type: ignore[arg-type]

            # Add to request cache if request_id provided
            if request_id:
                self._refreshed_requests.add(request_id)
                # Clean up cache if it gets too large
                if len(self._refreshed_requests) > self._max_cached_requests:
                    logger.debug(
                        "Clearing request cache to prevent memory leaks",
                        cache_size=len(self._refreshed_requests),
                    )
                    self._refreshed_requests.clear()

            logger.info(
                "Agent mapping completed",
                total_agents=len(self.agent_mapping.get_all_names()),
                request_id=request_id,
            )

        except Exception as e:
            logger.error(
                "Failed to build agent mapping",
                error=str(e),
                error_type=type(e).__name__,
            )
            self.agent_mapping = create_agent_mapping({})
            # Continue initialization even if mapping fails

    async def process_request(self, request: NormalizedRequest) -> AgentResponse:
        """Process a normalized request and return agent response."""
        return await self._process_request_core(request, skip_routing=False)

    async def _handle_agent_routing(
        self, agent_response: AgentResponse, request: NormalizedRequest
    ) -> AgentResponse:
        """Handle agent routing detection and processing."""
        from .routing import detect_and_validate_agent_routing

        # Get current agent name for routing detection
        if agent_response.agent_id is None:
            logger.error(
                "Agent response missing agent_id - this should not happen",
                request_id=request.request_id,
                session_id=request.session_id,
            )
            return agent_response  # Return as-is if we can't determine agent

        current_agent_name = self.agent_mapping.get_name(agent_response.agent_id)
        if not current_agent_name:
            logger.warning(
                "Could not find agent name for UUID, skipping routing detection",
                agent_uuid=agent_response.agent_id,
            )
            return agent_response

        # Always perform routing detection, but with different logic based on current agent
        is_routing_agent = (
            current_agent_name.lower() == self.config.default_agent_id.lower()
        )

        if not is_routing_agent:
            logger.debug(
                "Checking for task completion signal from specialist agent",
                current_agent=current_agent_name,
                request_id=request.request_id,
            )

        # Use existing agent mapping for routing detection (no HTTP call needed)
        # Check for routing signals
        routed_agent = await detect_and_validate_agent_routing(
            agent_response.content, current_agent_name, self.agent_mapping
        )

        if routed_agent:
            logger.info(
                "Agent routing detected - switching to target agent",
                from_agent=agent_response.agent_id,
                to_agent=routed_agent,
                request_id=request.request_id,
                is_routing_agent=is_routing_agent,
            )

            # Update session with the routed agent as current agent
            try:
                from shared_models import get_database_manager

                db_manager = get_database_manager()
                async with db_manager.get_session() as db_session:
                    session_manager = BaseSessionManager(db_session)

                    # Get the routed agent UUID for validation
                    routed_agent_uuid = self.agent_mapping.convert_to_uuid(routed_agent)

                    # Mark this as a fresh agent transition for introductory response
                    conversation_context = {
                        "agent_transition": True,
                        "previous_agent": agent_response.agent_id,
                        "new_agent": routed_agent,  # Store agent name for consistency
                        "agent_uuid": routed_agent_uuid,  # Store UUID for validation
                        "session_type": "llamastack_agent",
                        "transition_timestamp": datetime.now(timezone.utc).isoformat(),
                    }

                    updated_session = await session_manager.update_session(
                        session_id=request.session_id,
                        agent_id=routed_agent,  # Store agent name directly
                        conversation_context=conversation_context,
                    )

                    logger.info(
                        "Updated session with routed agent",
                        session_id=request.session_id,
                        agent_name=routed_agent,
                        update_successful=updated_session is not None,
                        request_id=request.request_id,
                    )
            except Exception as e:
                logger.error(
                    "Failed to update session after routing",
                    session_id=request.session_id,
                    routed_agent=routed_agent,
                    error=str(e),
                )

            # Create a direct request to the routed agent with the original user message
            # Bypass routing logic to prevent duplicate responses
            routed_request = request.model_copy()
            routed_request.target_agent_id = routed_agent
            routed_request.content = request.content  # Send original user message

            # Process the routed request directly without going through routing logic
            routed_response = await self._process_request_direct(routed_request)

            if routed_response:
                logger.info(
                    "Successfully routed to target agent with original message",
                    target_agent=routed_agent,
                    request_id=request.request_id,
                    final_agent_id=routed_response.agent_id,
                )
                # Return the specialist agent's response to the user's original question
                return routed_response
            else:
                logger.error(
                    "Failed to get response from routed agent",
                    target_agent=routed_agent,
                    request_id=request.request_id,
                )
                # Fallback to routing agent response if specialist fails
                return agent_response

        # No routing detected, return original response
        return agent_response

    async def _process_request_direct(
        self, request: NormalizedRequest
    ) -> AgentResponse:
        """Process a request directly without routing logic to prevent duplicate responses."""
        return await self._process_request_core(request, skip_routing=True)

    async def _process_request_core(
        self, request: NormalizedRequest, skip_routing: bool = False
    ) -> AgentResponse:
        """Core request processing logic shared between process_request and _process_request_direct."""
        start_time = datetime.now(timezone.utc)

        try:
            # Check for reset command first
            if not skip_routing and self._is_reset_command(request.content):
                return await self._handle_reset_command(request)

            # Check for tokens command
            if not skip_routing and self._is_tokens_command(request.content):
                return await self._handle_tokens_command(request)

            # Publish processing started event for user notification (only for main requests)
            if not skip_routing:
                await self._publish_processing_event(request)

            # Check if responses mode is requested
            if request.use_responses:
                logger.info(
                    "Responses mode requested, delegating to responses session manager",
                    request_id=request.request_id,
                    session_id=request.session_id,
                )
                return await self._handle_responses_mode_request(request, start_time)

            # Determine which agent to use for traditional agent mode
            agent_id = await self._determine_agent(request)
            logger.info(
                "Agent determined for request",
                request_id=request.request_id,
                target_agent_id=request.target_agent_id,
                resolved_agent_id=agent_id,
            )

            # Handle session management (increment request count) for both eventing and direct HTTP modes
            await self._handle_session_management(
                request.session_id, request.request_id
            )

            # Get or create LlamaStack session for this specific agent
            llama_stack_session_id = await self._get_or_create_llama_stack_session(
                RequestManagerSessionId(request.session_id), AgentId(agent_id)
            )

            # Send message to agent
            messages = [{"role": "user", "content": request.content}]

            # Get full agent configuration to ensure all settings are applied
            agent = self.client.agents.retrieve(agent_id)
            agent_config = agent.agent_config

            # Debug logging for agent configuration
            logger.info(
                "Agent configuration retrieved",
                agent_id=agent_id,
                agent_name=agent.name if hasattr(agent, "name") else "unknown",
                toolgroups=(
                    agent_config.toolgroups
                    if hasattr(agent_config, "toolgroups")
                    else None
                ),
                tool_choice=(
                    getattr(agent_config.tool_config, "tool_choice", None)
                    if hasattr(agent_config, "tool_config") and agent_config.tool_config
                    else None
                ),
                sampling_params=(
                    agent_config.sampling_params
                    if hasattr(agent_config, "sampling_params")
                    else None
                ),
            )
            # Use streaming agent turns (required by LlamaStack)
            turn_params = {
                "agent_id": agent_id,
                "session_id": llama_stack_session_id,
                "messages": messages,
                "stream": True,  # Enable streaming
            }

            # Add toolgroups if available
            if agent_config.toolgroups:
                logger.info(
                    "Adding toolgroups to turn params",
                    agent_id=agent_id,
                    toolgroups=agent_config.toolgroups,
                )
                turn_params["toolgroups"] = agent_config.toolgroups
            else:
                logger.info("No toolgroups found for agent", agent_id=agent_id)

            response_stream = self.client.agents.turn.create(**turn_params)  # type: ignore[call-overload]

            # Use shared stream processor
            from shared_clients.stream_processor import LlamaStackStreamProcessor

            stream_result = await LlamaStackStreamProcessor.process_stream(
                response_stream,
                collect_content=True,
            )

            content = stream_result["content"]
            tool_calls_made = stream_result["tool_calls_made"]
            errors = stream_result["errors"]
            chunk_count = stream_result["chunk_count"]

            if errors:
                logger.error("Stream processing errors", errors=errors)

            # If no content collected, provide error message instead of stream object
            if not content:
                content = f"No response content received from agent (processed {chunk_count} chunks). This may indicate the agent didn't complete its turn or the stream format has changed."
                logger.warning(
                    "No content collected from stream",
                    chunk_count=chunk_count,
                    message="Check if agent completed its turn successfully",
                )

            # Log tool calls for monitoring (Strategy 3 only)
            if tool_calls_made:
                logger.info(
                    "Tools called during agent processing",
                    agent_id=agent_id,
                    tool_calls=tool_calls_made,
                )

            # Create response with automatic timing calculation
            agent_response = self._create_agent_response(
                request=request,
                content=stream_result["content"],
                agent_id=agent_id,
                metadata={
                    "tool_calls_made": stream_result["tool_calls_made"],
                    "chunk_count": stream_result["chunk_count"],
                    "errors": stream_result["errors"],
                },
                start_time=start_time,
            )

            # Handle agent routing detection if needed (only for main requests)
            if not skip_routing:
                final_response = await self._handle_agent_routing(
                    agent_response, request
                )
                return final_response
            else:
                return agent_response

        except Exception as e:
            logger.error(
                "Failed to process request", error=str(e), request_id=request.request_id
            )

            # Return error response
            return self._create_error_response(
                request=request,
                content=f"I apologize, but I encountered an error processing your request: {str(e)}",
                agent_id="unknown",
            )

    async def _determine_agent(self, request: NormalizedRequest) -> str:
        """Determine which agent should handle the request."""
        if request.target_agent_id:
            # Refresh agent mapping if configured to do so or if mapping is empty
            if (
                self.always_refresh_mapping
                or len(self.agent_mapping.get_all_names()) == 0
            ):
                logger.debug(
                    "Refreshing agent mapping for request",
                    agent_name=request.target_agent_id,
                )
                await self._build_agent_mapping(request_id=request.request_id)

            # Use simple conversion
            agent_uuid = self.agent_mapping.convert_to_uuid(request.target_agent_id)
            if agent_uuid:
                logger.info(
                    "Resolved target agent name to ID",
                    agent_name=request.target_agent_id,
                    agent_id=agent_uuid,
                )
                return str(agent_uuid)
            else:
                available_agents = self.agent_mapping.get_all_names()
                logger.error(
                    "Target agent not found in mapping",
                    target_agent_id=request.target_agent_id,
                    available_agents=available_agents,
                )
                raise ValueError(
                    f"Target agent '{request.target_agent_id}' not found in llama-stack. "
                    f"Available agents: {available_agents}. "
                    f"Please check agent configuration."
                )

        # Check if session has a current agent assigned
        try:
            from shared_models import get_database_manager

            db_manager = get_database_manager()
            async with db_manager.get_session() as db_session:
                session_manager = BaseSessionManager(db_session)
                session = await session_manager.get_session(request.session_id)

                # Debug logging to understand what's happening
                logger.info(
                    "Session lookup result",
                    session_id=request.session_id,
                    session_found=session is not None,
                    current_agent_id=session.current_agent_id if session else None,
                    session_status=session.status if session else None,
                    session_data=session.model_dump() if session else None,
                )

                if session and session.current_agent_id:
                    # User is already assigned to a specific agent, use that agent
                    await self._build_agent_mapping(request_id=request.request_id)

                    # Use the existing agent mapping conversion method
                    stored_agent_id = session.current_agent_id
                    agent_uuid = self.agent_mapping.convert_to_uuid(stored_agent_id)

                    # Check if the agent actually exists in the mapping (not just if it's a valid UUID)
                    if agent_uuid and self.agent_mapping.get_name(agent_uuid):
                        logger.info(
                            "Using session's current agent",
                            session_id=request.session_id,
                            current_agent=stored_agent_id,
                            agent_id=agent_uuid,
                        )
                        return str(agent_uuid)
                    else:
                        logger.warning(
                            "Session's current agent not found in mapping, resetting to routing agent",
                            session_id=request.session_id,
                            current_agent=stored_agent_id,
                        )

                        # Reset session to routing agent to fix invalid agent UUID
                        try:
                            default_agent_uuid = self.agent_mapping.convert_to_uuid(
                                self.config.default_agent_id
                            )
                            if default_agent_uuid:
                                await session_manager.update_session(
                                    session_id=request.session_id,
                                    agent_id=default_agent_uuid,
                                )
                                logger.info(
                                    "Reset session to routing agent due to invalid agent UUID",
                                    session_id=request.session_id,
                                    old_agent=stored_agent_id,
                                    new_agent=default_agent_uuid,
                                )
                        except Exception as e:
                            logger.error(
                                "Failed to reset session to routing agent",
                                session_id=request.session_id,
                                error=str(e),
                            )
                else:
                    logger.info(
                        "No current agent in session, will use routing agent",
                        session_id=request.session_id,
                        session_found=session is not None,
                        current_agent_id=session.current_agent_id if session else None,
                    )
        except Exception as e:
            logger.warning(
                "Failed to check session current agent, falling back to routing agent",
                session_id=request.session_id,
                error=str(e),
            )

        # Use routing agent for all requests unless a specific target agent is provided
        agent_name = self.config.default_agent_id

        # Always refresh agent mapping to ensure we have the latest agents from LlamaStack
        logger.debug("Refreshing agent mapping for request", agent_name=agent_name)
        await self._build_agent_mapping(request_id=request.request_id)

        # Resolve agent name to ID using simple conversion
        logger.debug(
            "Resolving agent name to ID",
            agent_name=agent_name,
            available_agents=self.agent_mapping.get_all_names(),
        )

        agent_uuid = self.agent_mapping.convert_to_uuid(agent_name)
        if agent_uuid:
            logger.info(
                "Resolved agent name to ID", agent_name=agent_name, agent_id=agent_uuid
            )
            return str(agent_uuid)
        else:
            available_agents = self.agent_mapping.get_all_names()
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

    async def _get_or_create_llama_stack_session(
        self, request_manager_session_id: RequestManagerSessionId, agent_id: AgentId
    ) -> LlamaStackSessionId:
        """Get or create a LlamaStack session for a specific agent.

        This method handles LlamaStack session management (AI conversation history)
        which is separate from Request Manager session management (database context).

        Args:
            request_manager_session_id: The Request Manager session ID (user conversation context)
            agent_id: The agent ID for which to create/get the LlamaStack session

        Returns:
            The LlamaStack session ID for this agent

        Note: This method also updates the Request Manager session to store the
        LlamaStack session ID reference, but the actual LlamaStack session
        management is handled via LlamaStackClient API calls.
        """
        # Get database session for persistent storage
        db_manager = get_database_manager()
        async with db_manager.get_session() as db:
            session_manager = BaseSessionManager(db)

            # Try to get existing session from database
            existing_session = await session_manager.get_session(
                request_manager_session_id
            )

            if existing_session and existing_session.conversation_thread_id:
                # Check if agent UUID has changed (agent was recreated)
                stored_uuid = None
                if existing_session.conversation_context:
                    stored_uuid = existing_session.conversation_context.get(
                        "agent_uuid"
                    )

                if stored_uuid and stored_uuid != agent_id:
                    logger.warning(
                        "Agent UUID has changed, agent was likely recreated",
                        request_manager_session_id=request_manager_session_id,
                        stored_uuid=stored_uuid,
                        current_uuid=agent_id,
                        agent_name=existing_session.current_agent_id,
                    )
                else:
                    # Verify the session still exists in LlamaStack
                    try:
                        self.client.agents.session.retrieve(
                            agent_id=agent_id,
                            session_id=existing_session.conversation_thread_id,
                        )
                        logger.info(
                            "Reusing existing LlamaStack session from database",
                            request_manager_session_id=request_manager_session_id,
                            agent_id=agent_id,
                            llama_stack_session_id=existing_session.conversation_thread_id,
                        )
                        return LlamaStackSessionId(
                            existing_session.conversation_thread_id
                        )
                    except Exception as e:
                        logger.warning(
                            "Database session no longer valid in LlamaStack, will create new one",
                            request_manager_session_id=request_manager_session_id,
                            agent_id=agent_id,
                            session_id=existing_session.conversation_thread_id,
                            error=str(e),
                        )

        try:
            # Create new session
            session_response = self.client.agents.session.create(
                agent_id=agent_id,
                session_name=f"session-{request_manager_session_id}",
            )

            llama_stack_session_id = session_response.session_id

            # Store the LlamaStack session ID in the database
            async with db_manager.get_session() as db:
                session_manager = BaseSessionManager(db)
                # Get agent name for consistent storage
                agent_name = self.agent_mapping.get_name(agent_id) or agent_id

                # Store both agent name and UUID for validation
                conversation_context = {
                    "agent_name": agent_name,
                    "agent_uuid": agent_id,
                    "session_type": "llamastack_agent",
                }

                await session_manager.update_session(
                    session_id=request_manager_session_id,
                    agent_id=agent_name,  # Store agent name for readability
                    conversation_thread_id=llama_stack_session_id,
                    conversation_context=conversation_context,  # Store UUID for validation
                )

            logger.info(
                "Created new agent session and stored in database",
                request_manager_session_id=request_manager_session_id,
                llama_stack_session_id=llama_stack_session_id,
                agent_id=agent_id,
            )

            return LlamaStackSessionId(llama_stack_session_id)

        except Exception as e:
            logger.error("Failed to create agent session", exc_info=e)
            raise

    async def publish_response(self, response: AgentResponse) -> bool:
        """Publish agent response as CloudEvent and update database."""
        # Skip event publishing if eventing is disabled
        if not self.config.eventing_enabled:
            logger.debug("Eventing disabled, skipping response event publication")
            return True

        try:
            # Debug log the response object to see what values it has
            logger.debug(
                "AgentResponse object details",
                request_id=response.request_id,
                session_id=response.session_id,
                user_id=response.user_id,
                agent_id=response.agent_id,
                content_preview=response.content[:100] if response.content else "None",
                response_type=response.response_type,
                processing_time_ms=response.processing_time_ms,
            )

            # Update RequestLog in database first (for CLI sync requests)
            logger.info(
                "Updating RequestLog in database", request_id=response.request_id
            )
            await self._update_request_log(response)
            logger.info(
                "RequestLog updated successfully", request_id=response.request_id
            )

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

            # Debug log the event_data to see what values are being sent
            logger.debug(
                "Event data being published",
                event_data_keys=list(event_data.keys()),
                event_data_values={
                    key: value
                    for key, value in event_data.items()
                    if key
                    in ["request_id", "session_id", "user_id", "agent_id", "content"]
                },
                response_id=response.request_id,
            )

            logger.debug(
                "Publishing agent response event",
                event_data=event_data,
                response_id=response.request_id,
            )

            # Use shared CloudEvent builder with correct event type
            builder = CloudEventBuilder("agent-service")
            event = builder.create_response_event(
                event_data,
                response.request_id,
                response.agent_id,
                response.session_id,
            )

            headers, body = to_structured(event)

            if self.config.broker_url is None:
                logger.error("Broker URL not configured")
                return False

            response_http = await self.http_client.post(
                self.config.broker_url,
                headers=headers,
                content=body,
            )

            response_http.raise_for_status()
            return True

        except Exception as e:
            logger.error("Failed to publish response event", exc_info=e)
            return False

    def _create_agent_response(
        self,
        request: NormalizedRequest,
        content: str,
        agent_id: str,
        response_type: str = "message",
        metadata: Optional[Dict[str, Any]] = None,
        processing_time_ms: Optional[int] = None,
        start_time: Optional[datetime] = None,
        requires_followup: bool = False,
        followup_actions: Optional[List[str]] = None,
    ) -> AgentResponse:
        """Create an AgentResponse with consistent defaults.

        Args:
            request: The normalized request
            content: Response content
            agent_id: Agent identifier (required)
            response_type: Type of response (default: "message")
            metadata: Optional metadata dictionary
            processing_time_ms: Processing time in milliseconds (if None and start_time provided, will calculate)
            start_time: Start time for processing (used to calculate processing_time_ms if not provided)
            requires_followup: Whether response requires followup
            followup_actions: List of followup actions
        """
        # Calculate processing time if start_time provided and processing_time_ms not specified
        if processing_time_ms is None and start_time is not None:
            processing_time_ms = int(
                (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            )

        response = AgentResponse(
            request_id=request.request_id,
            session_id=request.session_id,
            user_id=request.user_id,
            agent_id=agent_id,
            content=content,
            response_type=response_type,
            metadata=metadata or {},
            processing_time_ms=processing_time_ms,
            requires_followup=requires_followup,
            followup_actions=followup_actions or [],
            created_at=datetime.now(timezone.utc),
        )
        return response

    def _create_error_response(
        self,
        request: NormalizedRequest,
        content: str,
        agent_id: str = "system",
        start_time: Optional[datetime] = None,
    ) -> AgentResponse:
        """Create an error response with common defaults."""
        return self._create_agent_response(
            request=request,
            content=content,
            agent_id=agent_id,
            response_type="error",
            processing_time_ms=0,
            start_time=start_time,
        )

    def _create_system_response(
        self,
        request: NormalizedRequest,
        content: str,
        start_time: Optional[datetime] = None,
    ) -> AgentResponse:
        """Create a system response with common defaults."""
        return self._create_agent_response(
            request=request,
            content=content,
            agent_id="system",
            processing_time_ms=0,
            start_time=start_time,
        )

    async def _update_request_log(self, response: AgentResponse) -> None:
        """Update RequestLog in database with response content."""
        if response.agent_id is None:
            logger.error(
                "Cannot update request log - response missing agent_id",
                request_id=response.request_id,
                session_id=response.session_id,
            )
            return

        await _update_request_log_unified(
            request_id=response.request_id,
            response_content=response.content,
            agent_id=response.agent_id,
            response_metadata=response.metadata,
            processing_time_ms=response.processing_time_ms,
            db=None,  # Will create its own database session
        )

    async def _handle_responses_mode_request(
        self, request: NormalizedRequest, start_time: datetime
    ) -> AgentResponse:
        """Handle responses mode requests using LangGraph session manager."""
        try:
            from shared_models import get_database_manager

            # Handle session management (increment request count) for responses mode
            await self._handle_session_management(
                request.session_id, request.request_id
            )

            # Get database session for responses session manager
            db_manager = get_database_manager()

            async with db_manager.get_session() as db:
                # Create responses session manager
                session_manager = ResponsesSessionManager(
                    db_session=db,
                    user_id=request.user_id,
                )

                # Process the message using responses mode with session-specific context
                response_content = await session_manager.handle_responses_message(
                    text=request.content,
                    request_manager_session_id=request.session_id,
                )

                # Create response with automatic timing calculation
                if session_manager.current_agent_name is None:
                    logger.error(
                        "Cannot create agent response - no agent assigned",
                        request_id=request.request_id,
                        session_id=request.session_id,
                    )
                    return self._create_error_response(
                        request=request,
                        content="Error: No agent assigned to handle this request",
                    )

                return self._create_agent_response(
                    request=request,
                    content=response_content,
                    agent_id=session_manager.current_agent_name,
                    start_time=start_time,
                )

        except Exception as e:
            logger.error(
                "Failed to handle responses mode request",
                error=str(e),
                request_id=request.request_id,
                session_id=request.session_id,
            )
            return self._create_error_response(
                request=request,
                content=f"Failed to process responses mode request: {str(e)}",
            )

    async def _publish_processing_event(self, request: NormalizedRequest) -> bool:
        """Publish processing started event for user notification."""
        # Skip event publishing if eventing is disabled
        if not self.config.eventing_enabled:
            logger.debug("Eventing disabled, skipping processing event publication")
            return True

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
                    "type": EventTypes.REQUEST_PROCESSING,
                    "source": "agent-service",
                    "id": str(uuid.uuid4()),
                    "time": datetime.now(timezone.utc).isoformat(),
                    "subject": f"session/{request.session_id}",
                    "datacontenttype": "application/json",
                },
                event_data,
            )

            headers, body = to_structured(event)

            if self.config.broker_url is None:
                logger.error("Broker URL not configured")
                return False

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
            logger.error("Failed to publish processing event", exc_info=e)
            return False

    async def _log_agent_config(self, agent_id: str) -> None:
        """Log agent configuration for debugging."""
        try:
            # Get agent details from llama-stack
            response = self.client.get("v1/agents", cast_to=httpx.Response)
            json_string = response.content.decode("utf-8")
            data = json.loads(json_string)

            # Find the agent with matching ID
            for agent in data.get("data", []):
                if isinstance(agent, dict) and agent.get("agent_id") == agent_id:
                    agent_config = agent.get("agent_config", {})
                    logger.info(
                        "Agent configuration in llama-stack",
                        agent_id=agent_id,
                        agent_name=agent.get("name"),
                        toolgroups=agent_config.get("toolgroups"),
                        tool_choice=agent_config.get("tool_config", {}).get(
                            "tool_choice"
                        ),
                        max_infer_iters=agent_config.get("max_infer_iters"),
                        sampling_params=agent_config.get("sampling_params"),
                    )
                    return

            logger.warning(
                "Agent configuration not found in llama-stack", agent_id=agent_id
            )
        except Exception as e:
            logger.error(
                "Failed to get agent configuration", agent_id=agent_id, error=str(e)
            )

    async def close(self) -> None:
        """Close HTTP client."""
        await self.http_client.aclose()

    async def _handle_session_management(
        self, session_id: str, request_id: str
    ) -> None:
        """Handle session management including request count increment.

        This method is called for both eventing and direct HTTP modes to ensure
        consistent session management across all communication strategies.
        """
        try:
            # Get database session for session management
            db_manager = get_database_manager()
            async with db_manager.get_session() as db:
                session_manager = BaseSessionManager(db)
                await session_manager.increment_request_count(session_id, request_id)

                logger.debug(
                    "Session management completed",
                    session_id=session_id,
                    request_id=request_id,
                )
        except Exception as e:
            logger.warning(
                "Failed to handle session management",
                session_id=session_id,
                request_id=request_id,
                error=str(e),
            )
            # Don't raise exception - session management failure shouldn't stop request processing


# Global agent service instance
_agent_service: Optional[AgentService] = None


async def _agent_service_startup() -> None:
    """Custom startup logic for Agent Service."""
    global _agent_service

    config = AgentConfig()
    _agent_service = AgentService(config)

    try:
        await _agent_service.initialize()
        logger.info("Agent Service initialized")
    except Exception as e:
        logger.error("Failed to initialize Agent Service", exc_info=e)
        raise


async def _agent_service_shutdown() -> None:
    """Custom shutdown logic for Agent Service."""
    global _agent_service

    if _agent_service:
        await _agent_service.close()
        _agent_service = None


# Create lifespan using shared utility with custom startup/shutdown
def lifespan(app: FastAPI) -> Any:
    return create_shared_lifespan(
        service_name="agent-service",
        version=__version__,
        custom_startup=_agent_service_startup,
        custom_shutdown=_agent_service_shutdown,
    )


# Create FastAPI application
app = FastAPI(
    title="Self-Service Agent Service",
    description="CloudEvent-driven Agent Service",
    version=__version__,
    lifespan=lifespan,
)


@app.get("/health")
async def health_check() -> Dict[str, Any]:
    """Health check endpoint - lightweight without database dependency."""
    return {
        "status": "healthy",
        "service": "agent-service",
        "version": __version__,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/health/detailed")
async def detailed_health_check(
    db: AsyncSession = Depends(get_db_session_dependency),
) -> Dict[str, Any]:
    """Detailed health check with database dependency for monitoring."""
    return dict(
        await simple_health_check(
            service_name="agent-service",
            version=__version__,
            db=db,
        )
    )


@app.get("/agents")
async def list_agents() -> Dict[str, Any]:
    """List available agents endpoint."""
    if not _agent_service:
        raise create_not_found_error("Agent service not initialized")

    try:
        # Refresh agent mapping to get latest agents
        await _agent_service._build_agent_mapping()

        return {
            "agents": _agent_service.agent_mapping.to_dict(),
            "count": len(_agent_service.agent_mapping.get_all_names()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error("Failed to get agent list", exc_info=e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve agent list",
        )


@app.post("/process")
async def handle_direct_request(
    request: Request, stream: bool = False
) -> Any:  # Returns StreamingResponse or JSONResponse
    """Handle direct HTTP requests with optional streaming support."""
    if not _agent_service:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Agent service not initialized",
        )

    try:
        body = await request.body()
        request_data = json.loads(body)

        logger.info(
            "Direct request received",
            request_id=request_data.get("request_id"),
            session_id=request_data.get("session_id"),
            user_id=request_data.get("user_id"),
            stream=stream,
        )

        # Create a normalized request for processing
        normalized_request = _create_normalized_request_from_data(request_data)

        if stream:
            # Return streaming response
            async def generate_stream() -> Any:
                try:
                    # Process the request using the agent service
                    agent_response = await _agent_service.process_request(
                        normalized_request
                    )

                    # Stream the response as Server-Sent Events using shared utilities
                    yield LlamaStackStreamProcessor.create_sse_start_event(
                        agent_response.request_id
                    )

                    # Stream the content using optimized streaming
                    async for (
                        chunk_data
                    ) in LlamaStackStreamProcessor.stream_content_optimized(
                        agent_response.content,
                        content_type="content",
                    ):
                        yield chunk_data

                    # Send completion event
                    if (
                        agent_response.agent_id is None
                        or agent_response.processing_time_ms is None
                    ):
                        logger.error(
                            "Cannot send completion event - missing agent_id or processing_time_ms",
                            request_id=agent_response.request_id,
                            session_id=agent_response.session_id,
                            agent_id=agent_response.agent_id,
                            processing_time_ms=agent_response.processing_time_ms,
                        )
                        yield LlamaStackStreamProcessor.create_sse_error_event(
                            "Missing required response data"
                        )
                    else:
                        yield LlamaStackStreamProcessor.create_sse_complete_event(
                            agent_response.agent_id,
                            agent_response.processing_time_ms,
                        )

                except Exception as e:
                    logger.error("Error in streaming response", exc_info=e)
                    yield LlamaStackStreamProcessor.create_sse_error_event(str(e))

            return LlamaStackStreamProcessor.create_sse_response(generate_stream())
        else:
            # Return JSON response using existing CloudEvent handler
            result = await _handle_request_event_from_data(
                {"data": request_data}, _agent_service
            )
            return result

    except json.JSONDecodeError:
        logger.error("Invalid JSON in direct request")
        raise HTTPException(status_code=400, detail="Invalid JSON")
    except Exception as e:
        logger.error("Error handling direct request", exc_info=e)
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/process/stream")
async def handle_direct_request_stream(
    request: Request,
) -> Any:  # Returns StreamingResponse
    """Handle direct HTTP requests with streaming responses (legacy endpoint)."""
    # Redirect to unified endpoint with streaming enabled
    return await handle_direct_request(request, stream=True)


@app.post("/api/v1/events/cloudevents")
async def handle_cloudevent(request: Request) -> Dict[str, Any]:
    """Handle incoming CloudEvents."""
    if not _agent_service:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Agent service not initialized",
        )

    try:
        # Parse CloudEvent from request using shared utility
        event_data = await parse_cloudevent_from_request(request)

        event_type = event_data.get("type")

        # Handle request events
        if event_type == EventTypes.REQUEST_CREATED:
            return await _handle_request_event_from_data(event_data, _agent_service)

        # Handle responses mode events
        if event_type == EventTypes.RESPONSES_REQUEST_CREATED:
            return await _handle_responses_request_event_from_data(
                event_data, _agent_service
            )

        # Handle database update events
        if event_type == EventTypes.DATABASE_UPDATE_REQUESTED:
            return await _handle_database_update_event_from_data(
                event_data, _agent_service
            )

        logger.warning("Unhandled CloudEvent type", event_type=event_type)
        return dict(
            await create_cloudevent_response(
                status="ignored",
                message="Unhandled event type",
                details={"event_type": event_type},
            )
        )

    except Exception as e:
        logger.error("Failed to handle CloudEvent", exc_info=e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to process CloudEvent",
        )


def _create_normalized_request_from_data(
    request_data: Dict[str, Any],
) -> NormalizedRequest:
    """Create a NormalizedRequest from request data with proper validation."""
    # Validate required fields
    required_fields = [
        "request_id",
        "session_id",
        "user_id",
        "integration_type",
        "request_type",
        "content",
    ]
    missing_fields = [field for field in required_fields if not request_data.get(field)]

    if missing_fields:
        raise ValueError(f"Missing required fields in request data: {missing_fields}")

    return NormalizedRequest(
        request_id=request_data["request_id"],
        session_id=request_data["session_id"],
        user_id=request_data["user_id"],
        integration_type=request_data["integration_type"],
        request_type=request_data["request_type"],
        content=request_data["content"],
        integration_context=request_data.get("integration_context", {}),
        user_context=request_data.get("user_context", {}),
        target_agent_id=request_data.get("target_agent_id"),
        requires_routing=request_data.get("requires_routing", True),
        use_responses=request_data.get("use_responses", True),
        created_at=datetime.fromisoformat(
            request_data.get("created_at", datetime.now().isoformat())
        ),
    )


async def _handle_request_event_from_data(
    event_data: Dict[str, Any], agent_service: AgentService
) -> Dict[str, Any]:
    """Handle request CloudEvent using pre-parsed event data."""
    try:
        # Extract event data using common utility
        request_data = CloudEventHandler.extract_event_data(event_data)

        # Parse normalized request with proper error handling
        try:
            request = _create_normalized_request_from_data(request_data)

            logger.debug(
                "Created NormalizedRequest",
                request_id=request.request_id,
                session_id=request.session_id,
                user_id=request.user_id,
                content_preview=request.content[:100] if request.content else "empty",
            )
        except Exception as e:
            logger.error(
                "Failed to parse normalized request",
                error=str(e),
                event_data=event_data,
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid request data: {str(e)}",
            )

        # Process the request
        logger.debug("Calling agent_service.process_request")
        response = await agent_service.process_request(request)

        logger.debug(
            "Agent response created",
            response_id=response.request_id if response else "None",
            response_type=type(response).__name__ if response else "None",
        )

        # Publish response event only if eventing is enabled
        success = True
        if agent_service.config.eventing_enabled:
            logger.debug("Eventing enabled - publishing response")
            success = await agent_service.publish_response(response)
        else:
            logger.debug("Skipping response event publishing - eventing disabled")

        logger.info(
            "Request processed",
            request_id=request.request_id,
            session_id=request.session_id,
            agent_id=response.agent_id,
            response_published=success,
        )

        return {
            "request_id": response.request_id,
            "session_id": response.session_id,
            "user_id": response.user_id,
            "agent_id": response.agent_id,
            "content": response.content,
            "response_type": response.response_type,
            "metadata": response.metadata,
            "processing_time_ms": response.processing_time_ms,
            "requires_followup": response.requires_followup,
            "followup_actions": response.followup_actions,
            "created_at": response.created_at.isoformat(),
        }

    except Exception as e:
        logger.error("Failed to handle request event", exc_info=e)
        raise


async def _handle_responses_request_event_from_data(
    event_data: Dict[str, Any], agent_service: AgentService
) -> Dict[str, Any]:
    """Handle responses request CloudEvent using pre-parsed event data."""
    start_time = datetime.now(timezone.utc)
    try:
        # Extract event data using common utility
        request_data = CloudEventHandler.extract_event_data(event_data)

        # Extract required fields
        request_manager_session_id = request_data.get("request_manager_session_id")
        user_id = request_data.get("user_id")
        message = request_data.get("message")
        user_email = request_data.get("user_email")
        session_name = request_data.get("session_name")

        if not all([request_manager_session_id, user_id, message]):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing required fields: request_manager_session_id, user_id, message",
            )

        # At this point, we know these are not None due to the check above
        assert user_id is not None
        assert message is not None

        # Get database session
        db_manager = get_database_manager()

        async with db_manager.get_session() as db:
            # Create responses session manager
            session_manager = ResponsesSessionManager(
                db_session=db,
                user_id=user_id,
                user_email=user_email,
            )

            # Process the message using responses mode
            response_content = await session_manager.handle_responses_message(
                text=message,
                request_manager_session_id=request_manager_session_id,
                session_name=session_name,
            )

            # Get current agent name for response metadata
            current_agent_name = session_manager.current_agent_name
            current_thread_id = session_manager.get_current_thread_id()

            # Clean up the session manager
            await session_manager.close()

        # Return response in the expected format
        if current_agent_name is None:
            logger.error(
                "Cannot create response - no agent assigned",
                user_id=user_id,
                request_manager_session_id=request_manager_session_id,
            )
            return {
                "status": "error",
                "error": "No agent assigned to handle this request",
                "request_manager_session_id": request_manager_session_id,
                "user_id": user_id,
            }

        return {
            "status": "success",
            "response": {
                "content": response_content,
                "agent_id": current_agent_name,
                "metadata": {
                    "agent_name": current_agent_name,
                    "session_type": "responses_api",
                    "thread_id": current_thread_id,
                },
                "processing_time_ms": int(
                    (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
                ),
                "requires_followup": False,
                "followup_actions": [],
            },
            "request_manager_session_id": request_manager_session_id,
            "user_id": user_id,
            "current_agent": current_agent_name,
            "thread_id": current_thread_id,
        }

    except Exception as e:
        logger.error("Failed to handle responses request event", exc_info=e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to process responses request event",
        )


async def _handle_database_update_event_from_data(
    event_data: Dict[str, Any], agent_service: AgentService
) -> Dict[str, Any]:
    """Handle database update event from Request Manager using pre-parsed event data."""
    try:
        # Extract the actual update data from the CloudEvent data field
        update_data = event_data.get("data", {})

        request_id = update_data.get("request_id")
        session_id = update_data.get("session_id")
        agent_id = update_data.get("agent_id")
        content = update_data.get("content")
        user_id = update_data.get("user_id")

        if not all([request_id, session_id, agent_id, content]):
            raise ValueError("Missing required fields in database update event")

        if not user_id:
            logger.warning(
                "Missing user_id in database update event, using fallback",
                request_id=request_id,
                session_id=session_id,
            )
            user_id = generate_fallback_user_id(request_id)

        logger.info(
            "Database update event received from Request Manager",
            request_id=request_id,
            session_id=session_id,
            agent_id=agent_id,
        )

        return {
            "status": "updated",
            "request_id": request_id,
            "session_id": session_id,
            "agent_id": agent_id,
        }

    except Exception as e:
        logger.error("Failed to handle database update event", exc_info=e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to process database update event",
        )


# Session Management Endpoints


@app.post("/api/v1/sessions", response_model=SessionResponse)
async def create_session(
    session_data: SessionCreate,
    db: AsyncSession = Depends(get_db_session_dependency),
) -> SessionResponse:
    """Create a new session."""
    session_manager = BaseSessionManager(db)

    try:
        session = await session_manager.create_session(session_data)
        return session
    except Exception as e:
        logger.error("Failed to create session", exc_info=e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create session",
        )


@app.get("/api/v1/sessions/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: str,
    db: AsyncSession = Depends(get_db_session_dependency),
) -> SessionResponse:
    """Get session information."""
    session_manager = BaseSessionManager(db)

    session = await session_manager.get_session(session_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    return session


@app.put("/api/v1/sessions/{session_id}", response_model=SessionResponse)
async def update_session(
    session_id: str,
    session_update: SessionUpdate,
    db: AsyncSession = Depends(get_db_session_dependency),
) -> SessionResponse:
    """Update session information."""
    session_manager = BaseSessionManager(db)

    # Check if session exists
    existing_session = await session_manager.get_session(session_id)
    if not existing_session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    # Update session with provided fields
    updated_session = await session_manager.update_session(
        session_id=session_id,
        agent_id=session_update.current_agent_id,
        conversation_thread_id=session_update.conversation_thread_id,
        status=session_update.status,
        conversation_context=session_update.conversation_context,
        user_context=session_update.user_context,
    )

    if not updated_session:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update session",
        )

    return updated_session


@app.post("/api/v1/sessions/{session_id}/increment")
async def increment_request_count(
    session_id: str,
    request_id: str,
    db: AsyncSession = Depends(get_db_session_dependency),
) -> Dict[str, str]:
    """Increment the request count for a session."""
    session_manager = BaseSessionManager(db)

    try:
        await session_manager.increment_request_count(session_id, request_id)
        return {"status": "success", "message": "Request count incremented"}
    except Exception as e:
        logger.error("Failed to increment request count", exc_info=e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to increment request count",
        )


async def _update_request_log_unified(
    request_id: str,
    response_content: str,
    agent_id: str,
    response_metadata: dict[str, Any] | None = None,
    processing_time_ms: int | None = None,
    db: AsyncSession | None = None,
) -> None:
    """Update RequestLog for any API type."""
    try:
        from shared_models.models import RequestLog
        from sqlalchemy import update

        # Update the RequestLog with response content
        stmt = (
            update(RequestLog)
            .where(RequestLog.request_id == request_id)
            .values(
                response_content=response_content,
                response_metadata=response_metadata or {},
                agent_id=agent_id,
                processing_time_ms=processing_time_ms,
                completed_at=datetime.now(timezone.utc),
            )
        )

        if db:
            await db.execute(stmt)
            await db.commit()
        else:
            # For backward compatibility with existing code that doesn't pass db
            from shared_models import get_database_manager

            db_manager = get_database_manager()
            async with db_manager.get_session() as session:
                await session.execute(stmt)
                await session.commit()

        logger.info(
            "RequestLog updated",
            request_id=request_id,
            agent_id=agent_id,
            content_length=len(response_content),
        )

    except Exception as e:
        logger.error(
            "Failed to update RequestLog",
            request_id=request_id,
            error=str(e),
        )
        # Don't raise exception - RequestLog update failure shouldn't stop response


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
