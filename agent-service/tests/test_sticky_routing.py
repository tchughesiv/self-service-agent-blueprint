"""Tests for ticketing sticky laptop intent heuristics."""

from agent_service.session_manager import (
    _text_suggests_non_laptop_topic,
    sticky_prefer_laptop_over_general,
)


def test_sticky_help_prefers_laptop() -> None:
    assert sticky_prefer_laptop_over_general("help") is True


def test_sticky_short_followup_prefers_laptop() -> None:
    assert sticky_prefer_laptop_over_general("thanks") is True


def test_explicit_non_laptop_topic() -> None:
    assert (
        sticky_prefer_laptop_over_general(
            "The office printer is jammed on floor 3, please send someone"
        )
        is False
    )


def test_laptop_refresh_still_prefers_laptop() -> None:
    assert sticky_prefer_laptop_over_general("i need my laptop refreshed") is True


def test_non_laptop_marker_detected() -> None:
    assert _text_suggests_non_laptop_topic("password reset for my account") is True


def test_laptop_keyword_overrides_blocked_phrase() -> None:
    assert _text_suggests_non_laptop_topic("vpn on laptop not working") is False
