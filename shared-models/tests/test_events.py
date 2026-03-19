"""Tests for event utilities (broker-safe event IDs, etc.)."""

import hashlib

from shared_models import CloudEventBuilder, agent_response_event_id


class TestBrokerSafeEventId:
    """Tests for broker-safe event_id handling of email Message-IDs."""

    def test_slack_request_id_passthrough(self) -> None:
        """Slack-style request_id (URL-safe) passes through unchanged."""
        builder = CloudEventBuilder("integration-dispatcher")
        event = builder.create_request_event(
            {"content": "hello"},
            request_id="slack-event-Ev0AKXS2JBB6",
        )
        assert event["id"] == "slack-event-Ev0AKXS2JBB6"

    def test_email_message_id_sanitized(self) -> None:
        """Email Message-ID format (<...@domain>) gets hashed for broker safety."""
        msg_id = "<CAPbJ+-1=_EWJZQcjsjvK7jZB_Zm_bPKAfmJzAunJEHYz+FtO0A@mail.gmail.com>"
        builder = CloudEventBuilder("integration-dispatcher")
        event = builder.create_request_event(
            {"content": "hello"},
            request_id=msg_id,
        )
        assert event["id"] != msg_id
        assert event["id"].startswith("email-")
        assert len(event["id"]) == len("email-") + 32  # sha256 hex[:32]
        digest = hashlib.sha256(msg_id.encode()).hexdigest()[:32]
        assert event["id"] == f"email-{digest}"

    def test_agent_response_event_id_email_safe(self) -> None:
        """agent_response_event_id produces broker-safe form for email."""
        msg_id = "<CAPbJ+...@mail.gmail.com>"
        got = agent_response_event_id(msg_id)
        assert got.startswith("agent-response:email-")
        assert "agent-response:" in got
        # Must be deterministic for same msg_id
        assert got == agent_response_event_id(msg_id)

    def test_agent_response_event_id_slack_passthrough(self) -> None:
        """agent_response_event_id passes through non-email request_ids."""
        req_id = "slack-event-ABC123"
        got = agent_response_event_id(req_id)
        assert got == "agent-response:slack-event-ABC123"
