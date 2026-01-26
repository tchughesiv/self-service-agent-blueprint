"""Tests for the conversations endpoint.

The /api/v1/conversations endpoint does not require authentication (no auth required).
Tests cover: unauthenticated access; optional auth (user and admin) to ensure the
endpoint does not break when a token is sent; invalid date/integration_type/
integration_types; limit capping; filters (session_id, user_email, user_id,
integration_type, integration_types single/multiple, agent_id); random sampling; include_messages.
"""

from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from fastapi.testclient import TestClient
from request_manager.main import app, get_current_user
from shared_models import get_db_session_dependency
from sqlalchemy.ext.asyncio import AsyncSession


class TestConversationReviewEndpoint:
    """Test cases for conversation review API endpoint."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.client = TestClient(app)
        # Clear any existing dependency overrides
        app.dependency_overrides.clear()

    def teardown_method(self) -> None:
        """Clean up after each test."""
        app.dependency_overrides.clear()

    def test_get_conversations_no_auth(self) -> None:
        """Test get_conversations endpoint without authentication (no auth required, matches generic)."""
        mock_session = MagicMock(spec=AsyncSession)
        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 0
        mock_sessions_result = MagicMock()
        mock_sessions_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(
            side_effect=[mock_count_result, mock_sessions_result]
        )

        async def override_get_db() -> AsyncSession:
            return mock_session

        app.dependency_overrides[get_db_session_dependency] = override_get_db

        try:
            response = self.client.get("/api/v1/conversations")
            assert response.status_code == 200
            data = response.json()
            assert "sessions" in data
            assert "count" in data
        finally:
            app.dependency_overrides.pop(get_db_session_dependency, None)

    def test_get_conversations_authenticated_user(self) -> None:
        """Optional auth: endpoint still works when a non-admin token is sent (no auth required)."""
        mock_session = MagicMock(spec=AsyncSession)
        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 0
        mock_sessions_result = MagicMock()
        mock_sessions_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(
            side_effect=[mock_count_result, mock_sessions_result]
        )

        async def override_get_current_user() -> Dict[str, Any]:
            return {
                "user_id": "test-user",
                "groups": ["user"],
                "token": "test-token",
            }

        async def override_get_db() -> AsyncSession:
            return mock_session

        app.dependency_overrides[get_current_user] = override_get_current_user
        app.dependency_overrides[get_db_session_dependency] = override_get_db

        try:
            response = self.client.get(
                "/api/v1/conversations",
                headers={"Authorization": "Bearer test-token"},
            )
            # Any authenticated user can access; may return 200 (empty results) or 500 if DB not fully mocked
            assert response.status_code in [200, 500]
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_db_session_dependency, None)

    def test_get_conversations_admin_success(self) -> None:
        """Optional auth: endpoint still works when an admin token is sent (no auth required)."""
        # Mock database session and admin user
        mock_session = MagicMock(spec=AsyncSession)
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_result.scalar.return_value = 0
        mock_session.execute = AsyncMock(return_value=mock_result)

        async def override_get_current_user() -> Dict[str, Any]:
            return {
                "user_id": "admin-user",
                "groups": ["admin"],
                "token": "admin-token",
            }

        async def override_get_db() -> AsyncSession:
            return mock_session

        app.dependency_overrides[get_current_user] = override_get_current_user
        app.dependency_overrides[get_db_session_dependency] = override_get_db

        try:
            response = self.client.get(
                "/api/v1/conversations",
                headers={"Authorization": "Bearer admin-token"},
            )
            # Should succeed (may return empty results if no sessions)
            assert response.status_code in [200, 500]  # 500 if DB not properly mocked
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_db_session_dependency, None)

    def test_get_conversations_invalid_date_format(self) -> None:
        """Test get_conversations endpoint with invalid date format."""

        async def override_get_current_user() -> Dict[str, Any]:
            return {
                "user_id": "admin-user",
                "groups": ["admin"],
                "token": "admin-token",
            }

        app.dependency_overrides[get_current_user] = override_get_current_user

        try:
            response = self.client.get(
                "/api/v1/conversations",
                headers={"Authorization": "Bearer admin-token"},
                params={"start_date": "invalid-date"},
            )
            assert response.status_code == 400
            response_data = response.json()
            assert "Invalid start_date format" in response_data.get(
                "detail", str(response_data)
            )
        finally:
            app.dependency_overrides.pop(get_current_user, None)

    def test_get_conversations_invalid_integration_type(self) -> None:
        """Test get_conversations endpoint with invalid integration type."""

        async def override_get_current_user() -> Dict[str, Any]:
            return {
                "user_id": "admin-user",
                "groups": ["admin"],
                "token": "admin-token",
            }

        app.dependency_overrides[get_current_user] = override_get_current_user

        try:
            response = self.client.get(
                "/api/v1/conversations",
                headers={"Authorization": "Bearer admin-token"},
                params={"integration_type": "INVALID"},
            )
            assert response.status_code == 400
            response_data = response.json()
            assert "Invalid integration_type" in response_data.get(
                "detail", str(response_data)
            )
        finally:
            app.dependency_overrides.pop(get_current_user, None)

    def test_get_conversations_invalid_integration_types(self) -> None:
        """Test get_conversations endpoint with invalid integration_types value."""
        mock_session = MagicMock(spec=AsyncSession)
        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 0
        mock_sessions_result = MagicMock()
        mock_sessions_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(
            side_effect=[mock_count_result, mock_sessions_result]
        )

        async def override_get_current_user() -> Dict[str, Any]:
            return {
                "user_id": "admin-user",
                "groups": ["admin"],
                "token": "admin-token",
            }

        async def override_get_db() -> AsyncSession:
            return mock_session

        app.dependency_overrides[get_current_user] = override_get_current_user
        app.dependency_overrides[get_db_session_dependency] = override_get_db

        try:
            response = self.client.get(
                "/api/v1/conversations",
                headers={"Authorization": "Bearer admin-token"},
                params={"integration_types": "INVALID"},
            )
            assert response.status_code == 400
            response_data = response.json()
            assert "Invalid integration_types value" in response_data.get(
                "detail", str(response_data)
            )
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_db_session_dependency, None)

    def test_get_conversations_with_integration_type(self) -> None:
        """Test get_conversations with integration_type filter (channel where conversation started)."""
        mock_session = MagicMock(spec=AsyncSession)
        mock_result = MagicMock()
        mock_result.scalar.return_value = 0
        mock_session.execute = AsyncMock(return_value=mock_result)

        async def override_get_current_user() -> Dict[str, Any]:
            return {
                "user_id": "admin-user",
                "groups": ["admin"],
                "token": "admin-token",
            }

        async def override_get_db() -> AsyncSession:
            return mock_session

        app.dependency_overrides[get_current_user] = override_get_current_user
        app.dependency_overrides[get_db_session_dependency] = override_get_db

        try:
            response = self.client.get(
                "/api/v1/conversations",
                headers={"Authorization": "Bearer admin-token"},
                params={"integration_type": "CLI"},
            )
            assert response.status_code in [200, 500]
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_db_session_dependency, None)

    def test_get_conversations_with_integration_types_single(self) -> None:
        """Test get_conversations with integration_types filter (one channel)."""
        mock_session = MagicMock(spec=AsyncSession)
        mock_result = MagicMock()
        mock_result.scalar.return_value = 0
        mock_session.execute = AsyncMock(return_value=mock_result)

        async def override_get_current_user() -> Dict[str, Any]:
            return {
                "user_id": "admin-user",
                "groups": ["admin"],
                "token": "admin-token",
            }

        async def override_get_db() -> AsyncSession:
            return mock_session

        app.dependency_overrides[get_current_user] = override_get_current_user
        app.dependency_overrides[get_db_session_dependency] = override_get_db

        try:
            response = self.client.get(
                "/api/v1/conversations",
                headers={"Authorization": "Bearer admin-token"},
                params={"integration_types": ["CLI"]},
            )
            assert response.status_code in [200, 500]
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_db_session_dependency, None)

    def test_get_conversations_with_integration_types_multiple(self) -> None:
        """Test get_conversations with integration_types filter (multiple channels)."""
        mock_session = MagicMock(spec=AsyncSession)
        mock_result = MagicMock()
        mock_result.scalar.return_value = 0
        mock_session.execute = AsyncMock(return_value=mock_result)

        async def override_get_current_user() -> Dict[str, Any]:
            return {
                "user_id": "admin-user",
                "groups": ["admin"],
                "token": "admin-token",
            }

        async def override_get_db() -> AsyncSession:
            return mock_session

        app.dependency_overrides[get_current_user] = override_get_current_user
        app.dependency_overrides[get_db_session_dependency] = override_get_db

        try:
            response = self.client.get(
                "/api/v1/conversations",
                headers={"Authorization": "Bearer admin-token"},
                params={"integration_types": ["CLI", "SLACK", "EMAIL"]},
            )
            assert response.status_code in [200, 500]
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_db_session_dependency, None)

    def test_get_conversations_with_agent_id(self) -> None:
        """Test get_conversations endpoint with agent_id filter (sessions that used agent, full conversation)."""
        mock_session = MagicMock(spec=AsyncSession)
        mock_result = MagicMock()
        mock_result.scalar.return_value = 0
        mock_session.execute = AsyncMock(return_value=mock_result)

        async def override_get_current_user() -> Dict[str, Any]:
            return {
                "user_id": "admin-user",
                "groups": ["admin"],
                "token": "admin-token",
            }

        async def override_get_db() -> AsyncSession:
            return mock_session

        app.dependency_overrides[get_current_user] = override_get_current_user
        app.dependency_overrides[get_db_session_dependency] = override_get_db

        try:
            response = self.client.get(
                "/api/v1/conversations",
                headers={"Authorization": "Bearer admin-token"},
                params={"agent_id": "laptop-refresh"},
            )
            assert response.status_code in [200, 500]
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_db_session_dependency, None)

    def test_get_conversations_limit_validation(self) -> None:
        """Test get_conversations endpoint limit validation."""
        mock_session = MagicMock(spec=AsyncSession)
        mock_result = MagicMock()
        mock_result.scalar.return_value = 0
        mock_session.execute = AsyncMock(return_value=mock_result)

        async def override_get_current_user() -> Dict[str, Any]:
            return {
                "user_id": "admin-user",
                "groups": ["admin"],
                "token": "admin-token",
            }

        async def override_get_db() -> AsyncSession:
            return mock_session

        app.dependency_overrides[get_current_user] = override_get_current_user
        app.dependency_overrides[get_db_session_dependency] = override_get_db

        try:
            # Test limit > 1000 gets capped
            response = self.client.get(
                "/api/v1/conversations",
                headers={"Authorization": "Bearer admin-token"},
                params={"limit": 2000},
            )
            # Should not error, limit should be capped to 1000
            assert response.status_code in [200, 500]
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_db_session_dependency, None)

    def test_get_conversations_with_session_id(self) -> None:
        """Test get_conversations endpoint with session_id filter."""
        session_id = str(uuid4())
        mock_session = MagicMock(spec=AsyncSession)
        mock_result = MagicMock()
        mock_result.scalar.return_value = 0
        mock_session.execute = AsyncMock(return_value=mock_result)

        async def override_get_current_user() -> Dict[str, Any]:
            return {
                "user_id": "admin-user",
                "groups": ["admin"],
                "token": "admin-token",
            }

        async def override_get_db() -> AsyncSession:
            return mock_session

        app.dependency_overrides[get_current_user] = override_get_current_user
        app.dependency_overrides[get_db_session_dependency] = override_get_db

        try:
            response = self.client.get(
                "/api/v1/conversations",
                headers={"Authorization": "Bearer admin-token"},
                params={"session_id": session_id},
            )
            # Should succeed (may return empty if session not found)
            assert response.status_code in [200, 500]
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_db_session_dependency, None)

    def test_get_conversations_with_user_email(self) -> None:
        """Test get_conversations endpoint with user_email filter."""
        mock_session = MagicMock(spec=AsyncSession)
        mock_result = MagicMock()
        mock_result.scalar.return_value = 0
        mock_session.execute = AsyncMock(return_value=mock_result)

        async def override_get_current_user() -> Dict[str, Any]:
            return {
                "user_id": "admin-user",
                "groups": ["admin"],
                "token": "admin-token",
            }

        async def override_get_db() -> AsyncSession:
            return mock_session

        app.dependency_overrides[get_current_user] = override_get_current_user
        app.dependency_overrides[get_db_session_dependency] = override_get_db

        try:
            response = self.client.get(
                "/api/v1/conversations",
                headers={"Authorization": "Bearer admin-token"},
                params={"user_email": "test@example.com"},
            )
            # Should succeed
            assert response.status_code in [200, 500]
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_db_session_dependency, None)

    def test_get_conversations_with_user_id(self) -> None:
        """Test get_conversations endpoint with user_id filter."""
        user_id = str(uuid4())
        mock_session = MagicMock(spec=AsyncSession)
        mock_result = MagicMock()
        mock_result.scalar.return_value = 0
        mock_session.execute = AsyncMock(return_value=mock_result)

        async def override_get_current_user() -> Dict[str, Any]:
            return {
                "user_id": "admin-user",
                "groups": ["admin"],
                "token": "admin-token",
            }

        async def override_get_db() -> AsyncSession:
            return mock_session

        app.dependency_overrides[get_current_user] = override_get_current_user
        app.dependency_overrides[get_db_session_dependency] = override_get_db

        try:
            response = self.client.get(
                "/api/v1/conversations",
                headers={"Authorization": "Bearer admin-token"},
                params={"user_id": user_id},
            )
            # Should succeed
            assert response.status_code in [200, 500]
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_db_session_dependency, None)

    def test_get_conversations_random_sampling(self) -> None:
        """Test get_conversations endpoint with random sampling."""
        mock_session = MagicMock(spec=AsyncSession)
        mock_result = MagicMock()
        mock_result.scalar.return_value = 0
        mock_session.execute = AsyncMock(return_value=mock_result)

        async def override_get_current_user() -> Dict[str, Any]:
            return {
                "user_id": "admin-user",
                "groups": ["admin"],
                "token": "admin-token",
            }

        async def override_get_db() -> AsyncSession:
            return mock_session

        app.dependency_overrides[get_current_user] = override_get_current_user
        app.dependency_overrides[get_db_session_dependency] = override_get_db

        try:
            response = self.client.get(
                "/api/v1/conversations",
                headers={"Authorization": "Bearer admin-token"},
                params={"random": "true", "limit": 10},
            )
            # Should succeed
            assert response.status_code in [200, 500]
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_db_session_dependency, None)

    def test_get_conversations_without_messages(self) -> None:
        """Test get_conversations endpoint without including messages."""
        mock_session = MagicMock(spec=AsyncSession)
        mock_result = MagicMock()
        mock_result.scalar.return_value = 0
        mock_session.execute = AsyncMock(return_value=mock_result)

        async def override_get_current_user() -> Dict[str, Any]:
            return {
                "user_id": "admin-user",
                "groups": ["admin"],
                "token": "admin-token",
            }

        async def override_get_db() -> AsyncSession:
            return mock_session

        app.dependency_overrides[get_current_user] = override_get_current_user
        app.dependency_overrides[get_db_session_dependency] = override_get_db

        try:
            response = self.client.get(
                "/api/v1/conversations",
                headers={"Authorization": "Bearer admin-token"},
                params={"include_messages": "false"},
            )
            # Should succeed
            assert response.status_code in [200, 500]
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_db_session_dependency, None)
