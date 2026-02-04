"""Tests for CloudEventSender retry behavior."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from shared_models import CloudEventSender


class TestCloudEventSenderMaxRetries:
    """Tests for send_request_event max_retries parameter."""

    @pytest.mark.asyncio
    async def test_max_retries_zero_single_attempt(self) -> None:
        """With max_retries=0, only 1 attempt is made (no retry on failure)."""
        post_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

        async def mock_post(*args: object, **kwargs: object) -> MagicMock:
            post_calls.append((args, kwargs))
            resp = MagicMock()
            resp.raise_for_status = MagicMock(
                side_effect=Exception("simulated failure")
            )
            return resp

        with patch(
            "httpx.AsyncClient",
            return_value=MagicMock(
                __aenter__=AsyncMock(
                    return_value=MagicMock(post=AsyncMock(side_effect=mock_post))
                ),
                __aexit__=AsyncMock(return_value=None),
            ),
        ):
            sender = CloudEventSender("http://broker.example/", "test-service")
            result = await sender.send_request_event(
                {"content": "test"},
                max_retries=0,
            )

        assert result is False
        assert len(post_calls) == 1

    @pytest.mark.asyncio
    async def test_default_retries_multiple_attempts(self) -> None:
        """With default max_retries=3, 4 attempts on transient failure."""
        post_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

        async def mock_post(*args: object, **kwargs: object) -> MagicMock:
            post_calls.append((args, kwargs))
            resp = MagicMock()
            resp.status_code = 503
            resp.raise_for_status = MagicMock(
                side_effect=httpx.HTTPStatusError(
                    "503", request=MagicMock(), response=resp
                )
            )
            return resp

        with (
            patch(
                "httpx.AsyncClient",
                return_value=MagicMock(
                    __aenter__=AsyncMock(
                        return_value=MagicMock(post=AsyncMock(side_effect=mock_post))
                    ),
                    __aexit__=AsyncMock(return_value=None),
                ),
            ),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            sender = CloudEventSender("http://broker.example/", "test-service")
            result = await sender.send_request_event({"content": "test"})

        assert result is False
        assert len(post_calls) == 4  # 1 + 3 retries
