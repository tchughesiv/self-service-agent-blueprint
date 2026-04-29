"""Zammad trigger webhook: verify signature, dedupe, forward to Request Manager (APPENG-4759)."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import uuid
from typing import Any, Dict, Optional, Set

from shared_models import (
    SOURCE_SERVICE_INTEGRATION_DISPATCHER,
    CloudEventSender,
    DatabaseUtils,
    EventTypes,
    configure_logging,
    insert_outbox_event,
    mark_outbox_published,
)
from shared_models.database import get_database_manager

from .thread_lock import with_thread_lock

logger = configure_logging("integration-dispatcher")

_HTML_TAG_RE = re.compile(r"<[^>]+>")


def build_zammad_ticket_thread_key(ticket_id: int) -> str:
    """Advisory lock / outbox ordering key: one FIFO stream per Zammad ticket."""
    return f"integration:zammad:ticket:{ticket_id}"


def verify_zammad_webhook_signature(
    body: bytes, signature_header: Optional[str], secret: str
) -> bool:
    """Verify X-Hub-Signature (HMAC-SHA1 hex) from Zammad trigger webhook."""
    if not secret:
        logger.warning(
            "ZAMMAD_WEBHOOK_SECRET not set — skipping webhook signature verification"
        )
        return True
    if not signature_header:
        return False
    sig = signature_header.strip()
    if sig.lower().startswith("sha1="):
        sig = sig[5:].strip()
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha1).hexdigest()
    return hmac.compare_digest(expected, sig)


def _strip_html_body(body: str) -> str:
    if not body:
        return ""
    text = _HTML_TAG_RE.sub(" ", body)
    return " ".join(text.split()).strip()


def _group_name_from_ticket(ticket: Dict[str, Any]) -> Optional[str]:
    """Zammad may send `group` as a string (older payloads) or an expanded object (name, active, …)."""
    raw = ticket.get("group")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    if isinstance(raw, dict):
        name = raw.get("name")
        if name is not None:
            return str(name).strip()
        return None
    gn = ticket.get("group_name")
    if isinstance(gn, str) and gn.strip():
        return gn.strip()
    return None


def _customer_email_from_ticket(ticket: Dict[str, Any]) -> Optional[str]:
    """Best-effort customer email from expanded Zammad webhook ticket JSON."""
    cust = ticket.get("customer")
    if isinstance(cust, dict):
        email = cust.get("email")
        if email:
            return str(email).strip().lower()
    for key in ("customer_email", "customerEmail"):
        v = ticket.get(key)
        if v:
            return str(v).strip().lower()
    return None


def _zammad_customer_id_from_ticket(ticket: Dict[str, Any]) -> Optional[int]:
    """Zammad users.id for the ticket customer when present on the ticket object."""
    raw = ticket.get("customer_id")
    if raw is not None:
        try:
            return int(raw)
        except (TypeError, ValueError):
            pass
    cust = ticket.get("customer")
    if isinstance(cust, dict) and cust.get("id") is not None:
        try:
            return int(cust["id"])
        except (TypeError, ValueError):
            pass
    return None


def canonical_ticket_customer_email(
    stored_email: Optional[str],
    stored_cid: Optional[int],
    incoming_email: str,
    incoming_cid: Optional[int],
) -> Optional[str]:
    """Return the canonical normalized email for ``user_id``, or None to drop the webhook.

    After the first observation, ``stored_email`` is fixed for RM identity. Zammad may send
    updated display email for the same ``customer_id``; we still use the stored string.
    """
    if stored_email is None:
        return incoming_email
    if stored_cid is not None and incoming_cid is not None:
        if int(stored_cid) != int(incoming_cid):
            return None
    if stored_cid is None and incoming_cid is None and stored_email != incoming_email:
        return None
    return stored_email


async def apply_zammad_ticket_customer_anchor(
    db_session: Any,
    *,
    ticket_id: int,
    incoming_customer_id: Optional[int],
    incoming_email_normalized: str,
) -> Optional[str]:
    """Persist or validate first-seen customer for this ticket; return canonical email for RM."""
    from shared_models.models import ZammadTicketCustomerAnchor
    from sqlalchemy import select

    stmt = select(ZammadTicketCustomerAnchor).where(
        ZammadTicketCustomerAnchor.ticket_id == ticket_id
    )
    result = await db_session.execute(stmt)
    row = result.scalar_one_or_none()

    canon = canonical_ticket_customer_email(
        stored_email=(row.email_normalized if row else None),
        stored_cid=(row.zammad_customer_id if row else None),
        incoming_email=incoming_email_normalized,
        incoming_cid=incoming_customer_id,
    )
    if canon is None:
        await db_session.rollback()
        return None

    if row is None:
        db_session.add(
            ZammadTicketCustomerAnchor(
                ticket_id=ticket_id,
                zammad_customer_id=incoming_customer_id,
                email_normalized=incoming_email_normalized,
            )
        )
        await db_session.commit()
        return incoming_email_normalized

    if row.zammad_customer_id is None and incoming_customer_id is not None:
        row.zammad_customer_id = incoming_customer_id
        await db_session.commit()

    return str(row.email_normalized)


def _ticket_state_name(ticket: Dict[str, Any]) -> str:
    s = ticket.get("state")
    if isinstance(s, dict):
        return str(s.get("name") or s.get("state") or "").strip().lower()
    return str(s or "").strip().lower()


def _ticket_tag_set(ticket: Dict[str, Any]) -> Set[str]:
    raw = ticket.get("tags")
    if raw is None:
        return set()
    if isinstance(raw, str):
        return {raw.strip().lower()} if raw.strip() else set()
    out: Set[str] = set()
    for t in raw:
        if t is not None:
            out.add(str(t).strip().lower())
    return out


class ZammadService:
    """Handle Zammad Manage → Triggers → Webhook POST payloads.

    Ingest policy is ideally enforced on the Zammad trigger (bootstrap
    ``ZAMMAD_TRIGGER_*``); optional ``ZAMMAD_ALLOWED_GROUP_IDS`` /
    ``ZAMMAD_REQUIRE_ANY_TAG`` here are defense-in-depth when unset at source.
    """

    def __init__(self) -> None:
        self.webhook_secret = (os.getenv("ZAMMAD_WEBHOOK_SECRET") or "").strip()
        self.broker_url = os.getenv("BROKER_URL")
        if not self.broker_url:
            raise ValueError(
                "BROKER_URL is required for ZammadService (same as SlackService)."
            )
        self.cloudevent_sender = CloudEventSender(
            self.broker_url, "integration-dispatcher"
        )
        ai_raw = os.getenv("ZAMMAD_AI_AGENT_USER_ID", "").strip()
        self.ai_agent_user_id: Optional[int] = int(ai_raw) if ai_raw.isdigit() else None

        self.allowed_group_ids: Optional[Set[int]] = None
        ag = os.getenv("ZAMMAD_ALLOWED_GROUP_IDS", "").strip()
        if ag:
            self.allowed_group_ids = set()
            for part in ag.split(","):
                part = part.strip()
                if part.isdigit():
                    self.allowed_group_ids.add(int(part))

        blocked = os.getenv("ZAMMAD_BLOCKED_STATE_NAMES", "").strip()
        self.blocked_state_names: Set[str] = (
            {x.strip().lower() for x in blocked.split(",") if x.strip()}
            if blocked
            else set()
        )

        req_tags = os.getenv("ZAMMAD_REQUIRE_ANY_TAG", "").strip()
        self.require_any_tags: Set[str] = (
            {x.strip().lower() for x in req_tags.split(",") if x.strip()}
            if req_tags
            else set()
        )
        if not self.webhook_secret:
            logger.warning(
                "ZAMMAD_WEBHOOK_SECRET is not set: trigger webhooks will not be HMAC-verified. "
                "Set ticketingZammad.webhookSecret in Helm (or mount the secret) before production."
            )

    def verify_signature(self, body: bytes, signature_header: Optional[str]) -> bool:
        return verify_zammad_webhook_signature(
            body, signature_header, self.webhook_secret
        )

    async def handle_webhook(
        self,
        payload: Dict[str, Any],
        *,
        delivery_id: str,
        trigger_header: Optional[str],
        db_session: Any,
    ) -> None:
        """Process JSON body from Zammad after route verified signature and parsed JSON."""
        ticket = payload.get("ticket") or {}
        article = payload.get("article") or {}

        ticket_id = ticket.get("id")
        if ticket_id is None:
            logger.warning("Zammad webhook: missing ticket.id")
            return

        ticket_id = int(ticket_id)

        delivery_id = (delivery_id or "").strip()
        if not delivery_id:
            delivery_id = str(uuid.uuid4())
            logger.warning(
                "Zammad webhook: empty delivery id after strip — using random (dedupe weak)"
            )

        claim_id = f"zammad-delivery-{delivery_id}"
        event_claimed = await DatabaseUtils.try_claim_event_for_processing(
            db_session,
            claim_id,
            "zammad_webhook",
            "zammad",
            "integration-dispatcher",
        )
        if not event_claimed:
            logger.info(
                "Zammad webhook: duplicate delivery skipped",
                delivery_id=delivery_id,
            )
            return

        if not article:
            logger.info(
                "Zammad webhook: no article in payload — ignored",
                ticket_id=ticket_id,
            )
            return

        sender = str(article.get("sender") or "").strip()
        if sender.lower() not in ("customer", "external"):
            logger.info(
                "Zammad webhook: skipping non-customer article",
                ticket_id=ticket_id,
                sender=sender,
            )
            return

        if article.get("internal"):
            logger.info(
                "Zammad webhook: skipping internal article", ticket_id=ticket_id
            )
            return

        origin_by = article.get("origin_by_id")
        created_by = article.get("created_by_id")
        if self.ai_agent_user_id is not None:
            for uid in (origin_by, created_by):
                if uid is not None and int(uid) == self.ai_agent_user_id:
                    logger.info(
                        "Zammad webhook: skipping article from AI agent user",
                        ticket_id=ticket_id,
                        user_id=uid,
                    )
                    return

        group_id = ticket.get("group_id")
        if group_id is None:
            logger.warning(
                "Zammad webhook: missing ticket.group_id", ticket_id=ticket_id
            )
            return
        group_id = int(group_id)

        if (
            self.allowed_group_ids is not None
            and group_id not in self.allowed_group_ids
        ):
            logger.info(
                "Zammad webhook: group not allowed",
                ticket_id=ticket_id,
                group_id=group_id,
            )
            return

        state_name = _ticket_state_name(ticket)
        if self.blocked_state_names and state_name in self.blocked_state_names:
            logger.info(
                "Zammad webhook: ticket state blocked",
                ticket_id=ticket_id,
                state=state_name,
            )
            return

        tags = _ticket_tag_set(ticket)
        if self.require_any_tags and not (tags & self.require_any_tags):
            logger.info(
                "Zammad webhook: ticket missing required tag",
                ticket_id=ticket_id,
                tags=tags,
                required=self.require_any_tags,
            )
            return

        article_id = article.get("id")
        if article_id is None:
            logger.warning("Zammad webhook: missing article.id", ticket_id=ticket_id)
            return
        article_id = int(article_id)

        body_raw = str(article.get("body") or "")
        content = _strip_html_body(body_raw)
        if not content:
            logger.info("Zammad webhook: empty body after strip", ticket_id=ticket_id)
            return

        zammad_cid = _zammad_customer_id_from_ticket(ticket)
        email_resolved = _customer_email_from_ticket(ticket)
        if not email_resolved:
            email_resolved = (
                f"customer{zammad_cid}@zammad.unknown"
                if zammad_cid is not None
                else "unknown@zammad.unknown"
            )
            logger.warning(
                "Zammad webhook: no customer email in payload — using synthetic id",
                ticket_id=ticket_id,
                synthetic_email=email_resolved,
            )

        created_by_id = created_by
        if created_by_id is None:
            created_by_id = origin_by
        if created_by_id is None:
            logger.warning(
                "Zammad webhook: missing created_by_id / origin_by_id",
                ticket_id=ticket_id,
            )
            return
        created_by_id = int(created_by_id)

        thread_key = build_zammad_ticket_thread_key(ticket_id)
        db_manager = get_database_manager()

        async def _locked_forward() -> None:
            canonical_email = email_resolved
            if (
                os.getenv("ZAMMAD_ENFORCE_TICKET_CUSTOMER_ANCHOR", "true").lower()
                == "true"
            ):
                async with db_manager.get_session() as db:
                    resolved = await apply_zammad_ticket_customer_anchor(
                        db,
                        ticket_id=ticket_id,
                        incoming_customer_id=zammad_cid,
                        incoming_email_normalized=email_resolved,
                    )
                if resolved is None:
                    logger.error(
                        "Zammad webhook: ticket customer identity mismatch — not forwarding",
                        ticket_id=ticket_id,
                        zammad_customer_id=zammad_cid,
                        email_normalized=email_resolved,
                    )
                    return
                canonical_email = resolved

            # Suffix must be Zammad internal ticket id (API). MCP parses AUTHORITATIVE_USER_ID as
            # {email}-{id}; display ticket.number differs and breaks Basher zammad_get_ticket.
            user_id = f"{canonical_email}-{ticket_id}"

            metadata: Dict[str, Any] = {
                "source": "zammad_webhook",
                "zammad_trigger": trigger_header,
                "request_id": claim_id,
            }

            ticket_title = str(ticket.get("title") or "").strip()
            event_data: Dict[str, Any] = {
                "user_id": user_id,
                "content": content,
                "integration_type": "ZAMMAD",
                "request_type": "zammad_ticket_article",
                "session_id": f"zammad-{ticket_id}",
                "request_id": claim_id,
                "ticket_id": ticket_id,
                "article_id": article_id,
                "group_id": group_id,
                "group_name": _group_name_from_ticket(ticket),
                "owner_id": ticket.get("owner_id"),
                "created_by_id": created_by_id,
                "zammad_delivery_id": delivery_id,
                "ticket_title": ticket_title,
                "metadata": metadata,
            }

            async with db_manager.get_session() as db:
                outbox_id = await insert_outbox_event(
                    db,
                    source_service=SOURCE_SERVICE_INTEGRATION_DISPATCHER,
                    event_type=EventTypes.REQUEST_CREATED,
                    idempotency_key=claim_id,
                    payload=event_data,
                    thread_order_key=thread_key,
                )
                if outbox_id is None:
                    logger.info(
                        "Zammad webhook: outbox duplicate — skipping publish",
                        claim_id=claim_id,
                    )
                    return

            success = await self.cloudevent_sender.send_request_event(
                request_data=event_data,
                request_id=claim_id,
                user_id=user_id,
                session_id=event_data["session_id"],
                max_retries=0,
            )
            if success:
                async with db_manager.get_session() as db:
                    await mark_outbox_published(db, outbox_id)
                logger.info(
                    "Zammad webhook: forwarded to Request Manager",
                    ticket_id=ticket_id,
                    article_id=article_id,
                    user_id=user_id,
                )
            else:
                logger.error(
                    "Zammad webhook: broker publish failed",
                    ticket_id=ticket_id,
                    claim_id=claim_id,
                )

        await with_thread_lock(thread_key, db_manager, _locked_forward)


def parse_zammad_webhook_json(body: bytes) -> Dict[str, Any]:
    """Parse webhook body as JSON object."""
    data = json.loads(body.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Zammad webhook JSON must be an object")
    return data
