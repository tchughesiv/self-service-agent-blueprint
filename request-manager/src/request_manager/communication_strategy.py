"""Communication strategy abstraction for eventing mode."""

import asyncio
import os
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import HTTPException, status
from shared_models import (
    CloudEventSender,
    SessionResponse,
    configure_logging,
    get_enum_value,
    get_or_create_zammad_ticket_session,
)
from shared_models.models import IntegrationType, NormalizedRequest
from sqlalchemy.ext.asyncio import AsyncSession

from .normalizer import RequestNormalizer

logger = configure_logging("request-manager")

# Global registry for response futures (event-driven approach)
_response_futures_registry: dict[str, Any] = {}
_session_futures_registry: dict[str, Any] = {}


def _should_filter_sessions_by_integration_type() -> bool:
    """Check if sessions should be filtered by integration type.

    Returns:
        True if sessions should be separated by integration type (legacy behavior)
        False if a single session should be maintained across all integration types (default)
    """
    return os.getenv("SESSION_PER_INTEGRATION_TYPE", "false").lower() == "true"


def _get_session_timeout_hours() -> int:
    """Get session timeout in hours from environment variable.

    Returns:
        Session timeout in hours (default: 336 hours = 2 weeks)
    """
    return int(os.getenv("SESSION_TIMEOUT_HOURS", "336"))


# Global polling task (single per pod)
_pod_polling_task: Optional[asyncio.Task[None]] = None


def get_pod_name() -> Optional[str]:
    """Get pod name from environment variable."""
    return os.getenv("HOSTNAME") or os.getenv("POD_NAME")


def register_response_future(request_id: str) -> Any:
    """Ensure a response future exists for request_id. Creates if not present.

    Must be called before releasing the session lock so the poller can resolve
    if another pod dequeues and processes this request. Returns the future.
    """
    if request_id not in _response_futures_registry:
        _response_futures_registry[request_id] = asyncio.Future()
    return _response_futures_registry[request_id]


def resolve_response_future(request_id: str, response_data: Dict[str, Any]) -> bool:
    """Resolve a waiting response future when event is received.

    Note: This is now optional - the primary delivery mechanism is database polling.
    If a future exists and response arrives via event, resolve it immediately for speed.
    If not, the database polling will find it.

    Returns:
        True if future was found and resolved, False if not found (database polling will handle it)
    """
    logger.debug(
        "Attempting to resolve response future (optional - database polling is primary)",
        request_id=request_id,
        registry_keys=list(_response_futures_registry.keys()),
    )

    if request_id in _response_futures_registry:
        future = _response_futures_registry[request_id]
        if not future.done():
            # Mark as from event for logging
            response_data["_from_event"] = True
            future.set_result(response_data)
            logger.info(
                "Response future resolved via event (fast path)",
                request_id=request_id,
            )
            return True
        # Future already resolved, return True (no need to log - this is expected)
        return True
    else:
        # No future found - database polling will handle it
        logger.debug(
            "No waiting response future found - database polling will handle delivery",
            request_id=request_id,
        )
        return False


async def create_or_get_session_shared(
    request: Any, db: AsyncSession
) -> Optional[SessionResponse]:
    """Shared session management logic for all communication strategies.

    This function handles the common pattern of:
    1. Looking for existing active sessions for the user
    2. Reusing existing sessions if found (updating timestamp)
    3. Creating new sessions if none found

    Args:
        request: The request object containing user_id, integration_type, etc.
        db: Database session for queries and updates

    Returns:
        SessionResponse object for the session (existing or newly created)
    """
    # Resolve user_id to canonical user_id if it's an email address
    from shared_models import SessionResponse, resolve_canonical_user_id
    from shared_models.models import RequestSession, SessionStatus
    from sqlalchemy import select

    canonical_user_id = await resolve_canonical_user_id(
        request.user_id,
        integration_type=getattr(request, "integration_type", None),
        db=db,
    )

    session_timeout_hours = _get_session_timeout_hours()
    expires_at = datetime.now(timezone.utc) + timedelta(hours=session_timeout_hours)

    # Zammad: one stable session per ticket per user (not one session per user like other channels).
    req_integration = getattr(request, "integration_type", None)
    if (
        req_integration is not None
        and get_enum_value(req_integration) == IntegrationType.ZAMMAD.value
    ):
        tid_raw = getattr(request, "ticket_id", None)
        if tid_raw is None:
            md_early = getattr(request, "metadata", {}) or {}
            tid_raw = md_early.get("ticket_id")
        parsed_ticket_id: Optional[int] = None
        if tid_raw is not None:
            try:
                parsed_ticket_id = int(tid_raw)
            except (TypeError, ValueError):
                parsed_ticket_id = None
        if parsed_ticket_id is not None and parsed_ticket_id >= 1:
            z_sess = await get_or_create_zammad_ticket_session(
                db,
                canonical_user_id=canonical_user_id,
                ticket_id=parsed_ticket_id,
                channel_id=getattr(request, "channel_id", None),
                thread_id=getattr(request, "thread_id", None),
                integration_metadata=getattr(request, "metadata", {}) or {},
                user_context={},
                expires_at=expires_at,
            )
            return z_sess

    # Check if a session_id was provided in metadata (e.g., from X-Session-ID header in email reply, or thread metadata)
    # This allows integrations to provide a session_id to continue an existing session
    request_metadata = getattr(request, "metadata", {}) or {}
    provided_session_id = request_metadata.get("session_id")

    # If a session_id is provided, try to use it first
    if provided_session_id:
        logger.debug(
            "Session ID provided in request metadata, attempting to use it",
            provided_session_id=provided_session_id,
            canonical_user_id=canonical_user_id,
        )
        # Verify the provided session_id exists and belongs to this user
        stmt = select(RequestSession).where(
            RequestSession.session_id == provided_session_id,
            RequestSession.user_id == canonical_user_id,
            RequestSession.status == SessionStatus.ACTIVE.value,
        )
        result = await db.execute(stmt)
        provided_session = result.scalar_one_or_none()

        if provided_session:
            # Check if session is expired
            now = datetime.now(timezone.utc)
            if provided_session.expires_at is None or provided_session.expires_at > now:
                # Valid session found - update activity timestamp and return it
                provided_session.last_request_at = datetime.now(timezone.utc)  # type: ignore[assignment]
                await db.commit()
                logger.info(
                    "Reusing provided session from metadata",
                    session_id=provided_session_id,
                    canonical_user_id=canonical_user_id,
                )
                return SessionResponse.model_validate(provided_session)
            else:
                logger.warning(
                    "Provided session_id is expired, will create new session",
                    session_id=provided_session_id,
                    canonical_user_id=canonical_user_id,
                )
        else:
            logger.warning(
                "Provided session_id not found or doesn't belong to user, will create new session",
                provided_session_id=provided_session_id,
                canonical_user_id=canonical_user_id,
            )

    # Check if we should filter by integration type
    filter_by_integration_type = _should_filter_sessions_by_integration_type()

    # Get current time for expiration checks
    now = datetime.now(timezone.utc)

    # Try to find existing active session (not expired)
    # Use SELECT FOR UPDATE to lock rows and prevent concurrent session creation
    where_conditions = [
        RequestSession.user_id == canonical_user_id,
        RequestSession.status == SessionStatus.ACTIVE.value,
        # Filter out expired sessions
        ((RequestSession.expires_at.is_(None)) | (RequestSession.expires_at > now)),
    ]

    # Optionally filter by integration type based on env var
    if filter_by_integration_type:
        where_conditions.append(
            RequestSession.integration_type == request.integration_type
        )

    # Use SELECT FOR UPDATE SKIP LOCKED to prevent race conditions
    # SKIP LOCKED allows other transactions to proceed if row is locked
    stmt = (
        select(RequestSession)
        .where(*where_conditions)
        .order_by(RequestSession.last_request_at.desc())
        .with_for_update(skip_locked=True)
    )

    result = await db.execute(stmt)
    existing_sessions = result.scalars().all()

    # Debug logging for session lookup
    logger.debug(
        "Session lookup results",
        canonical_user_id=canonical_user_id,
        original_user_id=request.user_id,
        integration_type=(
            request.integration_type.value
            if hasattr(request.integration_type, "value")
            else str(request.integration_type)
        ),
        filter_by_integration_type=filter_by_integration_type,
        found_sessions_count=len(existing_sessions),
    )

    if existing_sessions:
        # Use the most recent session (first in the ordered list)
        existing_session = existing_sessions[0]

        # If we found multiple sessions, clean up the old ones
        if len(existing_sessions) > 1:
            logger.warning(
                "Multiple active sessions found for user, cleaning up old sessions",
                user_id=canonical_user_id,
                original_user_id=request.user_id,
                integration_type=request.integration_type,
                session_count=len(existing_sessions),
                selected_session_id=existing_session.session_id,
                all_session_ids=[s.session_id for s in existing_sessions],
                filter_by_integration_type=filter_by_integration_type,
            )

            # Use the cleanup utility function
            from shared_models import get_enum_value

            from .database_utils import cleanup_old_sessions

            # Pass integration_type only if filtering by it, otherwise None
            # Convert enum to string value for consistency
            cleanup_integration_type = (
                get_enum_value(request.integration_type)
                if filter_by_integration_type
                else None
            )

            deactivated_count = await cleanup_old_sessions(
                db=db,
                user_id=canonical_user_id,
                integration_type=cleanup_integration_type,
            )

            logger.info(
                "Session cleanup completed",
                user_id=canonical_user_id,
                original_user_id=request.user_id,
                deactivated_count=deactivated_count,
            )

        # Update activity timestamp
        existing_session.last_request_at = datetime.now(timezone.utc)  # type: ignore[assignment]
        await db.commit()
        logger.info(
            "Reusing existing session",
            session_id=existing_session.session_id,
            current_agent_id=existing_session.current_agent_id,
            user_id=canonical_user_id,
            original_user_id=request.user_id,
            integration_type=(
                existing_session.integration_type.value
                if hasattr(existing_session.integration_type, "value")
                else str(existing_session.integration_type)
            ),
            filter_by_integration_type=filter_by_integration_type,
        )
        return SessionResponse.model_validate(existing_session)

    # Create new session via event (with fallback to direct DB access)
    # This uses eventing for race condition prevention while maintaining resilience
    import uuid

    from shared_models import BaseSessionManager, SessionCreate

    # Get integration_type from request, with fallback to None if not available
    request_integration_type = getattr(request, "integration_type", None)

    # SessionCreate requires integration_type, so we need to handle None case
    if request_integration_type is None:
        from shared_models.models import IntegrationType

        # Default to WEB if not specified
        request_integration_type = IntegrationType.WEB

    # Try eventing-based session creation first
    use_eventing = os.getenv("USE_SESSION_EVENTING", "true").lower() == "true"

    if use_eventing:
        try:
            # Send SESSION_CREATE_OR_GET event
            # Use correlation_id as event_id for deterministic dedup on retry
            correlation_id = str(uuid.uuid4())
            event_id = correlation_id

            broker_url = os.getenv("BROKER_URL", "http://knative-broker:8080")
            event_sender = CloudEventSender(broker_url, "request-manager")

            session_request_data = {
                "user_id": canonical_user_id,
                "integration_type": (
                    request_integration_type.value
                    if hasattr(request_integration_type, "value")
                    else str(request_integration_type)
                ),
                "channel_id": getattr(request, "channel_id", None),
                "thread_id": getattr(request, "thread_id", None),
                "external_session_id": None,
                "integration_metadata": request.metadata or {},
                "user_context": {},
            }

            success = await event_sender.send_session_create_or_get_event(
                session_data=session_request_data,
                event_id=event_id,
                user_id=canonical_user_id,
                correlation_id=correlation_id,
            )

            if success:
                logger.info(
                    "Sent SESSION_CREATE_OR_GET event",
                    event_id=event_id,
                    correlation_id=correlation_id,
                    user_id=canonical_user_id,
                )

                # Wait for SESSION_READY event
                from .session_events import wait_for_session_ready

                session_response = await wait_for_session_ready(
                    correlation_id, timeout=1.0, db=db
                )

                if session_response:
                    logger.info(
                        "Received session via event",
                        session_id=session_response.session_id,
                        correlation_id=correlation_id,
                    )

                    # Update expires_at if needed
                    if expires_at:
                        from sqlalchemy import update as sql_update

                        update_stmt = (
                            sql_update(RequestSession)
                            .where(
                                RequestSession.session_id == session_response.session_id
                            )
                            .values(expires_at=expires_at)
                        )
                        await db.execute(update_stmt)
                        await db.commit()

                    return session_response
                else:
                    logger.warning(
                        "Timeout waiting for SESSION_READY event, falling back to direct DB access",
                        correlation_id=correlation_id,
                    )
            else:
                logger.warning(
                    "Failed to send SESSION_CREATE_OR_GET event, falling back to direct DB access",
                    event_id=event_id,
                )

        except Exception as e:
            logger.warning(
                "Error in eventing-based session creation, falling back to direct DB access",
                error=str(e),
                user_id=canonical_user_id,
            )

    # Fallback: Direct database access (resilience)
    logger.debug(
        "Using direct database access for session creation",
        user_id=canonical_user_id,
    )

    session_manager = BaseSessionManager(db)
    session_data = SessionCreate(
        user_id=canonical_user_id,
        integration_type=request_integration_type,
        channel_id=getattr(request, "channel_id", None),
        thread_id=getattr(request, "thread_id", None),
        external_session_id=None,
        explicit_session_id=None,
        integration_metadata=request.metadata or {},
        user_context={},
    )

    try:
        session_response = await session_manager.create_session(session_data)

        # Update expires_at separately since it's not in SessionCreate
        if expires_at:
            from sqlalchemy import update as sql_update

            update_stmt = (
                sql_update(RequestSession)
                .where(RequestSession.session_id == session_response.session_id)
                .values(expires_at=expires_at)
            )
            await db.execute(update_stmt)
            await db.commit()
            # Re-fetch the session to get updated expires_at
            select_stmt = select(RequestSession).where(
                RequestSession.session_id == session_response.session_id
            )
            result = await db.execute(select_stmt)
            updated_session = result.scalar_one_or_none()
            if updated_session:
                session_response = SessionResponse.model_validate(updated_session)

        logger.info(
            "Created new session via direct DB access",
            session_id=session_response.session_id,
            user_id=canonical_user_id,
            original_user_id=request.user_id,
        )
        return session_response
    except Exception as e:
        # If creation failed, try one more time to get existing session
        logger.warning(
            "Session creation failed, checking for existing session",
            user_id=canonical_user_id,
            error=str(e),
        )
        existing_session_obj = await session_manager.get_active_session(
            canonical_user_id, request_integration_type
        )
        if existing_session_obj:
            logger.info(
                "Found existing session after creation failure",
                session_id=existing_session_obj.session_id,
                user_id=canonical_user_id,
            )
            return SessionResponse.model_validate(existing_session_obj)
        # Re-raise the original exception if no existing session found
        raise


class CommunicationStrategy(ABC):
    """Abstract base class for communication strategies."""

    async def create_or_get_session(
        self, request: Any, db: AsyncSession
    ) -> Optional[SessionResponse]:
        """Create or get session using shared session management logic.

        This method is implemented in the base class since all communication
        strategies use identical session management logic.
        """
        return await create_or_get_session_shared(request, db)

    @abstractmethod
    async def send_request(self, normalized_request: NormalizedRequest) -> bool:
        """Send a request to the agent service."""
        pass

    @abstractmethod
    async def wait_for_response(
        self, request_id: str, timeout: int, db: Optional[AsyncSession] = None
    ) -> Dict[str, Any]:
        """Wait for response from the agent service."""
        pass


class EventingStrategy(CommunicationStrategy):
    """Communication strategy using Knative eventing."""

    def __init__(self) -> None:
        broker_url = os.getenv("BROKER_URL", "http://knative-broker:8080")
        self.event_sender = CloudEventSender(broker_url, "request-manager")

        # Configurable polling strategy
        self.poll_intervals = [
            float(x)
            for x in os.getenv("POLL_INTERVALS", "0.5,1.0,2.0,3.0,5.0").split(",")
        ]

    async def send_request(self, normalized_request: NormalizedRequest) -> bool:
        """Send request via CloudEvent.

        REQUEST_CREATED uses event_id=request_id. AGENT_RESPONSE_READY uses
        event_id=agent-response:{request_id} to avoid collision when request-manager
        claims both. processed_events composite key (event_id, processed_by) lets
        multiple services claim independently.
        """
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
        self, request_id: str, timeout: int, db: Optional[AsyncSession] = None
    ) -> Dict[str, Any]:
        """Wait for response using single per-pod polling mechanism.

        Primary mechanism: Single background polling task per pod checks database.
        Any pod that receives the response event stores it in the database.

        Optional fast path: If response arrives via event at this pod, resolve immediately.
        """
        logger.info(
            "Waiting for response (single pod polling mechanism)",
            request_id=request_id,
            timeout=timeout,
        )

        # Use existing future if pre-registered (two-phase insert), else create
        response_future = register_response_future(request_id)
        logger.debug(
            "Response future ready (pre-registered or created)",
            request_id=request_id,
        )

        try:
            # Wait for the response (either from event fast path or single pod polling)
            response_data = await asyncio.wait_for(response_future, timeout=timeout)

            logger.info(
                "Response received",
                request_id=request_id,
                source="event" if response_data.get("_from_event") else "database",
            )

            # Remove internal flag
            response_data.pop("_from_event", None)

            return {
                "request_id": request_id,
                "session_id": response_data.get("session_id"),
                "status": "completed",
                "created_at": response_data.get("created_at"),
                "agent_received_at": response_data.get("agent_received_at"),
                "response": {
                    "content": response_data.get("content"),
                    "agent_id": response_data.get("agent_id"),
                    "metadata": response_data.get("metadata", {}),
                    "processing_time_ms": response_data.get("processing_time_ms"),
                    "requires_followup": response_data.get("requires_followup", False),
                    "followup_actions": response_data.get("followup_actions", []),
                },
            }

        except asyncio.TimeoutError:
            logger.error(
                "Timeout waiting for response",
                request_id=request_id,
                timeout=timeout,
            )
            raise  # Re-raise so request-manager returns 503 for retry
        finally:
            # Clean up the future
            if request_id in _response_futures_registry:
                del _response_futures_registry[request_id]


async def _start_pod_polling_task(pod_name: str) -> None:
    """Start the single per-pod polling task that checks for responses.

    Polls the database for request_ids in _response_futures_registry where
    response_content is not null. Does not filter by pod_name (another pod
    may process a request this pod accepted; poller must still resolve).
    """
    global _pod_polling_task

    if _pod_polling_task and not _pod_polling_task.done():
        logger.warning("Pod polling task already running")
        return

    _pod_polling_task = asyncio.create_task(_pod_response_poller(pod_name))
    logger.info(
        "Started single per-pod polling task",
        pod_name=pod_name,
    )


async def _pod_response_poller(pod_name: str) -> None:
    """Single background polling task per pod that checks for responses.

    Polls the database for request_ids in _response_futures_registry where
    response_content is not null. Does not filter by pod_name—when pod A
    accepts and pod B processes, the row has pod_name=pod_B; pod A must
    still receive the response via this poller.
    """
    poll_interval = float(os.getenv("DB_POLL_INTERVAL", "0.5"))  # Poll every 500ms

    logger.info(
        "Pod response poller started",
        pod_name=pod_name,
        poll_interval=poll_interval,
    )

    while True:
        try:
            # Get all waiting request_ids from registry (cap to limit IN clause size)
            waiting_request_ids = list(_response_futures_registry.keys())[:100]

            if not waiting_request_ids:
                # No requests waiting, sleep and continue
                await asyncio.sleep(poll_interval)
                continue

            # Query database for responses. Do NOT filter by pod_name: pod_name = pod that
            # is *processing* the request. When pod A accepts and pod B processes, the row
            # has pod_name=pod_B; pod A must still receive the response via polling.
            from shared_models import get_database_manager
            from shared_models.models import RequestLog
            from sqlalchemy import select

            from .response_builder import build_response_data_from_request_log

            db_manager = get_database_manager()
            async with db_manager.get_session() as db:
                stmt = (
                    select(RequestLog)
                    .where(
                        RequestLog.request_id.in_(waiting_request_ids),
                        RequestLog.response_content.isnot(None),
                    )
                    .order_by(RequestLog.created_at.asc())
                )
                result = await db.execute(stmt)
                request_logs = result.scalars().all()

                # Resolve futures for any found responses (FIFO by created_at)
                for request_log in request_logs:
                    request_id: str = str(request_log.request_id)
                    if request_id in _response_futures_registry:
                        future = _response_futures_registry[request_id]
                        if not future.done():
                            response_data = build_response_data_from_request_log(
                                request_log, from_event=False
                            )
                            future.set_result(response_data)
                            logger.info(
                                "Response found in database via single pod polling",
                                request_id=request_id,
                                pod_name=pod_name,
                            )

        except asyncio.CancelledError:
            logger.info("Pod polling task cancelled", pod_name=pod_name)
            break
        except Exception as e:
            logger.error(
                "Error in pod polling task",
                pod_name=pod_name,
                error=str(e),
            )
            # Continue polling even on error
            await asyncio.sleep(poll_interval)
        else:
            # Wait before next poll
            await asyncio.sleep(poll_interval)


def get_communication_strategy() -> CommunicationStrategy:
    """Get the communication strategy (eventing-based)."""
    return EventingStrategy()


async def check_communication_strategy() -> bool:
    """Check the health of the eventing communication strategy configuration."""
    try:
        broker_url = os.getenv("BROKER_URL", "http://knative-broker:8080")

        # Check if we can create a CloudEventSender
        # This works for both mock eventing and real Knative eventing
        from shared_models import CloudEventSender

        event_sender = CloudEventSender(broker_url, "request-manager")
        return event_sender is not None
    except Exception as e:
        logger.error("Communication strategy health check failed", error=str(e))
        return False


class UnifiedRequestProcessor:
    """Unified request processor for eventing-based communication."""

    def __init__(self, strategy: CommunicationStrategy) -> None:
        self.strategy = strategy

    def _extract_session_data(self, session: Any) -> tuple[str, str]:
        """Extract session_id and current_agent_id from session data.

        Handles SessionResponse objects (from agent client) and SessionResponse objects (from session manager).
        """
        # Both agent client and session manager now return SessionResponse objects
        return session.session_id, session.current_agent_id

    async def process_request_sync(
        self,
        request: Any,
        db: AsyncSession,
        timeout: int = int(os.getenv("AGENT_TIMEOUT", "120")),
    ) -> Dict[str, Any]:
        """Process a request synchronously: accept, wait for turn, process one, return.

        Flow: create RequestLog (status=pending), register future, then
        wait_for_turn_and_process_one (acquire lock, reclaim, dequeue, send, wait).
        """
        # Common request preparation: session, normalize, create RequestLog (status=pending)
        normalized_request, session_id, current_agent_id = await self._prepare_request(
            request, db
        )

        logger.info(
            "Processing request (session serialization)",
            request_id=normalized_request.request_id,
            session_id=session_id,
        )

        # Register future BEFORE wait-for-turn so poller can resolve when another pod processes
        from .session_orchestrator import wait_for_turn_and_process_one

        response = await wait_for_turn_and_process_one(
            session_id=session_id,
            our_request_id=normalized_request.request_id,
            normalized_request=normalized_request,
            db=db,
            strategy_send_request=self.strategy.send_request,
            strategy_wait_for_response=self.strategy.wait_for_response,
            timeout=timeout,
        )

        logger.info(
            "Request processed successfully",
            request_id=normalized_request.request_id,
            session_id=session_id,
            user_id=request.user_id,
        )

        return response

    async def _prepare_request(
        self, request: Any, db: AsyncSession
    ) -> tuple[NormalizedRequest, str, str]:
        """Common request preparation logic: session management, normalization, and RequestLog creation.

        Returns:
            tuple: (normalized_request, session_id, current_agent_id)
        """
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

        # For llama-stack and agent-service, we need to use email instead of canonical UUID
        # Look up user email from canonical user_id and replace in NormalizedRequest
        try:
            from shared_models.models import User
            from shared_models.user_utils import is_uuid
            from sqlalchemy import select

            # Only look up email if user_id is a UUID (canonical user_id)
            if is_uuid(normalized_request.user_id):
                stmt = select(User).where(User.user_id == normalized_request.user_id)
                result = await db.execute(stmt)
                user = result.scalar_one_or_none()
                if user and user.primary_email:
                    user_email = str(user.primary_email)
                    # Replace UUID with email for llama-stack/agent-service communication
                    normalized_request.user_id = user_email
                    logger.debug(
                        "Replaced canonical user_id with email for agent-service",
                        canonical_user_id=request.user_id,
                        user_email=user_email,
                    )
                else:
                    # User has no email - leave UUID as-is
                    # The session_manager will detect it's a UUID and won't use it as authoritative_user_id
                    # This will cause the MCP server to raise an error when no email is available (correct behavior)
                    logger.warning(
                        "User has no email address - cannot perform email-based lookups",
                        canonical_user_id=normalized_request.user_id,
                    )
        except Exception as e:
            logger.warning(
                "Failed to retrieve user email for normalization",
                user_id=normalized_request.user_id,
                error=str(e),
            )
            # If lookup fails and user_id is a UUID, leave it as-is
            # The session_manager will detect it's a UUID and won't use it as authoritative_user_id
            # This will cause the MCP server to raise an error when no email is available (correct behavior)

        # RequestLog insert is Phase 1 of wait_for_turn_and_process_one (inside session lock)
        # for durability + FIFO; future registered there before release so poller can resolve.

        return normalized_request, session_id, current_agent_id
