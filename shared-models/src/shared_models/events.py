"""Shared CloudEvent utilities for all services."""

import asyncio
import hashlib
import os
import random
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import structlog
from cloudevents.conversion import to_structured
from cloudevents.http import CloudEvent

logger = structlog.get_logger()


def _is_transient_error(error: Exception) -> bool:
    """Determine if an error is transient and should be retried."""
    try:
        import httpx
    except ImportError:
        return False
    if isinstance(error, httpx.HTTPStatusError):
        status_code = error.response.status_code
        return status_code >= 500 or status_code in [408, 429]
    if isinstance(error, (httpx.ConnectError, httpx.TimeoutException)):
        return True
    if isinstance(error, OSError):
        return True
    return False


# CloudEvent type constants
class EventTypes:
    """CloudEvent type constants for the self-service agent system."""

    # Request events
    REQUEST_CREATED = "com.self-service-agent.request.created"
    REQUEST_PROCESSING = "com.self-service-agent.request.processing"

    # Response events
    AGENT_RESPONSE_READY = "com.self-service-agent.agent.response-ready"

    # Database update events
    DATABASE_UPDATE_REQUESTED = "com.self-service-agent.request.database-update"

    # Session events
    SESSION_CREATE_OR_GET = "com.self-service-agent.session.create-or-get"
    SESSION_READY = "com.self-service-agent.session.ready"


def _broker_safe_event_id(request_id: str) -> str:
    """Produce a broker-safe event_id from request_id.

    Email Message-IDs (e.g. <CAPbJ+...@mail.gmail.com>) contain <, >, @, +, =
    that can cause issues with Kafka/Knative brokers. Use a hash-based ID instead.
    """
    if not request_id:
        return str(uuid.uuid4())
    if request_id.startswith("<") and "@" in request_id:
        digest = hashlib.sha256(request_id.encode()).hexdigest()[:32]
        return f"email-{digest}"
    return request_id


def agent_response_event_id(request_id: str) -> str:
    """Return event_id for AGENT_RESPONSE_READY CloudEvents.

    Namespaced to avoid collision with REQUEST_CREATED (event_id=request_id).
    Uses broker-safe form for email Message-IDs. Use when claiming or recording
    AGENT_RESPONSE_READY in processed_events.
    """
    return f"agent-response:{_broker_safe_event_id(request_id)}"


class CloudEventBuilder:
    """Builder for creating standardized CloudEvents."""

    def __init__(self, source: str, service_name: str = "self-service-agent"):
        self.source = source
        self.service_name = service_name
        self.base_attributes = {
            "source": source,
            "specversion": "1.0",
            "datacontenttype": "application/json",
        }

    def create_request_event(
        self,
        request_data: Dict[str, Any],
        request_id: Optional[str] = None,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> CloudEvent:
        """Create a request created event.

        Uses broker-safe event_id for email Message-IDs (e.g. <...@mail.gmail.com>)
        to avoid Kafka/Knative issues with special characters.
        """
        raw_id = request_id or str(uuid.uuid4())
        event_id = _broker_safe_event_id(raw_id)

        attributes = {
            **self.base_attributes,
            "type": EventTypes.REQUEST_CREATED,
            "id": event_id,
            "time": datetime.now(timezone.utc).isoformat(),
        }

        # Add optional attributes
        if user_id:
            attributes["userid"] = user_id
        if session_id:
            attributes["sessionid"] = session_id

        # Partition key for Kafka ordering (session_id preferred; user_id for first request)
        # Use both partitionkey and partitionKey for Knative Kafka broker compatibility
        partition_key = session_id or user_id
        if partition_key:
            pk = str(partition_key)
            attributes["partitionkey"] = pk
            attributes["partitionKey"] = pk  # camelCase for some brokers

        return CloudEvent(attributes, request_data)

    def create_response_event(
        self,
        response_data: Dict[str, Any],
        request_id: str,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> CloudEvent:
        """Create an agent response ready event.

        Uses namespaced event id (agent-response:{request_id}) to avoid collision with
        REQUEST_CREATED, which uses event_id=request_id. Both are claimed by
        request-manager; distinct event_ids allow each to be claimed. request_id in
        the payload remains the end-to-end trace key for correlation.
        """
        attributes = {
            **self.base_attributes,
            "type": EventTypes.AGENT_RESPONSE_READY,
            "id": agent_response_event_id(request_id),
            "time": datetime.now(timezone.utc).isoformat(),
            "requestid": request_id,
        }

        # Add optional attributes
        if agent_id:
            attributes["agentid"] = agent_id
        if session_id:
            attributes["sessionid"] = session_id

        # Partition key for Kafka ordering (required for agent responses)
        if session_id:
            pk = str(session_id)
            attributes["partitionkey"] = pk
            attributes["partitionKey"] = pk  # camelCase for some brokers

        return CloudEvent(attributes, response_data)

    def create_session_create_or_get_event(
        self,
        session_data: Dict[str, Any],
        event_id: Optional[str] = None,
        user_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> CloudEvent:
        """Create a session create-or-get event."""
        event_id = event_id or str(uuid.uuid4())

        attributes = {
            **self.base_attributes,
            "type": EventTypes.SESSION_CREATE_OR_GET,
            "id": event_id,
            "time": datetime.now(timezone.utc).isoformat(),
        }

        # Add optional attributes
        if user_id:
            attributes["userid"] = user_id
        if correlation_id:
            attributes["correlationid"] = correlation_id

        return CloudEvent(attributes, session_data)

    def create_session_ready_event(
        self,
        session_data: Dict[str, Any],
        event_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> CloudEvent:
        """Create a session ready event."""
        attributes = {
            **self.base_attributes,
            "type": EventTypes.SESSION_READY,
            "id": event_id or str(uuid.uuid4()),
            "time": datetime.now(timezone.utc).isoformat(),
        }

        # Add optional attributes
        if correlation_id:
            attributes["correlationid"] = correlation_id
        if session_id:
            attributes["sessionid"] = session_id

        return CloudEvent(attributes, session_data)


class CloudEventSender:
    """Sender for CloudEvents to brokers with retry on transient failures."""

    def __init__(self, broker_url: str, service_name: str):
        self.broker_url = broker_url
        self.service_name = service_name
        self.builder = CloudEventBuilder(service_name, service_name)
        self.max_retries = int(os.getenv("EVENT_MAX_RETRIES", "3"))
        self.base_delay = float(os.getenv("EVENT_BASE_DELAY", "1.0"))
        self.max_delay = float(os.getenv("EVENT_MAX_DELAY", "10.0"))
        self.backoff_multiplier = float(os.getenv("EVENT_BACKOFF_MULTIPLIER", "2.0"))

    async def send_request_event(
        self,
        request_data: Dict[str, Any],
        request_id: Optional[str] = None,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        max_retries: Optional[int] = None,
    ) -> bool:
        """Send a request created event.

        Args:
            max_retries: Override instance default for this call. Use 0 for Slack
                (fail fast, let Slack retry webhook). Omit for Email/sync paths.
        """
        try:
            event = self.builder.create_request_event(
                request_data,
                request_id,
                user_id,
                session_id,
            )
            return await self._send_event(event, max_retries=max_retries)
        except Exception as e:
            logger.error("Failed to send request event", error=str(e))
            return False

    async def send_response_event(
        self,
        response_data: Dict[str, Any],
        request_id: str,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> bool:
        """Send an agent response ready event."""
        try:
            event = self.builder.create_response_event(
                response_data, request_id, agent_id, session_id
            )
            return await self._send_event(event)
        except Exception as e:
            logger.error("Failed to send response event", error=str(e))
            return False

    async def send_session_create_or_get_event(
        self,
        session_data: Dict[str, Any],
        event_id: Optional[str] = None,
        user_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> bool:
        """Send a session create-or-get event."""
        try:
            event = self.builder.create_session_create_or_get_event(
                session_data, event_id, user_id, correlation_id
            )
            return await self._send_event(event)
        except Exception as e:
            logger.error("Failed to send session create-or-get event", error=str(e))
            return False

    async def send_session_ready_event(
        self,
        session_data: Dict[str, Any],
        event_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> bool:
        """Send a session ready event."""
        try:
            event = self.builder.create_session_ready_event(
                session_data, event_id, correlation_id, session_id
            )
            return await self._send_event(event)
        except Exception as e:
            logger.error("Failed to send session ready event", error=str(e))
            return False

    async def _send_event(
        self, event: CloudEvent, max_retries: Optional[int] = None
    ) -> bool:
        """Send a CloudEvent to the broker with retry on transient failures."""
        import httpx

        effective_retries = max_retries if max_retries is not None else self.max_retries
        headers, data = to_structured(event)
        headers = dict(headers)
        partition_key = event.get("partitionkey") or event.get("partitionKey")
        if partition_key:
            headers["ce-partitionkey"] = str(partition_key)

        last_error = None
        for attempt in range(effective_retries + 1):
            try:
                logger.debug(
                    "Sending CloudEvent to broker",
                    broker_url=self.broker_url,
                    event_type=event["type"],
                    event_id=event["id"],
                    attempt=attempt + 1,
                    max_attempts=effective_retries + 1,
                )

                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        self.broker_url,
                        headers=headers,
                        content=data,
                        timeout=30.0,
                    )
                    logger.debug(
                        "HTTP response received",
                        status_code=response.status_code,
                        broker_url=self.broker_url,
                    )
                    response.raise_for_status()

                    logger.debug(
                        "CloudEvent sent successfully",
                        event_type=event["type"],
                        event_id=event["id"],
                        status_code=response.status_code,
                    )
                    return True

            except Exception as e:
                last_error = e
                logger.warning(
                    "Failed to send CloudEvent",
                    event_type=event["type"],
                    event_id=event["id"],
                    attempt=attempt + 1,
                    max_attempts=effective_retries + 1,
                    error=str(e),
                    error_type=type(e).__name__,
                )

                if attempt < effective_retries and _is_transient_error(e):
                    delay = min(
                        self.base_delay * (self.backoff_multiplier**attempt),
                        self.max_delay,
                    )
                    jitter = delay * 0.1 * (2 * random.random() - 1)
                    delay = max(0.1, delay + jitter)
                    logger.info(
                        "Retrying CloudEvent send after delay",
                        event_type=event["type"],
                        event_id=event["id"],
                        delay_seconds=round(delay, 2),
                        next_attempt=attempt + 2,
                    )
                    await asyncio.sleep(delay)
                else:
                    break

        logger.error(
            "Failed to send CloudEvent after all retries",
            event_type=event["type"],
            event_id=event["id"],
            total_attempts=effective_retries + 1,
            final_error=str(last_error),
            error_type=type(last_error).__name__ if last_error else None,
        )
        return False
