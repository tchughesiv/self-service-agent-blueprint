"""CloudEvents integration for event-driven communication."""

import json
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

import httpx
from cloudevents.http import CloudEvent, to_structured
from cloudevents.http.event import CloudEvent as CloudEventType
from pydantic import BaseModel

from .schemas import AgentResponse, NormalizedRequest


class EventConfig:
    """Configuration for event handling."""

    def __init__(self) -> None:
        # Knative Broker endpoint - will be set via environment variables
        self.broker_url = "http://broker-ingress.knative-eventing.svc.cluster.local"
        self.source = "request-manager"
        self.timeout = 30.0


class CloudEventPublisher:
    """Publishes CloudEvents to Knative Broker."""

    def __init__(self, config: EventConfig) -> None:
        self.config = config
        self.client = httpx.AsyncClient(timeout=config.timeout)

    async def publish_request_event(
        self,
        normalized_request: NormalizedRequest,
        event_type: str = "com.self-service-agent.request.created",
    ) -> bool:
        """Publish a normalized request as a CloudEvent."""
        event_data = {
            "request_id": normalized_request.request_id,
            "session_id": normalized_request.session_id,
            "user_id": normalized_request.user_id,
            "integration_type": normalized_request.integration_type.value,
            "request_type": normalized_request.request_type,
            "content": normalized_request.content,
            "integration_context": normalized_request.integration_context,
            "user_context": normalized_request.user_context,
            "target_agent_id": normalized_request.target_agent_id,
            "requires_routing": normalized_request.requires_routing,
            "created_at": normalized_request.created_at.isoformat(),
        }

        event = CloudEvent(
            {
                "type": event_type,
                "source": self.config.source,
                "id": str(uuid.uuid4()),
                "time": datetime.utcnow().isoformat() + "Z",
                "subject": f"session/{normalized_request.session_id}",
                "datacontenttype": "application/json",
            },
            event_data,
        )

        return await self._publish_event(event)

    async def publish_response_event(
        self,
        agent_response: AgentResponse,
        event_type: str = "com.self-service-agent.response.created",
    ) -> bool:
        """Publish an agent response as a CloudEvent."""
        event_data = {
            "request_id": agent_response.request_id,
            "session_id": agent_response.session_id,
            "agent_id": agent_response.agent_id,
            "content": agent_response.content,
            "response_type": agent_response.response_type,
            "metadata": agent_response.metadata,
            "processing_time_ms": agent_response.processing_time_ms,
            "requires_followup": agent_response.requires_followup,
            "followup_actions": agent_response.followup_actions,
            "created_at": agent_response.created_at.isoformat(),
        }

        event = CloudEvent(
            {
                "type": event_type,
                "source": self.config.source,
                "id": str(uuid.uuid4()),
                "time": datetime.utcnow().isoformat() + "Z",
                "subject": f"session/{agent_response.session_id}",
                "datacontenttype": "application/json",
            },
            event_data,
        )

        return await self._publish_event(event)

    async def publish_session_event(
        self,
        session_id: str,
        event_type: str,
        event_data: Dict[str, Any],
    ) -> bool:
        """Publish a session-related event."""
        event = CloudEvent(
            {
                "type": event_type,
                "source": self.config.source,
                "id": str(uuid.uuid4()),
                "time": datetime.utcnow().isoformat() + "Z",
                "subject": f"session/{session_id}",
                "datacontenttype": "application/json",
            },
            event_data,
        )

        return await self._publish_event(event)

    async def _publish_event(self, event: CloudEventType) -> bool:
        """Publish a CloudEvent to the broker."""
        try:
            headers, body = to_structured(event)
            
            response = await self.client.post(
                self.config.broker_url,
                headers=headers,
                content=body,
            )
            
            response.raise_for_status()
            return True
            
        except Exception as e:
            # Log error (in production, use proper logging)
            print(f"Failed to publish event: {e}")
            return False

    async def close(self) -> None:
        """Close the HTTP client."""
        await self.client.aclose()


class CloudEventHandler:
    """Handles incoming CloudEvents."""

    def __init__(self) -> None:
        self.handlers: Dict[str, callable] = {}

    def register_handler(self, event_type: str, handler: callable) -> None:
        """Register a handler for a specific event type."""
        self.handlers[event_type] = handler

    async def handle_event(self, event: CloudEventType) -> Optional[Dict[str, Any]]:
        """Handle an incoming CloudEvent."""
        event_type = event.get_type()
        
        if event_type not in self.handlers:
            print(f"No handler registered for event type: {event_type}")
            return None

        handler = self.handlers[event_type]
        
        try:
            return await handler(event)
        except Exception as e:
            print(f"Error handling event {event_type}: {e}")
            return None

    def parse_cloudevent_from_request(
        self, headers: Dict[str, str], body: bytes
    ) -> Optional[CloudEventType]:
        """Parse a CloudEvent from HTTP request headers and body."""
        try:
            # Try structured content first
            if headers.get("content-type", "").startswith("application/cloudevents+json"):
                event_data = json.loads(body)
                return CloudEvent(event_data)
            
            # Try binary content
            ce_headers = {
                key[3:].lower(): value  # Remove 'ce-' prefix and lowercase
                for key, value in headers.items()
                if key.lower().startswith("ce-")
            }
            
            if ce_headers:
                return CloudEvent(ce_headers, json.loads(body) if body else None)
                
            return None
            
        except Exception as e:
            print(f"Failed to parse CloudEvent: {e}")
            return None


# Event type constants
class EventTypes:
    """CloudEvent type constants."""

    # Request events
    REQUEST_CREATED = "com.self-service-agent.request.created"
    REQUEST_PROCESSED = "com.self-service-agent.request.processed"
    REQUEST_FAILED = "com.self-service-agent.request.failed"
    
    # Response events
    RESPONSE_CREATED = "com.self-service-agent.response.created"
    RESPONSE_DELIVERED = "com.self-service-agent.response.delivered"
    RESPONSE_FAILED = "com.self-service-agent.response.failed"
    
    # Session events
    SESSION_CREATED = "com.self-service-agent.session.created"
    SESSION_UPDATED = "com.self-service-agent.session.updated"
    SESSION_ENDED = "com.self-service-agent.session.ended"
    
    # Agent events
    AGENT_ROUTED = "com.self-service-agent.agent.routed"
    AGENT_RESPONSE_READY = "com.self-service-agent.agent.response-ready"


# Global event publisher instance
_event_publisher: Optional[CloudEventPublisher] = None


def get_event_publisher() -> CloudEventPublisher:
    """Get the global event publisher instance."""
    global _event_publisher
    if _event_publisher is None:
        config = EventConfig()
        _event_publisher = CloudEventPublisher(config)
    return _event_publisher
