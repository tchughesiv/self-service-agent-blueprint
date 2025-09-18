"""Communication strategy abstraction for eventing vs direct HTTP modes."""

import os
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import structlog
from agent_service.schemas import SessionResponse
from fastapi import HTTPException, status
from shared_clients import get_agent_client, get_integration_dispatcher_client
from shared_models import CloudEventSender, get_database_manager, get_enum_value

# RequestLog removed - Agent Service handles request/response logging
# sqlalchemy.select removed - no longer needed after removing RequestLog operations
from sqlalchemy.ext.asyncio import AsyncSession

from .normalizer import RequestNormalizer
from .response_handler import UnifiedResponseHandler
from .schemas import AgentResponse, NormalizedRequest

logger = structlog.get_logger()


class CommunicationStrategy(ABC):
    """Abstract base class for communication strategies."""

    @abstractmethod
    async def create_or_get_session(
        self, request, db: AsyncSession
    ) -> Optional[SessionResponse]:
        """Create or get session using the appropriate method for this strategy."""
        pass

    @abstractmethod
    async def send_request(self, normalized_request: NormalizedRequest) -> bool:
        """Send a request to the agent service."""
        pass

    @abstractmethod
    async def wait_for_response(
        self, request_id: str, timeout: int, db: AsyncSession
    ) -> Optional[Dict[str, Any]]:
        """Wait for and retrieve the agent response."""
        pass

    @abstractmethod
    async def deliver_response(self, agent_response: AgentResponse) -> bool:
        """Deliver the response to the integration dispatcher."""
        pass


class EventingStrategy(CommunicationStrategy):
    """Communication strategy using Knative eventing."""

    def __init__(self):
        broker_url = os.getenv("BROKER_URL", "http://knative-broker:8080")
        self.event_sender = CloudEventSender(broker_url, "request-manager")

        # Configurable polling strategy
        self.poll_intervals = [
            float(x)
            for x in os.getenv("POLL_INTERVALS", "0.5,1.0,2.0,3.0,5.0").split(",")
        ]

    async def create_or_get_session(
        self, request, db: AsyncSession
    ) -> Optional[SessionResponse]:
        """Create or get session using direct database access for eventing mode."""
        import uuid

        from agent_service.schemas import SessionResponse
        from shared_models.models import RequestSession, SessionStatus
        from sqlalchemy import select

        # Try to find existing active session
        stmt = (
            select(RequestSession)
            .where(
                RequestSession.user_id == request.user_id,
                RequestSession.integration_type == request.integration_type,
                RequestSession.status == SessionStatus.ACTIVE.value,
            )
            .order_by(RequestSession.last_request_at.desc())
        )

        result = await db.execute(stmt)
        existing_session = result.scalar_one_or_none()

        if existing_session:
            # Update activity timestamp
            existing_session.last_request_at = datetime.now(timezone.utc)
            await db.commit()
            return SessionResponse.from_orm(existing_session)

        # Create new session
        session = RequestSession(
            session_id=str(uuid.uuid4()),
            user_id=request.user_id,
            integration_type=request.integration_type,
            channel_id=getattr(request, "channel_id", None),
            thread_id=getattr(request, "thread_id", None),
            integration_metadata=request.metadata,
            status=SessionStatus.ACTIVE.value,
        )

        db.add(session)
        await db.commit()
        await db.refresh(session)

        return SessionResponse.from_orm(session)

    async def send_request(self, normalized_request: NormalizedRequest) -> bool:
        """Send request via CloudEvent."""
        request_event_data = normalized_request.model_dump(mode="json")
        success = await self.event_sender.send_request_event(
            request_event_data,
            normalized_request.request_id,
            normalized_request.user_id,
            normalized_request.session_id,
        )

        if not success:
            logger.error("Failed to publish request event")
            return False

        logger.info(
            "Request sent via eventing",
            request_id=normalized_request.request_id,
            session_id=normalized_request.session_id,
        )
        return True

    async def wait_for_response(
        self, request_id: str, timeout: int, db: AsyncSession
    ) -> Optional[Dict[str, Any]]:
        """Wait for response via CloudEvents (eventing mode).

        Note: In eventing mode, responses come via CloudEvents, not database polling.
        This method should not be used in eventing mode.
        """
        logger.warning(
            "Database polling not supported in eventing mode - use CloudEvents instead",
            request_id=request_id,
        )
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Sync requests not supported in eventing mode - use async requests instead",
        )

    async def deliver_response(self, agent_response: AgentResponse) -> bool:
        """Deliver response via CloudEvent."""
        response_event_data = agent_response.model_dump(mode="json")
        success = await self.event_sender.send_response_event(
            response_event_data,
            agent_response.request_id,
            agent_response.agent_id,
            agent_response.session_id,
        )

        if not success:
            logger.error("Failed to publish response event")
            return False

        logger.info(
            "Response delivered via eventing",
            request_id=agent_response.request_id,
            session_id=agent_response.session_id,
        )
        return True


class DirectHttpStrategy(CommunicationStrategy):
    """Communication strategy using direct HTTP calls."""

    def __init__(self):
        self.agent_client = get_agent_client()
        self.integration_client = get_integration_dispatcher_client()

    async def create_or_get_session(
        self, request, db: AsyncSession
    ) -> Optional[SessionResponse]:
        """Create or get session using agent service client."""
        if not self.agent_client:
            logger.error("Agent client not initialized")
            return None

        session_data = {
            "user_id": request.user_id,
            "integration_type": request.integration_type,
            "channel_id": getattr(request, "channel_id", None),
            "thread_id": getattr(request, "thread_id", None),
            "integration_metadata": request.metadata,
        }

        session = await self.agent_client.create_session(session_data)
        if not session:
            # Fallback to direct database access if agent client fails
            import uuid

            from shared_models.models import RequestSession, SessionStatus
            from sqlalchemy import select

            # Try to find existing active session
            stmt = (
                select(RequestSession)
                .where(
                    RequestSession.user_id == request.user_id,
                    RequestSession.integration_type == request.integration_type,
                    RequestSession.status == SessionStatus.ACTIVE.value,
                )
                .order_by(RequestSession.last_request_at.desc())
            )

            result = await db.execute(stmt)
            existing_session = result.scalar_one_or_none()

            if existing_session:
                # Update activity timestamp
                existing_session.last_request_at = datetime.now(timezone.utc)
                await db.commit()
                return SessionResponse.from_orm(existing_session)

            # Create new session
            session = RequestSession(
                session_id=str(uuid.uuid4()),
                user_id=request.user_id,
                integration_type=request.integration_type,
                channel_id=getattr(request, "channel_id", None),
                thread_id=getattr(request, "thread_id", None),
                integration_metadata=request.metadata,
                status=SessionStatus.ACTIVE.value,
            )

            db.add(session)
            await db.commit()
            await db.refresh(session)

            session = SessionResponse.from_orm(session)

        return session

    async def send_request(self, normalized_request: NormalizedRequest) -> bool:
        """
        Send request via direct HTTP.

        Note: In direct HTTP mode, this method just validates the client is ready.
        The actual request processing happens synchronously in the main flow.
        """
        if not self.agent_client:
            logger.error("Agent client not initialized")
            return False

        logger.info(
            "Direct HTTP mode: Request will be processed synchronously",
            request_id=normalized_request.request_id,
            session_id=normalized_request.session_id,
        )
        return True  # Client is ready, request will be processed synchronously

    async def wait_for_response(
        self, request_id: str, timeout: int, db: AsyncSession
    ) -> Optional[Dict[str, Any]]:
        """
        Wait for response in direct HTTP mode.

        Note: This method should not be called in direct HTTP mode as requests
        are processed synchronously. If called, it indicates a bug in the
        request processing flow.
        """
        logger.error(
            "BUG: wait_for_response called in direct HTTP mode",
            request_id=request_id,
            strategy="DirectHttpStrategy",
            explanation="Direct HTTP mode processes requests synchronously, so this method should never be called",
        )
        raise RuntimeError(
            "wait_for_response should not be called in direct HTTP mode. "
            "This indicates a bug in the request processing flow."
        )

    async def deliver_response(self, agent_response: AgentResponse) -> bool:
        """Deliver response via direct HTTP."""
        if not self.integration_client:
            logger.error("Integration client not initialized")
            return False

        success = await self.integration_client.deliver_response(agent_response)

        if not success:
            logger.error("Failed to deliver response via direct HTTP")
            return False

        logger.info(
            "Response delivered via direct HTTP",
            request_id=agent_response.request_id,
            session_id=agent_response.session_id,
        )
        return True


def get_communication_strategy() -> CommunicationStrategy:
    """Get the appropriate communication strategy based on configuration."""
    eventing_enabled = os.getenv("EVENTING_ENABLED", "true").lower() == "true"

    if eventing_enabled:
        return EventingStrategy()
    else:
        return DirectHttpStrategy()


class UnifiedRequestProcessor:
    """Unified request processor that works with any communication strategy."""

    def __init__(self, strategy: CommunicationStrategy, agent_client=None):
        self.strategy = strategy
        self.agent_client = agent_client

    def _extract_session_data(self, session) -> tuple[str, str]:
        """Extract session_id and current_agent_id from session data.

        Handles SessionResponse objects (from agent client) and SessionResponse objects (from session manager).
        """
        # Both agent client and session manager now return SessionResponse objects
        return session.session_id, session.current_agent_id

    async def process_request_async(self, request, db: AsyncSession) -> Dict[str, Any]:
        """Process a request asynchronously (eventing mode)."""
        logger.info(
            "Starting async request processing",
            user_id=request.user_id,
            request_type=request.request_type,
        )
        normalizer = RequestNormalizer()

        # Delegate session management to the communication strategy
        logger.debug("Creating or getting session", user_id=request.user_id)
        session = await self.strategy.create_or_get_session(request, db)

        # Check if session creation failed
        if not session:
            logger.error("Failed to create or find session", user_id=request.user_id)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create session",
            )

        logger.info(
            "Session created/found successfully",
            session_id=session.session_id,
            user_id=request.user_id,
        )

        # Normalize the request
        session_id, current_agent_id = self._extract_session_data(session)
        normalized_request = normalizer.normalize_request(
            request, session_id, current_agent_id
        )

        # Note: Request logging and session management is now handled by the Agent Service
        # The Request Manager only manages sessions, not request/response logging
        # In eventing mode, the Agent Service handles its own session management

        # Send request using strategy
        logger.debug(
            "Sending request using strategy",
            request_id=normalized_request.request_id,
            session_id=session_id,
        )
        success = await self.strategy.send_request(normalized_request)

        if not success:
            logger.error(
                "Failed to send request",
                request_id=normalized_request.request_id,
                session_id=session_id,
            )
            raise Exception("Failed to send request")
        else:
            logger.debug(
                "Request sent successfully",
                request_id=normalized_request.request_id,
                session_id=session_id,
            )

        logger.info(
            "Request processed asynchronously",
            request_id=normalized_request.request_id,
            session_id=session_id,
            user_id=request.user_id,
            integration_type=get_enum_value(request.integration_type),
        )

        return {
            "request_id": normalized_request.request_id,
            "session_id": session_id,
            "status": "accepted",
            "message": "Request has been queued for processing",
        }

    async def process_request_async_with_delivery(
        self, request, db: AsyncSession, timeout: int = 120
    ) -> Dict[str, Any]:
        """Process a request asynchronously but deliver response in direct HTTP mode."""
        normalizer = RequestNormalizer()

        # Delegate session management to the communication strategy
        session = await self.strategy.create_or_get_session(request, db)

        # Check if session creation failed
        if not session:
            logger.error("Failed to create or find session", user_id=request.user_id)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create session",
            )

        # Normalize the request
        session_id, current_agent_id = self._extract_session_data(session)
        normalized_request = normalizer.normalize_request(
            request, session_id, current_agent_id
        )

        # Note: Request logging is now handled by the Agent Service
        # The Request Manager only manages sessions, not request/response logging

        # Note: Request count is handled by the Agent Service when it processes the CloudEvent

        # In direct HTTP mode, process synchronously but return immediately to avoid timeout
        if isinstance(self.strategy, DirectHttpStrategy):
            # Process the request in the background
            import asyncio

            asyncio.create_task(
                self._process_and_deliver_background(normalized_request, db)
            )

            logger.info(
                "Request queued for background processing",
                request_id=normalized_request.request_id,
                session_id=session_id,
                user_id=request.user_id,
                integration_type=get_enum_value(request.integration_type),
            )

            return {
                "request_id": normalized_request.request_id,
                "session_id": session_id,
                "status": "accepted",
                "message": "Request has been queued for processing",
            }
        else:
            # Eventing mode - use standard async processing
            success = await self.strategy.send_request(normalized_request)

            if not success:
                raise Exception("Failed to send request")

            logger.info(
                "Request processed asynchronously",
                request_id=normalized_request.request_id,
                session_id=session_id,
                user_id=request.user_id,
                integration_type=get_enum_value(request.integration_type),
            )

            return {
                "request_id": normalized_request.request_id,
                "session_id": session_id,
                "status": "accepted",
                "message": "Request has been queued for processing",
            }

    async def _process_and_deliver_background(self, normalized_request, db):
        """Process request in background and deliver response."""
        try:
            # Process the request
            agent_client = get_agent_client()
            if not agent_client:
                logger.error("Agent client not initialized for background processing")
                return

            # Note: Session management is now handled by the Agent Service automatically

            agent_response = await agent_client.process_request(normalized_request)
            if not agent_response:
                logger.error("Agent service failed to process request in background")
                return

            # Note: Request logging is now handled by the Agent Service

            # Deliver response via integration dispatcher
            delivery_success = await self.strategy.deliver_response(agent_response)
            if not delivery_success:
                logger.warning("Failed to deliver response in background")

            logger.info(
                "Background request processing completed",
                request_id=normalized_request.request_id,
                session_id=normalized_request.session_id,
                user_id=normalized_request.user_id,
            )

        except Exception as e:
            logger.error(
                "Error in background request processing",
                request_id=normalized_request.request_id,
                error=str(e),
            )

    async def process_request_sync(
        self, request, db: AsyncSession, timeout: int = 120
    ) -> Dict[str, Any]:
        """Process a request synchronously and wait for response."""
        # datetime imports removed - no longer needed after removing RequestLog operations

        normalizer = RequestNormalizer()

        # Delegate session management to the communication strategy
        session = await self.strategy.create_or_get_session(request, db)

        # Check if session creation failed
        if not session:
            logger.error("Failed to create or find session", user_id=request.user_id)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create session",
            )

        # Normalize the request
        session_id, current_agent_id = self._extract_session_data(session)
        normalized_request = normalizer.normalize_request(
            request, session_id, current_agent_id
        )

        # Note: Request logging is now handled by the Agent Service
        # The Request Manager only manages sessions, not request/response logging

        # Note: Request count is handled by the Agent Service when it processes the CloudEvent

        # Handle different strategies
        if isinstance(self.strategy, EventingStrategy):
            # Eventing mode: simulate sync behavior by doing async + polling
            logger.info(
                "Simulating sync behavior in eventing mode",
                request_id=normalized_request.request_id,
            )

            # Send async request
            success = await self.strategy.send_request(normalized_request)
            if not success:
                raise Exception("Failed to send request")

            # Poll for response
            return await self._poll_for_response_sync(
                normalized_request.request_id, timeout
            )

        elif isinstance(self.strategy, DirectHttpStrategy):
            # Direct HTTP mode: process synchronously
            agent_client = get_agent_client()
            if not agent_client:
                raise Exception("Agent client not initialized")

            # Note: Session management is now handled by the Agent Service automatically

            agent_response = await agent_client.process_request(normalized_request)
            if not agent_response:
                raise Exception("Agent service failed to process request")

            # NOTE: Agent routing is now handled by the Agent Service
            # No additional routing logic needed in Request Manager

            # Note: Request logging is now handled by the Agent Service

            # For sync requests, return response directly; for async requests, deliver via integration dispatcher
            if normalized_request.request_type.upper() != "SYNC":
                # Deliver response for async requests (Slack, email, etc.)
                delivery_success = await self.strategy.deliver_response(agent_response)
                if not delivery_success:
                    logger.warning("Failed to deliver response")

            # Prepare response data (always returned for sync requests)
            response_data = {
                "content": agent_response.content,
                "agent_id": agent_response.agent_id,
                "metadata": agent_response.metadata,
                "processing_time_ms": agent_response.processing_time_ms,
                "requires_followup": agent_response.requires_followup,
                "followup_actions": agent_response.followup_actions,
            }

        else:
            raise Exception("Unknown communication strategy")

        logger.info(
            "Request processed synchronously",
            request_id=normalized_request.request_id,
            session_id=session_id,
            user_id=request.user_id,
        )

        return {
            "request_id": normalized_request.request_id,
            "session_id": session_id,
            "status": "completed",
            "response": response_data,
        }

    async def _poll_for_response_sync(
        self, request_id: str, timeout: int
    ) -> Dict[str, Any]:
        """
        Poll for response to an async request in eventing mode to simulate sync behavior.

        Args:
            request_id: The request ID to poll for
            timeout: Maximum time to wait in seconds

        Returns:
            The response data in sync format
        """
        import asyncio

        max_attempts = timeout  # Poll every second for timeout seconds
        attempt = 0

        logger.info(
            "Polling for response in eventing mode",
            request_id=request_id,
            timeout=timeout,
        )

        while attempt < max_attempts:
            try:
                # Use the response handler to get the response data
                db_manager = get_database_manager()
                async with db_manager.get_session() as db:
                    response_handler = UnifiedResponseHandler(db)
                    response_data = await response_handler.get_response_data(request_id)

                    if response_data:
                        # Response is ready
                        logger.info(
                            "Response found in database",
                            request_id=request_id,
                            attempt=attempt + 1,
                        )

                        return {
                            "request_id": request_id,
                            "session_id": response_data.get("session_id"),
                            "status": "completed",
                            "response": {
                                "content": response_data.get("content"),
                                "agent_id": response_data.get("agent_id"),
                                "metadata": response_data.get("metadata", {}),
                                "processing_time_ms": response_data.get(
                                    "processing_time_ms"
                                ),
                                "requires_followup": response_data.get(
                                    "requires_followup", False
                                ),
                                "followup_actions": response_data.get(
                                    "followup_actions", []
                                ),
                            },
                        }

                    # No response yet, wait and try again
                    await asyncio.sleep(1)
                    attempt += 1

            except Exception as e:
                logger.warning(
                    "Error polling for response",
                    request_id=request_id,
                    attempt=attempt + 1,
                    error=str(e),
                )
                await asyncio.sleep(1)
                attempt += 1

        # Timeout reached
        logger.error(
            "Timeout waiting for response",
            request_id=request_id,
            timeout=timeout,
        )

        raise HTTPException(
            status_code=status.HTTP_408_REQUEST_TIMEOUT,
            detail=f"Request timed out after {timeout} seconds",
        )
