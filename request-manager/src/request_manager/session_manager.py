"""Session management for Request Manager."""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from shared_db import get_enum_value
from shared_db.models import IntegrationType, RequestSession, SessionStatus
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .schemas import SessionCreate, SessionResponse


class SessionManager:
    """Manages user sessions and conversation state."""

    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    async def create_session(self, session_data: SessionCreate) -> SessionResponse:
        """Create a new session."""
        # Check for existing active session for the same user/integration
        existing_session = await self._find_active_session(
            session_data.user_id,
            session_data.integration_type,
            session_data.channel_id,
            session_data.thread_id,
        )

        if existing_session:
            # Update existing session activity
            await self._update_session_activity(existing_session.session_id)
            return SessionResponse.from_orm(existing_session)

        # Create new session
        session = RequestSession(
            session_id=str(uuid.uuid4()),
            user_id=session_data.user_id,
            integration_type=get_enum_value(session_data.integration_type),
            channel_id=session_data.channel_id,
            thread_id=session_data.thread_id,
            external_session_id=session_data.external_session_id,
            integration_metadata=session_data.integration_metadata,
            user_context=session_data.user_context,
            status=SessionStatus.ACTIVE.value,
        )

        self.db_session.add(session)
        await self.db_session.commit()
        await self.db_session.refresh(session)

        return SessionResponse.from_orm(session)

    async def get_session(self, session_id: str) -> Optional[SessionResponse]:
        """Get session by ID."""
        stmt = select(RequestSession).where(RequestSession.session_id == session_id)
        result = await self.db_session.execute(stmt)
        session = result.scalar_one_or_none()

        if session:
            return SessionResponse.from_orm(session)
        return None

    async def update_session(
        self,
        session_id: str,
        agent_id: Optional[str] = None,
        llama_stack_session_id: Optional[str] = None,
        status: Optional[SessionStatus] = None,
        conversation_context: Optional[Dict] = None,
        user_context: Optional[Dict] = None,
    ) -> Optional[SessionResponse]:
        """Update session information."""
        update_data = {
            "updated_at": datetime.now(timezone.utc),
            "last_request_at": datetime.now(timezone.utc),
        }

        if agent_id is not None:
            update_data["current_agent_id"] = agent_id
        if llama_stack_session_id is not None:
            update_data["llama_stack_session_id"] = llama_stack_session_id
        if status is not None:
            update_data["status"] = status
        if conversation_context is not None:
            update_data["conversation_context"] = conversation_context
        if user_context is not None:
            update_data["user_context"] = user_context

        stmt = (
            update(RequestSession)
            .where(RequestSession.session_id == session_id)
            .values(**update_data)
            .returning(RequestSession)
        )

        result = await self.db_session.execute(stmt)
        await self.db_session.commit()
        updated_session = result.scalar_one_or_none()

        if updated_session:
            return SessionResponse.from_orm(updated_session)
        return None

    async def increment_request_count(self, session_id: str, request_id: str) -> None:
        """Increment the request count for a session."""
        stmt = (
            update(RequestSession)
            .where(RequestSession.session_id == session_id)
            .values(
                total_requests=RequestSession.total_requests + 1,
                last_request_id=request_id,
                last_request_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )

        await self.db_session.execute(stmt)
        await self.db_session.commit()

    async def find_or_create_session(
        self,
        user_id: str,
        integration_type: IntegrationType,
        channel_id: Optional[str] = None,
        thread_id: Optional[str] = None,
        external_session_id: Optional[str] = None,
        integration_metadata: Optional[Dict] = None,
        user_context: Optional[Dict] = None,
    ) -> SessionResponse:
        """Find existing session or create new one."""
        # Try to find existing active session
        existing_session = await self._find_active_session(
            user_id, integration_type, channel_id, thread_id
        )

        if existing_session:
            await self._update_session_activity(existing_session.session_id)
            return SessionResponse.from_orm(existing_session)

        # Create new session
        session_data = SessionCreate(
            user_id=user_id,
            integration_type=integration_type,
            channel_id=channel_id,
            thread_id=thread_id,
            external_session_id=external_session_id,
            integration_metadata=integration_metadata or {},
            user_context=user_context or {},
        )

        return await self.create_session(session_data)

    async def get_user_sessions(
        self,
        user_id: str,
        integration_type: Optional[IntegrationType] = None,
        status: Optional[SessionStatus] = None,
        limit: int = 50,
    ) -> List[SessionResponse]:
        """Get sessions for a user."""
        stmt = select(RequestSession).where(RequestSession.user_id == user_id)

        if integration_type:
            stmt = stmt.where(
                RequestSession.integration_type == get_enum_value(integration_type)
            )
        if status:
            stmt = stmt.where(RequestSession.status == status)

        stmt = stmt.order_by(RequestSession.last_request_at.desc()).limit(limit)

        result = await self.db_session.execute(stmt)
        sessions = result.scalars().all()

        return [SessionResponse.from_orm(session) for session in sessions]

    async def cleanup_inactive_sessions(self, hours: int = 24) -> int:
        """Clean up inactive sessions older than specified hours."""
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours)

        stmt = (
            update(RequestSession)
            .where(
                RequestSession.last_request_at < cutoff_time,
                RequestSession.status == SessionStatus.ACTIVE.value,
            )
            .values(
                status=SessionStatus.INACTIVE.value,
                updated_at=datetime.now(timezone.utc),
            )
        )

        result = await self.db_session.execute(stmt)
        await self.db_session.commit()

        return result.rowcount

    async def _find_active_session(
        self,
        user_id: str,
        integration_type: IntegrationType,
        channel_id: Optional[str] = None,
        thread_id: Optional[str] = None,
    ) -> Optional[RequestSession]:
        """Find an active session for the user/integration combination."""
        stmt = select(RequestSession).where(
            RequestSession.user_id == user_id,
            RequestSession.integration_type == get_enum_value(integration_type),
            RequestSession.status == SessionStatus.ACTIVE.value,
        )

        # For Slack, match on channel_id and optionally thread_id
        if integration_type == IntegrationType.SLACK and channel_id:
            stmt = stmt.where(RequestSession.channel_id == channel_id)
            if thread_id:
                stmt = stmt.where(RequestSession.thread_id == thread_id)

        # For other integrations, just match on user_id and type
        stmt = stmt.order_by(RequestSession.last_request_at.desc())

        result = await self.db_session.execute(stmt)
        return result.scalar_one_or_none()

    async def _update_session_activity(self, session_id: str) -> None:
        """Update session activity timestamp."""
        stmt = (
            update(RequestSession)
            .where(RequestSession.session_id == session_id)
            .values(
                last_request_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )

        await self.db_session.execute(stmt)
        await self.db_session.commit()
