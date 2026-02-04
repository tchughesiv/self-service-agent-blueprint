"""Tests for thread lock key builders."""

from integration_dispatcher.thread_lock import (
    build_email_thread_key,
    build_slack_thread_key,
)


class TestBuildSlackThreadKey:
    """Tests for build_slack_thread_key."""

    def test_build_slack_thread_key(self) -> None:
        """Key format: integration:slack:{team_id}:{channel_id}:{thread_ts}."""
        key = build_slack_thread_key("T1", "C1", "123.456")
        assert key == "integration:slack:T1:C1:123.456"

    def test_same_inputs_same_key(self) -> None:
        """Same inputs produce same key (stability for lock consistency)."""
        k1 = build_slack_thread_key("T1", "C1", "123")
        k2 = build_slack_thread_key("T1", "C1", "123")
        assert k1 == k2

    def test_different_inputs_different_keys(self) -> None:
        """Different inputs produce different keys."""
        k1 = build_slack_thread_key("T1", "C1", "123")
        k2 = build_slack_thread_key("T1", "C1", "456")
        k3 = build_slack_thread_key("T2", "C1", "123")
        assert k1 != k2
        assert k1 != k3
        assert k2 != k3


class TestBuildEmailThreadKey:
    """Tests for build_email_thread_key."""

    def test_in_reply_to_preferred(self) -> None:
        """When in_reply_to present, use it as thread part."""
        key = build_email_thread_key(
            "a@b.com", in_reply_to="<ref123>", message_id="mid"
        )
        assert key == "integration:email:a@b.com:<ref123>"

    def test_message_id_when_no_in_reply_to(self) -> None:
        """When in_reply_to missing, use message_id."""
        key = build_email_thread_key("a@b.com", in_reply_to=None, message_id="msg-456")
        assert key == "integration:email:a@b.com:msg-456"

    def test_empty_in_reply_to_uses_message_id(self) -> None:
        """Empty in_reply_to treated as missing; fallback to message_id."""
        key = build_email_thread_key("a@b.com", in_reply_to="", message_id="mid")
        assert key == "integration:email:a@b.com:mid"

    def test_fallback_to_first_when_both_none(self) -> None:
        """When both in_reply_to and message_id are None, use 'first' fallback."""
        key = build_email_thread_key("a@b.com", in_reply_to=None, message_id=None)
        assert key == "integration:email:a@b.com:first"

    def test_same_inputs_same_key(self) -> None:
        """Same inputs produce same key."""
        k1 = build_email_thread_key("a@b.com", in_reply_to="ref1")
        k2 = build_email_thread_key("a@b.com", in_reply_to="ref1")
        assert k1 == k2
