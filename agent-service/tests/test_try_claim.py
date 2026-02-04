"""Tests for agent try_claim / duplicate skip behavior."""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_duplicate_event_skipped_when_try_claim_returns_false() -> None:
    """When try_claim returns False (event already claimed), handler returns skip response."""
    # Import here to avoid pulling in full app bootstrap
    from agent_service.main import _handle_event_with_try_claim

    mock_db = AsyncMock(spec=AsyncSession)

    with patch(
        "agent_service.main.DatabaseUtils.try_claim_event_for_processing",
        new_callable=AsyncMock,
        return_value=False,
    ):
        result = await _handle_event_with_try_claim(
            db=mock_db,
            event_id="evt-123",
            event_type="com.self-service-agent.request.created",
            event_source="test",
            event_data={},
            handler=AsyncMock(return_value={"request_id": "r1", "session_id": "s1"}),
            extract_ids=lambda r, e: (r.get("request_id"), r.get("session_id")),
        )

    assert result == {
        "status": "skipped",
        "reason": "duplicate event (already claimed)",
        "event_id": "evt-123",
    }
