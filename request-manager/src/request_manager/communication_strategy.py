"""Communication strategy abstraction for eventing vs direct HTTP modes."""

import os
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import structlog
from agent_service.schemas import SessionResponse
from fastapi import HTTPException, status
from shared_clients import get_agent_client, get_integration_dispatcher_client
from shared_models import CloudEventSender, get_enum_value
from shared_models.models import RequestLog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .normalizer import RequestNormalizer
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

    async def create_or_get_session(
        self, request, db: AsyncSession
    ) -> Optional[SessionResponse]:
        """Create or get session using direct database access for eventing mode."""
        from .session_manager import SessionManager

        session_manager = SessionManager(db)
        return await session_manager.find_or_create_session(
            user_id=request.user_id,
            integration_type=request.integration_type,
            channel_id=getattr(request, "channel_id", None),
            thread_id=getattr(request, "thread_id", None),
            integration_metadata=request.metadata,
        )

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
        """Wait for response by polling database (eventing mode)."""
        from datetime import datetime, timedelta

        start_time = datetime.now()
        end_time = start_time + timedelta(seconds=timeout)

        logger.debug(
            "Starting to wait for response",
            request_id=request_id,
            timeout=timeout,
        )

        while datetime.now() < end_time:
            try:
                # Use a fresh database session for each poll to avoid session issues
                from shared_models import get_database_manager

                async with get_database_manager().get_session() as fresh_db:
                    stmt = select(RequestLog).where(RequestLog.request_id == request_id)
                    result = await fresh_db.execute(stmt)
                    request_log = result.scalar_one_or_none()

                    if request_log and request_log.response_content:
                        logger.info(
                            "Response received via eventing",
                            request_id=request_id,
                            elapsed_seconds=(
                                datetime.now() - start_time
                            ).total_seconds(),
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

                    # Log progress every 5 seconds
                    elapsed = (datetime.now() - start_time).total_seconds()
                    if int(elapsed) % 5 == 0 and elapsed > 0:
                        logger.debug(
                            "Still waiting for response",
                            request_id=request_id,
                            elapsed_seconds=elapsed,
                            has_request_log=request_log is not None,
                        )

            except Exception as e:
                logger.warning(
                    "Error polling for response",
                    request_id=request_id,
                    error=str(e),
                )

            # Wait before next poll
            import asyncio

            await asyncio.sleep(1.0)  # Increased from 0.5 to 1.0 seconds

        logger.warning(
            "Timeout waiting for response via eventing", request_id=request_id
        )
        return None

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
            from .session_manager import SessionManager

            session_manager = SessionManager(db)
            session = await session_manager.find_or_create_session(
                user_id=request.user_id,
                integration_type=request.integration_type,
                channel_id=getattr(request, "channel_id", None),
                thread_id=getattr(request, "thread_id", None),
                integration_metadata=request.metadata,
            )

        return session

    async def send_request(self, normalized_request: NormalizedRequest) -> bool:
        """Send request via direct HTTP."""
        if not self.agent_client:
            logger.error("Agent client not initialized")
            return False

        logger.info(
            "Request sent via direct HTTP",
            request_id=normalized_request.request_id,
            session_id=normalized_request.session_id,
        )
        return True  # Request will be processed synchronously

    async def wait_for_response(
        self, request_id: str, timeout: int, db: AsyncSession
    ) -> Optional[Dict[str, Any]]:
        """Process request synchronously and return response."""
        # This method should not be called in direct HTTP mode
        # as the request is processed synchronously in the main flow
        logger.warning(
            "wait_for_response called in direct HTTP mode - this should not happen"
        )
        return None

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

        # Log the request
        request_log = RequestLog(
            request_id=normalized_request.request_id,
            session_id=session_id,
            request_type=request.request_type,
            request_content=request.content,
            normalized_request=normalized_request.model_dump(mode="json"),
            agent_id=normalized_request.target_agent_id,
        )

        db.add(request_log)
        await db.commit()

        # Increment request count via agent service
        logger.debug(
            "Incrementing request count",
            session_id=session_id,
            request_id=normalized_request.request_id,
        )
        if self.agent_client:
            success = await self.agent_client.increment_request_count(
                session_id, normalized_request.request_id
            )
            if not success:
                logger.error(
                    "Failed to increment request count via agent service",
                    session_id=session_id,
                    request_id=normalized_request.request_id,
                )
            else:
                logger.debug(
                    "Request count incremented successfully",
                    session_id=session_id,
                    request_id=normalized_request.request_id,
                )
        else:
            # Fallback to direct database access for eventing mode
            # For eventing mode, request count is handled by the agent service
            # when it processes the CloudEvent
            logger.debug(
                "No agent client available, skipping request count increment",
                session_id=session_id,
            )
            pass

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

        # Log the request
        request_log = RequestLog(
            request_id=normalized_request.request_id,
            session_id=session_id,
            request_type=request.request_type,
            request_content=request.content,
            normalized_request=normalized_request.model_dump(mode="json"),
            agent_id=normalized_request.target_agent_id,
        )

        db.add(request_log)
        await db.commit()

        # Increment request count via agent service
        if self.agent_client:
            await self.agent_client.increment_request_count(
                session_id, normalized_request.request_id
            )
        else:
            # Fallback to direct database access for eventing mode
            # For eventing mode, request count is handled by the agent service
            # when it processes the CloudEvent
            pass

        # In direct HTTP mode, process synchronously but return immediately to avoid timeout
        if isinstance(self.strategy, DirectHttpStrategy):
            # Process the request in the background
            import asyncio

            asyncio.create_task(
                self._process_and_deliver_background(
                    normalized_request, request_log, db
                )
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

    async def _process_and_deliver_background(
        self, normalized_request, request_log, db
    ):
        """Process request in background and deliver response."""
        try:
            # Process the request
            agent_client = get_agent_client()
            if not agent_client:
                logger.error("Agent client not initialized for background processing")
                return

            agent_response = await agent_client.process_request(normalized_request)
            if not agent_response:
                logger.error("Agent service failed to process request in background")
                return

            # Update request log with response
            request_log.response_content = agent_response.content
            request_log.response_metadata = agent_response.metadata
            request_log.agent_id = agent_response.agent_id
            request_log.processing_time_ms = agent_response.processing_time_ms
            request_log.completed_at = datetime.now(timezone.utc)

            # Update session with current agent (if not already set)
            if self.agent_client:
                current_session = await self.agent_client.get_session(
                    normalized_request.session_id
                )
                if current_session and not current_session.current_agent_id:
                    # Convert agent UUID to agent name for session storage
                    agent_name = agent_response.agent_id  # Use agent_id directly

                    if agent_name:
                        # Update session with the agent name via agent service
                        await self.agent_client.update_session(
                            session_id=normalized_request.session_id,
                            session_update={"current_agent_id": agent_name},
                        )
            else:
                # Fallback to direct database access for eventing mode
                from .session_manager import SessionManager

                session_manager = SessionManager(db)
                current_session = await session_manager.get_session(
                    normalized_request.session_id
                )
                if current_session and not current_session.current_agent_id:
                    # Convert agent UUID to agent name for session storage
                    agent_name = agent_response.agent_id  # Use agent_id directly

                    if agent_name:
                        # Update session with the agent name
                        await session_manager.update_session(
                            session_id=normalized_request.session_id,
                            agent_id=agent_name,
                        )
                    logger.info(
                        "Updated session with current agent",
                        session_id=normalized_request.session_id,
                        agent_name=agent_name,
                        agent_uuid=agent_response.agent_id,
                    )

            await db.commit()

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
        from datetime import datetime, timezone

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

        # Log the request
        request_log = RequestLog(
            request_id=normalized_request.request_id,
            session_id=session_id,
            request_type=request.request_type,
            request_content=request.content,
            normalized_request=normalized_request.model_dump(mode="json"),
            agent_id=normalized_request.target_agent_id,
        )

        db.add(request_log)
        await db.commit()

        # Increment request count via agent service
        if self.agent_client:
            await self.agent_client.increment_request_count(
                session_id, normalized_request.request_id
            )
        else:
            # Fallback to direct database access for eventing mode
            # For eventing mode, request count is handled by the agent service
            # when it processes the CloudEvent
            pass

        # Handle different strategies
        if isinstance(self.strategy, EventingStrategy):
            # Eventing mode: send event and wait for response
            success = await self.strategy.send_request(normalized_request)
            if not success:
                raise Exception("Failed to send request event")

            response_data = await self.strategy.wait_for_response(
                normalized_request.request_id, timeout, db
            )

            if not response_data:
                raise Exception("Timeout waiting for response")

        elif isinstance(self.strategy, DirectHttpStrategy):
            # Direct HTTP mode: process synchronously
            agent_client = get_agent_client()
            if not agent_client:
                raise Exception("Agent client not initialized")

            agent_response = await agent_client.process_request(normalized_request)
            if not agent_response:
                raise Exception("Agent service failed to process request")

            # NOTE: Agent routing is now handled by the Agent Service
            # No additional routing logic needed in Request Manager

            # Update request log with response
            request_log.response_content = agent_response.content
            request_log.response_metadata = agent_response.metadata
            request_log.agent_id = agent_response.agent_id
            request_log.processing_time_ms = agent_response.processing_time_ms
            request_log.completed_at = datetime.now(timezone.utc)

            await db.commit()

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
