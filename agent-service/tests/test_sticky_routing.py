"""Tests for Zammad sticky ticket track (routing_decision vs persisted sticky_routing_agent)."""

from agent_service.session_manager import (
    STICKY_AGENT_GENERAL,
    STICKY_AGENT_LAPTOP,
    _zammad_apply_sticky_ticket_track,
)

ROUTING = "routing-agent"


def test_no_sticky_passes_through() -> None:
    assert (
        _zammad_apply_sticky_ticket_track(
            STICKY_AGENT_GENERAL,
            None,
            routing_agent_name=ROUTING,
        )
        == STICKY_AGENT_GENERAL
    )


def test_no_routed_passes_through() -> None:
    assert (
        _zammad_apply_sticky_ticket_track(
            None,
            STICKY_AGENT_LAPTOP,
            routing_agent_name=ROUTING,
        )
        is None
    )


def test_already_matches_sticky() -> None:
    assert (
        _zammad_apply_sticky_ticket_track(
            STICKY_AGENT_LAPTOP,
            STICKY_AGENT_LAPTOP,
            routing_agent_name=ROUTING,
        )
        == STICKY_AGENT_LAPTOP
    )


def test_conflict_overrides_to_sticky() -> None:
    assert (
        _zammad_apply_sticky_ticket_track(
            STICKY_AGENT_GENERAL,
            STICKY_AGENT_LAPTOP,
            routing_agent_name=ROUTING,
        )
        == STICKY_AGENT_LAPTOP
    )


def test_return_to_router_cleared_when_track_locked() -> None:
    assert (
        _zammad_apply_sticky_ticket_track(
            ROUTING,
            STICKY_AGENT_LAPTOP,
            routing_agent_name=ROUTING,
        )
        is None
    )
