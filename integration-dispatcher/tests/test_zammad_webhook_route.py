"""Route tests for POST /zammad/webhook (APPENG-4759).

Uses a minimal FastAPI app with only this route so tests do not run the full
integration-dispatcher lifespan (DB migration, integration defaults, IMAP, outbox).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Iterator
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from integration_dispatcher.main import handle_zammad_webhook
from shared_models import get_db_session_dependency


@pytest.fixture
def client() -> Iterator[TestClient]:
    async def _mock_db() -> AsyncGenerator[AsyncMock, None]:
        yield AsyncMock()

    mini = FastAPI()
    mini.add_api_route("/zammad/webhook", handle_zammad_webhook, methods=["POST"])
    mini.dependency_overrides[get_db_session_dependency] = _mock_db
    with TestClient(mini) as c:
        yield c
    mini.dependency_overrides.clear()


def test_zammad_webhook_401_when_signature_invalid(client: TestClient) -> None:
    with patch("integration_dispatcher.main.zammad_service") as zsvc:
        zsvc.verify_signature.return_value = False
        r = client.post(
            "/zammad/webhook",
            content=b"{}",
            headers={
                "Content-Type": "application/json",
                "X-Zammad-Delivery": "del-1",
                "X-Hub-Signature": "sha1=bad",
            },
        )
        assert r.status_code == 401


def test_zammad_webhook_400_when_delivery_header_missing(client: TestClient) -> None:
    with patch("integration_dispatcher.main.zammad_service") as zsvc:
        zsvc.verify_signature.return_value = True
        r = client.post(
            "/zammad/webhook",
            content=b"{}",
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 400


def test_zammad_webhook_400_when_body_not_json_object(client: TestClient) -> None:
    with patch("integration_dispatcher.main.zammad_service") as zsvc:
        zsvc.verify_signature.return_value = True
        r = client.post(
            "/zammad/webhook",
            content=b'"not-an-object"',
            headers={
                "Content-Type": "application/json",
                "X-Zammad-Delivery": "del-2",
            },
        )
        assert r.status_code == 400


def test_zammad_webhook_200(client: TestClient) -> None:
    with patch("integration_dispatcher.main.zammad_service") as zsvc:
        zsvc.verify_signature.return_value = True
        zsvc.handle_webhook = AsyncMock()
        body = b'{"ticket":{"id":1},"article":{"id":2,"body":"hi"}}'
        r = client.post(
            "/zammad/webhook",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Zammad-Delivery": "del-3",
                "X-Zammad-Trigger": "t1",
            },
        )
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}
        zsvc.handle_webhook.assert_awaited_once()
