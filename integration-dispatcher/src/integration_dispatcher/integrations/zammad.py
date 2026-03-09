"""Zammad integration handler — posts customer-visible ticket articles via REST."""

import os
from typing import Any, Dict

import httpx
from shared_clients.service_client import get_zammad_rest_service_client
from shared_models import configure_logging
from shared_models.models import DeliveryRequest, DeliveryStatus, UserIntegrationConfig
from shared_models.utils import zammad_rest_json_headers

from .base import BaseIntegrationHandler, IntegrationResult

logger = configure_logging("integration-dispatcher-zammad")


class ZammadIntegrationHandler(BaseIntegrationHandler):
    """Posts pipeline reply body to Zammad; article is attributed to the API token user."""

    async def deliver(
        self,
        request: DeliveryRequest,
        config: UserIntegrationConfig,
        template_content: Dict[str, str],
    ) -> IntegrationResult:
        ic = request.integration_context or {}
        if ic.get("platform") != "zammad":
            return IntegrationResult(
                success=True,
                status=DeliveryStatus.DELIVERED,
                message="Skipped: not a Zammad ticket delivery",
                metadata={"delivery_method": "zammad_skip_non_ticket"},
            )

        ticket_raw = ic.get("ticket_id")
        if ticket_raw is None:
            return IntegrationResult(
                success=False,
                status=DeliveryStatus.FAILED,
                message="Missing ticket_id in integration_context",
                metadata={},
            )

        try:
            ticket_id = int(ticket_raw)
        except (TypeError, ValueError):
            return IntegrationResult(
                success=False,
                status=DeliveryStatus.FAILED,
                message=f"Invalid ticket_id in integration_context: {ticket_raw!r}",
                metadata={},
            )

        base_url = (os.getenv("ZAMMAD_URL") or "").strip()
        token = (os.getenv("ZAMMAD_HTTP_TOKEN") or "").strip()
        if not base_url or not token:
            return IntegrationResult(
                success=False,
                status=DeliveryStatus.FAILED,
                message="ZAMMAD_URL or ZAMMAD_HTTP_TOKEN not configured",
                metadata={},
            )

        body = (template_content.get("body") or request.content or "").strip()
        if not body:
            return IntegrationResult(
                success=False,
                status=DeliveryStatus.FAILED,
                message="Empty delivery body for Zammad article",
                metadata={},
            )

        # Minimal POST: no origin_by_id / from — Zammad attributes the article to the token user
        # (typically admin). Token should include ticket.agent or Zammad forces Customer sender.

        payload: Dict[str, Any] = {
            "ticket_id": ticket_id,
            "body": body,
            "type": "note",
            "internal": False,
            "sender": "Agent",
        }

        try:
            client = await get_zammad_rest_service_client()
            if client is None:
                return IntegrationResult(
                    success=False,
                    status=DeliveryStatus.FAILED,
                    message="ZAMMAD_URL not configured for pooled REST client",
                    metadata={},
                )
            response = await client.post(
                "/ticket_articles",
                json=payload,
                headers=zammad_rest_json_headers(token),
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as e:
            detail = ""
            try:
                detail = e.response.text[:500]
            except Exception:
                pass
            logger.error(
                "Zammad ticket_articles HTTP error",
                status_code=e.response.status_code,
                detail=detail,
                ticket_id=ticket_id,
            )
            return IntegrationResult(
                success=False,
                status=DeliveryStatus.FAILED,
                message=f"Zammad API error: {e.response.status_code}",
                metadata={"http_status": e.response.status_code, "detail": detail},
            )
        except Exception as e:
            logger.error(
                "Zammad ticket_articles request failed",
                error=str(e),
                ticket_id=ticket_id,
            )
            return IntegrationResult(
                success=False,
                status=DeliveryStatus.FAILED,
                message=f"Zammad request failed: {e!s}",
                metadata={"error": str(e)},
            )

        article_id = data.get("id") if isinstance(data, dict) else None
        return IntegrationResult(
            success=True,
            status=DeliveryStatus.DELIVERED,
            message="Posted customer-visible article to Zammad ticket",
            metadata={
                "delivery_method": "zammad_rest_ticket_articles",
                "zammad_article_id": article_id,
                "ticket_id": ticket_id,
                "responding_agent_id": request.agent_id,
            },
        )

    async def validate_config(self, config: Dict[str, Any]) -> bool:
        return True

    async def health_check(self) -> bool:
        zammad_url = (os.getenv("ZAMMAD_URL") or "").strip()
        zammad_token = (os.getenv("ZAMMAD_HTTP_TOKEN") or "").strip()
        return bool(zammad_url and zammad_token)
