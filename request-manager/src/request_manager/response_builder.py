"""Shared response payload construction for event and database delivery."""

from typing import Any, Dict


def build_response_data_from_request_log(
    request_log: Any,
    *,
    from_event: bool = False,
) -> Dict[str, Any]:
    """Build response_data dict from RequestLog row for poller or event handler.

    Used by _pod_response_poller when resolving futures from database.
    Structure matches what resolve_response_future expects.
    """
    response_metadata = cast_to_dict(getattr(request_log, "response_metadata", None))
    created_at = getattr(request_log, "created_at", None)
    return {
        "request_id": str(getattr(request_log, "request_id", "")),
        "session_id": str(getattr(request_log, "session_id", "")),
        "agent_id": getattr(request_log, "agent_id", None),
        "content": getattr(request_log, "response_content", None),
        "metadata": response_metadata,
        "processing_time_ms": getattr(request_log, "processing_time_ms", None),
        "requires_followup": False,
        "followup_actions": [],
        "created_at": (created_at.isoformat() if created_at else None),
        "agent_received_at": response_metadata.get("agent_received_at"),
        "_from_event": from_event,
    }


def build_response_data_from_event_data(
    response_data: Dict[str, Any],
    *,
    created_at_iso: str | None = None,
) -> Dict[str, Any]:
    """Build response_data dict from event payload for resolve_response_future.

    Used by _handle_agent_response_event_from_data when resolving futures via event.
    Structure matches what resolve_response_future expects (same as poller).
    """
    metadata = cast_to_dict(response_data.get("metadata"))
    if response_data.get("agent_received_at"):
        metadata["agent_received_at"] = response_data.get("agent_received_at")
    return {
        "request_id": response_data.get("request_id"),
        "session_id": response_data.get("session_id"),
        "agent_id": response_data.get("agent_id"),
        "content": response_data.get("content"),
        "metadata": metadata,
        "processing_time_ms": response_data.get("processing_time_ms"),
        "requires_followup": response_data.get("requires_followup", False),
        "followup_actions": response_data.get("followup_actions", []),
        "created_at": created_at_iso or response_data.get("created_at"),
        "agent_received_at": response_data.get("agent_received_at"),
        "_from_event": True,
    }


def cast_to_dict(val: Any) -> Dict[str, Any]:
    """Safely cast to dict for response_metadata."""
    if val is None:
        return {}
    if isinstance(val, dict):
        return val
    return {}
