#!/usr/bin/env python3
"""
Session-Scoped Token Counter for Agent Service

Provides session-based token counting that persists to the database.
"""

from dataclasses import dataclass
from typing import Optional

import structlog
from shared_models.models import SessionTokenUsage
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()


@dataclass
class SessionTokenStats:
    """Token statistics for a specific session."""

    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    call_count: int = 0
    max_input_tokens: int = 0
    max_output_tokens: int = 0
    max_total_tokens: int = 0


class SessionTokenCounter:
    """Session-scoped token counter that persists to database."""

    def __init__(self, db_session: AsyncSession, session_id: str):
        self.db_session = db_session
        self.session_id = session_id

    async def add_tokens(
        self,
        input_tokens: int,
        output_tokens: int,
        model: Optional[str] = None,
        request_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> None:
        """Add token usage for this session."""
        total_tokens = input_tokens + output_tokens

        # Create token usage record
        token_usage = SessionTokenUsage(
            session_id=self.session_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            model=model,
            request_id=request_id or "unknown",
            agent_id=agent_id,
        )

        self.db_session.add(token_usage)
        await self.db_session.commit()

        logger.debug(
            "Token usage added to session",
            session_id=self.session_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            model=model,
        )

    async def get_session_stats(self) -> SessionTokenStats:
        """Get token statistics for this session."""
        # Query aggregated stats for this session
        stmt = select(
            func.sum(SessionTokenUsage.input_tokens).label("total_input"),
            func.sum(SessionTokenUsage.output_tokens).label("total_output"),
            func.sum(SessionTokenUsage.total_tokens).label("total_tokens"),
            func.count(SessionTokenUsage.id).label("call_count"),
            func.max(SessionTokenUsage.input_tokens).label("max_input"),
            func.max(SessionTokenUsage.output_tokens).label("max_output"),
            func.max(SessionTokenUsage.total_tokens).label("max_total"),
        ).where(SessionTokenUsage.session_id == self.session_id)

        result = await self.db_session.execute(stmt)
        row = result.first()

        if not row or row.call_count == 0:
            return SessionTokenStats()

        return SessionTokenStats(
            total_input_tokens=row.total_input or 0,
            total_output_tokens=row.total_output or 0,
            total_tokens=row.total_tokens or 0,
            call_count=row.call_count or 0,
            max_input_tokens=row.max_input or 0,
            max_output_tokens=row.max_output or 0,
            max_total_tokens=row.max_total or 0,
        )
