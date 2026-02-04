"""Unit tests for session request serialization.

Covers: session lock key derivation, reconstruct_normalized_request,
acquire/release session lock with mocked DB, SessionLockTimeoutError → 503.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from request_manager.exceptions import SessionLockTimeoutError
from request_manager.main import app
from request_manager.session_lock import (
    _session_id_to_lock_key,
    acquire_session_lock,
    release_session_lock,
)
from request_manager.session_orchestrator import reconstruct_normalized_request
from shared_models.models import IntegrationType


class TestSessionLockKeyDerivation:
    """Test session_id to advisory lock key mapping."""

    def test_same_session_id_produces_same_key(self) -> None:
        """Same session_id must always map to the same bigint key."""
        session_id = "550e8400-e29b-41d4-a716-446655440000"
        key1 = _session_id_to_lock_key(session_id)
        key2 = _session_id_to_lock_key(session_id)
        assert key1 == key2

    def test_different_session_ids_produce_different_keys(self) -> None:
        """Different session_ids should map to different keys (collision avoidance).

        Use UUIDs that differ in the first 16 hex chars (used for key derivation).
        """
        key1 = _session_id_to_lock_key("550e8400-e29b-41d4-a716-446655440000")
        key2 = _session_id_to_lock_key("660e8400-e29b-41d4-a716-446655440000")
        assert key1 != key2

    def test_key_is_non_negative(self) -> None:
        """Key must be non-negative (PostgreSQL advisory lock constraint)."""
        for sid in [
            "550e8400-e29b-41d4-a716-446655440000",
            "ffffffff-ffff-ffff-ffff-ffffffffffff",
        ]:
            key = _session_id_to_lock_key(sid)
            assert key >= 0
            assert key <= 0x7FFF_FFFF_FFFF_FFFF

    def test_uuid_format(self) -> None:
        """Key from standard UUID matches plan formula."""
        session_id = "550e8400-e29b-41d4-a716-446655440000"
        key = _session_id_to_lock_key(session_id)
        expected = int(uuid.UUID(session_id).hex[:16], 16) & 0x7FFF_FFFF_FFFF_FFFF
        assert key == expected

    def test_non_uuid_fallback(self) -> None:
        """Non-UUID session_ids use deterministic SHA-256 fallback."""
        key = _session_id_to_lock_key("non-uuid-session-123")
        assert isinstance(key, int)
        assert key >= 0
        assert key <= 0x7FFF_FFFF_FFFF_FFFF

    def test_non_uuid_deterministic(self) -> None:
        """Same non-UUID session_id always produces same key (required for multi-pod locking)."""
        sid = "non-uuid-session-456"
        key1 = _session_id_to_lock_key(sid)
        key2 = _session_id_to_lock_key(sid)
        assert key1 == key2


class TestReconstructNormalizedRequest:
    """Test NormalizedRequest reconstruction from RequestLog."""

    def test_reconstruct_with_full_normalized_request(self) -> None:
        """Reconstruct when normalized_request has all fields."""
        created = datetime.now(timezone.utc)

        # Create a minimal RequestLog-like object (we use a simple mock)
        class MockRequestLog:
            request_id = "req-123"
            session_id = "sess-456"
            request_content = "Hello"
            normalized_request = {
                "user_id": "user-789",
                "integration_type": "SLACK",
                "content": "Hello",
                "request_type": "message",
                "integration_context": {"channel_id": "C123"},
                "user_context": {"name": "Alice"},
                "target_agent_id": "agent-1",
                "requires_routing": False,
            }
            created_at = created

        nr = reconstruct_normalized_request(MockRequestLog())
        assert nr.request_id == "req-123"
        assert nr.session_id == "sess-456"
        assert nr.user_id == "user-789"
        assert nr.integration_type == IntegrationType.SLACK
        assert nr.content == "Hello"
        assert nr.request_type == "message"
        assert nr.integration_context == {"channel_id": "C123"}
        assert nr.user_context == {"name": "Alice"}
        assert nr.target_agent_id == "agent-1"
        assert nr.requires_routing is False
        assert nr.created_at == created

    def test_reconstruct_with_minimal_normalized_request(self) -> None:
        """Reconstruct with defaults when normalized_request is sparse."""
        created = datetime.now(timezone.utc)

        class MockRequestLog:
            request_id = "req-1"
            session_id = "sess-1"
            request_content = "Hi"
            normalized_request = {"user_id": "u1", "integration_type": "WEB"}
            created_at = created

        nr = reconstruct_normalized_request(MockRequestLog())
        assert nr.request_id == "req-1"
        assert nr.session_id == "sess-1"
        assert nr.user_id == "u1"
        assert nr.integration_type == IntegrationType.WEB
        assert nr.request_type == "unknown"
        assert nr.content == "Hi"
        assert nr.integration_context == {}
        assert nr.user_context == {}
        assert nr.target_agent_id is None
        assert nr.requires_routing is True

    def test_reconstruct_with_empty_or_none_normalized_request(self) -> None:
        """Reconstruct when normalized_request is None or empty (uses fallbacks)."""
        created = datetime.now(timezone.utc)

        class MockRequestLog:
            request_id = "req-2"
            session_id = "sess-2"
            request_content = "Fallback"
            normalized_request = None
            created_at = created

        nr = reconstruct_normalized_request(MockRequestLog())
        assert nr.request_id == "req-2"
        assert nr.session_id == "sess-2"
        assert nr.user_id == "unknown"
        assert nr.integration_type == IntegrationType.WEB
        assert nr.content == "Fallback"


@pytest.mark.asyncio
class TestSessionLockAcquireRelease:
    """Test session lock acquire/release with mocked DB."""

    async def test_acquire_returns_true_when_lock_granted(self) -> None:
        """Acquire returns True when pg_try_advisory_lock succeeds."""
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchone.return_value = (True,)
        mock_db.execute = AsyncMock(return_value=mock_result)

        result = await acquire_session_lock(
            "550e8400-e29b-41d4-a716-446655440000", mock_db, timeout_seconds=1.0
        )
        assert result is True
        # pg_try_advisory_lock only
        assert mock_db.execute.call_count >= 1

    async def test_acquire_returns_false_on_timeout(self) -> None:
        """Acquire returns False when pg_try_advisory_lock never grants (timeout)."""
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchone.return_value = (False,)  # Lock never acquired
        mock_db.execute = AsyncMock(return_value=mock_result)

        result = await acquire_session_lock(
            "550e8400-e29b-41d4-a716-446655440000",
            mock_db,
            timeout_seconds=0.05,  # Short timeout for test
        )
        assert result is False

    async def test_release_does_not_raise(self) -> None:
        """Release session lock completes without error."""
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchone.return_value = (True, 12345)  # released, backend_pid
        mock_db.execute = AsyncMock(return_value=mock_result)

        await release_session_lock("550e8400-e29b-41d4-a716-446655440000", mock_db)
        mock_db.execute.assert_called_once()


class TestSessionLockTimeoutReturns503:
    """Test that SessionLockTimeoutError results in HTTP 503."""

    def test_session_lock_timeout_returns_503(self) -> None:
        """When session lock times out, generic endpoint returns 503 Service Unavailable."""
        from shared_models import get_db_session_dependency
        from sqlalchemy.ext.asyncio import AsyncSession

        client = TestClient(app)
        mock_session = MagicMock(spec=AsyncSession)

        async def override_get_db() -> AsyncSession:
            return mock_session

        # Patch unified_processor to have process_request_sync raise SessionLockTimeoutError
        mock_processor = MagicMock()
        mock_processor.process_request_sync = AsyncMock(
            side_effect=SessionLockTimeoutError(
                "Session lock timeout - too many concurrent requests for this session"
            )
        )

        app.dependency_overrides[get_db_session_dependency] = override_get_db

        with patch("request_manager.main.unified_processor", mock_processor):
            response = client.post(
                "/api/v1/requests/generic",
                json={
                    "user_id": "test-user",
                    "content": "Hello",
                    "integration_type": "CLI",
                    "request_type": "message",
                },
                headers={"x-user-id": "test-user"},
            )

        app.dependency_overrides.pop(get_db_session_dependency, None)

        assert response.status_code == 503
        body = response.json()
        assert "Service temporarily unavailable" in (
            body.get("detail", "") or body.get("error", "")
        )
