"""Tests for Zammad REST delivery handler."""

import os
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from integration_dispatcher.integrations.zammad import ZammadIntegrationHandler
from shared_models.models import DeliveryRequest, DeliveryStatus, UserIntegrationConfig


@pytest.fixture
def handler() -> ZammadIntegrationHandler:
    return ZammadIntegrationHandler()


@pytest.fixture
def mock_config() -> UserIntegrationConfig:
    # Handler only needs a config object for the signature; deliver() does not read fields.
    return cast(UserIntegrationConfig, MagicMock())


@pytest.mark.asyncio
async def test_skips_when_not_zammad_platform(
    handler: ZammadIntegrationHandler,
    mock_config: UserIntegrationConfig,
) -> None:
    req = DeliveryRequest(
        request_id="r1",
        session_id="s1",
        user_id="user@test.com-81",
        content="Hello",
        integration_context={"platform": "slack"},
    )
    result = await handler.deliver(req, mock_config, {"subject": "", "body": "Hello"})
    assert result.success is True
    assert result.metadata.get("delivery_method") == "zammad_skip_non_ticket"


@pytest.mark.asyncio
async def test_posts_ticket_article(
    handler: ZammadIntegrationHandler,
    mock_config: UserIntegrationConfig,
) -> None:
    req = DeliveryRequest(
        request_id="r1",
        session_id="zammad-81",
        user_id="user@test.com-81",
        content="Visible reply",
        integration_context={"platform": "zammad", "ticket_id": 81},
    )
    mock_resp = MagicMock()
    mock_resp.status_code = 201
    mock_resp.json.return_value = {"id": 999}
    mock_resp.raise_for_status = MagicMock()

    with patch.dict(
        os.environ,
        {
            "ZAMMAD_URL": "http://zammad.example.com",
            "ZAMMAD_HTTP_TOKEN": "secret-token",
        },
        clear=False,
    ):
        with patch("httpx.AsyncClient") as mock_client_cls:
            instance = MagicMock()
            instance.post = AsyncMock(return_value=mock_resp)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = instance

            result = await handler.deliver(
                req, mock_config, {"subject": "", "body": "Visible reply"}
            )

    assert result.success is True
    assert result.status == DeliveryStatus.DELIVERED
    assert result.metadata.get("zammad_article_id") == 999
    instance.post.assert_awaited_once()
    call_kw = instance.post.await_args
    assert "ticket_articles" in str(call_kw[0][0])
    payload = call_kw[1]["json"]
    assert payload["ticket_id"] == 81
    assert payload["internal"] is False
    assert payload["body"] == "Visible reply"


@pytest.mark.asyncio
async def test_fails_without_credentials(
    handler: ZammadIntegrationHandler,
    mock_config: UserIntegrationConfig,
) -> None:
    req = DeliveryRequest(
        request_id="r1",
        session_id="zammad-81",
        user_id="u1",
        content="Hi",
        integration_context={"platform": "zammad", "ticket_id": 5},
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("ZAMMAD_")}
    with patch.dict(os.environ, env, clear=True):
        result = await handler.deliver(req, mock_config, {"body": "Hi"})
    assert result.success is False
