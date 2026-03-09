"""Feedback-loop skip when article author is the configured AI agent user."""

from unittest.mock import AsyncMock, patch

import pytest
from integration_dispatcher.zammad_service import ZammadService


@pytest.fixture
def zammad_svc(monkeypatch: pytest.MonkeyPatch) -> ZammadService:
    monkeypatch.setenv("BROKER_URL", "http://mock-broker.test/broker")
    monkeypatch.setenv("ZAMMAD_AI_AGENT_USER_ID", "42")
    return ZammadService()


@pytest.mark.asyncio
async def test_skips_forward_when_article_created_by_ai_agent_user(
    zammad_svc: ZammadService,
) -> None:
    payload = {
        "ticket": {
            "id": 10,
            "group_id": 1,
            "customer": {"email": "user@example.com", "id": 5},
            "customer_id": 5,
            "tags": [],
            "state": {"name": "open"},
        },
        "article": {
            "id": 20,
            "body": "Automated reply body",
            "internal": False,
            "created_by_id": 42,
        },
    }
    db = AsyncMock()
    with patch(
        "integration_dispatcher.zammad_service.DatabaseUtils.try_claim_event_for_processing",
        new_callable=AsyncMock,
        return_value=True,
    ):
        with patch(
            "integration_dispatcher.zammad_service.insert_outbox_event",
            new_callable=AsyncMock,
        ) as mock_outbox:
            await zammad_svc.handle_webhook(
                payload,
                delivery_id="del-feedback-1",
                trigger_header="t1",
                db_session=db,
            )
            mock_outbox.assert_not_called()
