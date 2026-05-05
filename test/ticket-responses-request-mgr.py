#!/usr/bin/env python3
"""
CLI chat with Request Manager via Zammad ticket articles.

Creates customer articles in Zammad (integration trigger → dispatcher → Request Manager)
and polls ``GET /api/v1/conversations`` until each turn's ``agent_response`` is available.
Conversation rows are matched by ``session_id`` ``zammad-{ticket_id}`` plus **new**
``request_id`` per turn (snapshot taken before each article) so stale replies are not reused.

Requires Zammad REST (ZAMMAD_URL, ZAMMAD_HTTP_TOKEN), Request Manager (REQUEST_MANAGER_URL),
and ``USER_ID`` (customer email).

Customer turns use ``--customer-password`` or env ``ZAMMAD_CUSTOMER_PASSWORD`` (default ``ChangeMe123!``,
same as ``zammad-bootstrap`` ``DEFAULT_PASSWORD``) so ``POST /ticket_articles`` uses HTTP Basic as the
ticket customer (integration trigger: ``ticket.customer_id`` = current user).
Set ``ZAMMAD_CUSTOMER_PASSWORD`` to empty to disable (legacy: agent token for customer articles).

For generic CLI RM without Zammad, use chat-responses-request-mgr.py instead.
"""

import argparse
import asyncio
import os
import sys
import time
from collections.abc import Awaitable, Callable

import httpx
from shared_clients import RequestManagerClient
from shared_models.utils import normalize_zammad_rest_api_base, zammad_rest_json_headers

TRIGGER_POLL_TIMEOUT = float(os.environ.get("TRIGGER_POLL_TIMEOUT", "180"))
TRIGGER_POLL_INTERVAL = float(os.environ.get("TRIGGER_POLL_INTERVAL", "1.0"))

AGENT_MESSAGE_TERMINATOR = os.environ.get("AGENT_MESSAGE_TERMINATOR", "")
REQUEST_MANAGER_URL = os.environ.get("REQUEST_MANAGER_URL", "http://localhost:8080")
USER_ID = os.environ.get("USER_ID", None)
AUTHORITATIVE_USER_ID = os.environ.get("AUTHORITATIVE_USER_ID", None)
ZAMMAD_URL = os.environ.get("ZAMMAD_URL", None)
ZAMMAD_HTTP_TOKEN = os.environ.get("ZAMMAD_HTTP_TOKEN", None)

# Seeded Zammad users from zammad-bootstrap get_or_create_user (must stay in sync with bootstrap.py).
_BOOTSTRAP_DEFAULT_CUSTOMER_PASSWORD = "ChangeMe123!"


def _customer_password_effective(cli_password: str | None) -> str | None:
    """Return password for customer-scoped Zammad calls, or None for legacy agent-token mode."""
    if cli_password is not None:
        stripped = cli_password.strip()
        return stripped if stripped else None
    raw = os.environ.get("ZAMMAD_CUSTOMER_PASSWORD")
    if raw is None:
        return _BOOTSTRAP_DEFAULT_CUSTOMER_PASSWORD
    stripped = raw.strip()
    return stripped if stripped else None


# ---------------------------------------------------------------------------
# Zammad API helpers
# ---------------------------------------------------------------------------


def _zammad_get_state_map(base_url: str, token: str) -> dict[int, str]:
    """Return a mapping of state_id -> state name from Zammad."""
    url = f"{normalize_zammad_rest_api_base(base_url)}/ticket_states"
    response = httpx.get(url, headers=zammad_rest_json_headers(token), timeout=10)
    response.raise_for_status()
    return {s["id"]: s["name"] for s in response.json()}


DEFAULT_TICKET_TITLE = "Laptop refresh help request"


def _zammad_create_ticket(
    base_url: str,
    token: str,
    customer_email: str,
    title: str = DEFAULT_TICKET_TITLE,
) -> tuple[int, str]:
    """Create a Zammad ticket and return (ticket_id, ticket_number)."""
    url = f"{normalize_zammad_rest_api_base(base_url)}/tickets"
    payload: dict[str, str | object] = {
        "title": title,
        "group": "Users",
        "customer": customer_email,
    }
    response = httpx.post(
        url, json=payload, headers=zammad_rest_json_headers(token), timeout=30
    )
    response.raise_for_status()
    data = response.json()
    return data["id"], str(data["number"])


_ZAMMAD_STATE_MAP = {
    "new": "new",
    "open": "being processed by agent",
    "pending reminder": "escalated",
    "pending close": "escalated",
    "closed": "closed",
    "merged": "closed",
}


def _zammad_get_ticket_status(
    base_url: str, token: str, ticket_id: int, state_map: dict[int, str]
) -> tuple[str, str, str]:
    """Return (state, owner, group) for the ticket."""
    url = f"{normalize_zammad_rest_api_base(base_url)}/tickets/{ticket_id}?expand=true"
    try:
        response = httpx.get(url, headers=zammad_rest_json_headers(token), timeout=10)
        response.raise_for_status()
        data = response.json()
        state_id = data.get("state_id")
        state_name = state_map.get(state_id, str(state_id))
        state = _ZAMMAD_STATE_MAP.get(state_name.lower(), state_name)
        owner = data.get("owner", {})
        if isinstance(owner, dict):
            owner_name = owner.get("fullname") or owner.get("login") or "unassigned"
        else:
            owner_name = str(owner) if owner else "unassigned"
        group = data.get("group", {})
        if isinstance(group, dict):
            group_name = group.get("name") or "none"
        else:
            group_name = str(group) if group else "none"
        return state, owner_name, group_name
    except Exception:
        return "unknown", "unknown", "unknown"


def _zammad_get_ticket_owner(
    base_url: str, token: str, ticket_id: int
) -> tuple[str | None, int | None]:
    """Return (owner_email, owner_id) for the ticket's current assigned owner."""
    try:
        url = f"{normalize_zammad_rest_api_base(base_url)}/tickets/{ticket_id}"
        response = httpx.get(url, headers=zammad_rest_json_headers(token), timeout=10)
        response.raise_for_status()
        owner_id = response.json().get("owner_id")
        if not owner_id:
            return None, None
        user_url = f"{normalize_zammad_rest_api_base(base_url)}/users/{owner_id}"
        user_response = httpx.get(
            user_url, headers=zammad_rest_json_headers(token), timeout=10
        )
        user_response.raise_for_status()
        user_data = user_response.json()
        return user_data.get("email") or None, owner_id
    except Exception:
        return None, None


def _zammad_add_article(
    base_url: str,
    ticket_id: int,
    body: str,
    *,
    from_email: str,
    sender: str,
    agent_token: str,
    customer_basic: tuple[str, str] | None,
    origin_by_id: int | None = None,
) -> int | None:
    """Add an article (message) to a Zammad ticket; return new article id if successful.

    Args:
        sender: "Customer" for user messages, "Agent" for agent responses.
        from_email: email address shown as the sender of the article.
        agent_token: Agent/service token for Agent articles (and fallback if no customer basic auth).
        customer_basic: (email, password) HTTP Basic auth for Customer articles.
        origin_by_id: Zammad user ID to set as the article originator,
            overriding the authenticated token owner.
    """
    url = f"{normalize_zammad_rest_api_base(base_url)}/ticket_articles"
    # Zammad integration trigger (bootstrap) matches customer web channel: article.sender_id
    # for "Customer" on public articles. Internal API notes do not always match the same
    # trigger path as real browser "web" messages — use type=web for harness turns.
    is_customer = (sender or "").strip().lower() == "customer"
    payload = {
        "ticket_id": ticket_id,
        "body": body,
        "type": "web" if is_customer else "note",
        "internal": not is_customer,
        "sender": sender,
        "from": from_email,
    }
    if origin_by_id is not None:
        payload["origin_by_id"] = origin_by_id
    try:
        if is_customer and customer_basic is not None:
            response = httpx.post(
                url,
                json=payload,
                auth=customer_basic,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                timeout=10,
            )
        else:
            if is_customer and customer_basic is None:
                print(
                    "[zammad] warning: no customer password — Customer article posted with "
                    "agent token; Zammad trigger (ticket.customer_id = current user) may not fire.",
                    file=sys.stderr,
                )
            response = httpx.post(
                url,
                json=payload,
                headers=zammad_rest_json_headers(agent_token),
                timeout=10,
            )
        response.raise_for_status()
        data = response.json()
        aid = data.get("id")
        return int(aid) if aid is not None else None
    except Exception as e:
        print(f"Warning: could not add article to Zammad ticket: {e}", file=sys.stderr)
        return None


def _zammad_delete_ticket(base_url: str, token: str, ticket_id: int) -> None:
    """Delete a Zammad ticket."""
    url = f"{normalize_zammad_rest_api_base(base_url)}/tickets/{ticket_id}"
    httpx.delete(url, headers=zammad_rest_json_headers(token), timeout=10)


async def _conversation_request_ids_snapshot(
    rm_client: RequestManagerClient, *, session_id: str
) -> set[str]:
    """Return existing ``request_id`` values for this RM session (before posting a new Zammad article).

    Session id is ``zammad-{ticket_id}`` and is stable for the ticket; each new turn adds a **new**
    ``request_id``. Matching on message text alone can replay an old completed row — we only accept
    rows whose ``request_id`` was **not** present in this snapshot.
    """
    try:
        data = await rm_client.get_conversations(
            session_id=session_id,
            limit=1,
            offset=0,
            include_messages=True,
        )
    except Exception:
        return set()
    sessions = data.get("sessions") if isinstance(data, dict) else None
    if not sessions:
        return set()
    conv = sessions[0].get("conversation") or []
    out: set[str] = set()
    for entry in conv:
        rid = entry.get("request_id")
        if rid is not None:
            out.add(str(rid))
    return out


async def _poll_conversation_for_agent_reply(
    rm_client: RequestManagerClient,
    *,
    session_id: str,
    user_message: str,
    baseline_request_ids: set[str],
    timeout_s: float,
    poll_interval_s: float,
) -> str:
    """Wait until RM exposes ``agent_response`` for this user turn (webhook-driven path).

    ``baseline_request_ids`` must be taken **before** ``POST /ticket_articles`` so the new
    RequestLog row (new ``request_id``) is not confused with prior turns on the same session.
    """
    deadline = time.monotonic() + timeout_s
    target = user_message.strip()
    interval = max(0.25, poll_interval_s)

    while time.monotonic() < deadline:
        try:
            data = await rm_client.get_conversations(
                session_id=session_id,
                limit=1,
                offset=0,
                include_messages=True,
            )
        except Exception as ex:
            print(
                f"[rm] poll: get_conversations failed: {ex}",
                file=sys.stderr,
            )
            await asyncio.sleep(interval)
            continue

        sessions = data.get("sessions") if isinstance(data, dict) else None
        if not sessions:
            await asyncio.sleep(interval)
            continue

        conv = sessions[0].get("conversation") or []
        for entry in reversed(conv):
            um = (entry.get("user_message") or "").strip()
            if um != target:
                continue
            rid = entry.get("request_id")
            if rid is None or str(rid) in baseline_request_ids:
                continue
            ar = entry.get("agent_response")
            if ar is not None and str(ar).strip():
                return str(ar).strip()
            break

        await asyncio.sleep(interval)

    return (
        f"Timeout after {timeout_s:.0f}s waiting for agent_response "
        f"(session_id={session_id!r}; check webhook, dispatcher, RM logs)."
    )


# ---------------------------------------------------------------------------
# Chat loop with ticket status
# ---------------------------------------------------------------------------


async def _chat_loop_with_status(
    rm_client: RequestManagerClient,
    send_to_agent: Callable[[str], Awaitable[str]],
    *,
    initial_message: str | None,
    ticket_id: int,
    ticket_number: str,
    customer_email: str | None,
    zammad_base_url: str,
    zammad_token: str,
    state_map: dict[int, str],
    show_ticket_status: bool,
    test_mode: bool,
) -> None:
    """
    Chat loop that records each user message and agent response as Zammad ticket
    articles and emits a TICKET_STATUS line after each agent response.

    The TICKET_STATUS line format is:
        TICKET_STATUS:{ticket_number}:{state}:owner={owner}:group={group}
    It is printed to stdout so callers can strip it before saving conversations.
    """

    def add_agent_article(message: str) -> None:
        if ticket_id and zammad_base_url and zammad_token:
            owner_email, owner_id = _zammad_get_ticket_owner(
                zammad_base_url, zammad_token, ticket_id
            )
            print(
                f"[article] ticket={ticket_id} owner={owner_email!r} id={owner_id}",
                file=sys.stderr,
            )
            _zammad_add_article(
                zammad_base_url,
                ticket_id,
                body=message,
                from_email=owner_email or "",
                sender="Agent",
                agent_token=zammad_token,
                customer_basic=None,
                origin_by_id=owner_id,
            )

    def emit_status() -> None:
        if show_ticket_status and ticket_id and zammad_base_url and zammad_token:
            state, owner, group = _zammad_get_ticket_status(
                zammad_base_url, zammad_token, ticket_id, state_map
            )
            print(f"TICKET_STATUS:{ticket_number}:{state}:owner={owner}:group={group}")

    if not test_mode:
        print("CLI Chat - Type 'quit' to exit")
        print(f"Using Request Manager at: {rm_client.request_manager_url}")

    if initial_message:
        if test_mode:
            # In test mode the LLM simulator will send the first real message.
            # Don't send initial_message to the agent — the ticket body already
            # captures it. Print a placeholder so openshift_chat_client's
            # get_agent_initialization() can complete without hanging.
            print("agent: ")
            print(AGENT_MESSAGE_TERMINATOR)
        else:
            agent_response = await send_to_agent(initial_message)
            add_agent_article(str(agent_response))
            print(f"agent: {agent_response}")

    if test_mode:
        try:
            for line in sys.stdin:
                message = line.strip()
                if not message:
                    continue
                if message.lower() in ["quit", "exit"]:
                    break
                # Skip adding token summary messages and their responses to the ticket
                if message.lower() == "**tokens**":
                    agent_response = await send_to_agent(message)
                    print(f"agent: {agent_response}")
                    print(AGENT_MESSAGE_TERMINATOR)
                    continue
                agent_response = await send_to_agent(message)
                add_agent_article(str(agent_response))
                print(f"agent: {agent_response}")
                print(AGENT_MESSAGE_TERMINATOR)
                emit_status()
        except (EOFError, KeyboardInterrupt):
            pass
    else:
        while True:
            try:
                message = input("> ")
                if message.lower() in ["quit", "exit", "q"]:
                    break
                if message.strip():
                    if message.lower() == "**tokens**":
                        agent_response = await send_to_agent(message)
                        print(f"agent: {agent_response}")
                        continue
                    agent_response = await send_to_agent(message)
                    add_agent_article(str(agent_response))
                    print(f"agent: {agent_response}")
                    emit_status()
            except KeyboardInterrupt:
                break

    await rm_client.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Zammad ticket chat: customer articles → webhook pipeline → poll conversations for replies "
            "(requires ZAMMAD_URL, ZAMMAD_HTTP_TOKEN, REQUEST_MANAGER_URL)."
        )
    )
    parser.add_argument("--user-id", help="User ID for the chat session")
    parser.add_argument("--request-manager-url", help="Request Manager URL")
    parser.add_argument(
        "--initial-message", help="Initial message to send to the agent"
    )
    parser.add_argument(
        "--ticket-title",
        default=DEFAULT_TICKET_TITLE,
        help=f"Title for the created Zammad ticket (default: '{DEFAULT_TICKET_TITLE}')",
    )
    parser.add_argument(
        "--delete-ticket-on-close",
        action="store_true",
        help="Delete the Zammad ticket when the session ends",
    )
    parser.add_argument(
        "--show-ticket-status",
        action="store_true",
        help="Emit a TICKET_STATUS line after each agent response",
    )
    parser.add_argument(
        "--customer-password",
        default=None,
        help=(
            "Customer Zammad login password for Customer articles (HTTP Basic). "
            "Uses env ZAMMAD_CUSTOMER_PASSWORD when omitted; default ChangeMe123! if env unset. "
            "Pass empty to use agent token for customer turns (trigger may not fire)."
        ),
    )
    parser.add_argument(
        "--poll-timeout",
        type=float,
        default=TRIGGER_POLL_TIMEOUT,
        dest="poll_timeout",
        help=(
            "Seconds to wait per user turn for agent_response "
            f"(default {TRIGGER_POLL_TIMEOUT}; env TRIGGER_POLL_TIMEOUT)."
        ),
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=TRIGGER_POLL_INTERVAL,
        dest="poll_interval",
        help=(
            "Seconds between GET /api/v1/conversations polls "
            f"(default {TRIGGER_POLL_INTERVAL}; env TRIGGER_POLL_INTERVAL)."
        ),
    )
    args = parser.parse_args()

    base_user_id = args.user_id or USER_ID or AUTHORITATIVE_USER_ID
    request_manager_url = args.request_manager_url or REQUEST_MANAGER_URL
    initial_message = (
        args.initial_message or "please introduce yourself and tell me how you can help"
    )

    zammad_base_url = ZAMMAD_URL
    zammad_token = ZAMMAD_HTTP_TOKEN

    if not zammad_base_url or not zammad_token:
        print(
            "error: ZAMMAD_URL and ZAMMAD_HTTP_TOKEN are required.",
            file=sys.stderr,
        )
        sys.exit(2)
    if not base_user_id:
        print(
            "error: USER_ID or --user-id is required (customer email for the ticket).",
            file=sys.stderr,
        )
        sys.exit(2)

    api_base = normalize_zammad_rest_api_base(zammad_base_url)
    print(
        f"[zammad] ZAMMAD_URL={zammad_base_url!r} -> REST {api_base}",
        file=sys.stderr,
    )
    print(
        "[zammad] Compare the host above to the Zammad UI you open; they must be the same "
        "instance (or the same cluster entrypoint) or ticket numbers will not match the UI.",
        file=sys.stderr,
    )

    customer_pw = _customer_password_effective(args.customer_password)
    customer_basic: tuple[str, str] | None = (
        (base_user_id, customer_pw) if customer_pw else None
    )
    if customer_basic:
        print(
            "[zammad] Customer articles: HTTP Basic as ticket customer (webhook trigger path).",
            file=sys.stderr,
        )

    try:
        state_map = _zammad_get_state_map(zammad_base_url, zammad_token)
        ticket_id, ticket_number = _zammad_create_ticket(
            zammad_base_url,
            zammad_token,
            base_user_id,
            title=args.ticket_title,
        )
        print(f"Created Zammad ticket #{ticket_number} (id={ticket_id})")
        print(
            "[zammad] Expect webhook→dispatcher→RM; replies from polling conversations API.",
            file=sys.stderr,
        )
        # Re-read ticket so logs prove which Zammad returned id/number (catches wrong env / proxy).
        try:
            vurl = f"{api_base}/tickets/{ticket_id}"
            vresp = httpx.get(
                vurl, headers=zammad_rest_json_headers(zammad_token), timeout=10
            )
            vresp.raise_for_status()
            vdata = vresp.json()
            vnum = vdata.get("number")
            if str(vnum) != str(ticket_number):
                print(
                    f"[zammad] warning: create said number={ticket_number!r} "
                    f"but GET /tickets/{ticket_id} number={vnum!r}",
                    file=sys.stderr,
                )
            else:
                print(
                    f"[zammad] verified GET /tickets/{ticket_id} number={vnum!r} title={vdata.get('title')!r}",
                    file=sys.stderr,
                )
        except Exception as ex:
            print(
                f"[zammad] warning: could not verify ticket via GET: {ex}",
                file=sys.stderr,
            )
    except Exception as e:
        print(f"error: could not create Zammad ticket: {e}", file=sys.stderr)
        sys.exit(2)

    # Append internal ticket id (not display number) to user_id for session uniqueness
    # The Zammad MCP server expects the internal id from /api/v1/tickets/{id}, not the display number
    user_id = f"{base_user_id}-{ticket_id}"

    rm = RequestManagerClient(
        request_manager_url=request_manager_url,
        user_id=user_id,
    )

    tid = ticket_id
    zb = zammad_base_url
    ztok = zammad_token
    ce = base_user_id

    async def send_to_agent(msg: str) -> str:
        session_sid = f"zammad-{tid}"
        baseline_ids = await _conversation_request_ids_snapshot(
            rm, session_id=session_sid
        )
        article_id = _zammad_add_article(
            zb,
            tid,
            body=msg,
            from_email=ce,
            sender="Customer",
            agent_token=ztok,
            customer_basic=customer_basic,
        )
        if article_id is None:
            return "Error: could not create customer article in Zammad"
        return await _poll_conversation_for_agent_reply(
            rm,
            session_id=session_sid,
            user_message=msg,
            baseline_request_ids=baseline_ids,
            timeout_s=args.poll_timeout,
            poll_interval_s=args.poll_interval,
        )

    rm_client: RequestManagerClient = rm

    print(f"Using user ID: {user_id}")
    print(
        "Request Manager: poll GET /api/v1/conversations for replies (webhook-driven ingest)"
    )
    print("Using LangGraph state machine for conversation management")

    try:
        await _chat_loop_with_status(
            rm_client,
            send_to_agent,
            initial_message=initial_message,
            ticket_id=ticket_id,
            ticket_number=ticket_number,
            customer_email=base_user_id,
            zammad_base_url=zammad_base_url,
            zammad_token=zammad_token,
            state_map=state_map,
            show_ticket_status=args.show_ticket_status,
            test_mode=not sys.stdin.isatty(),
        )
    finally:
        if (
            args.delete_ticket_on_close
            and ticket_id
            and zammad_base_url
            and zammad_token
        ):
            try:
                _zammad_delete_ticket(zammad_base_url, zammad_token, ticket_id)
                print(f"Deleted Zammad ticket #{ticket_number}")
            except Exception as e:
                print(f"Warning: could not delete Zammad ticket: {e}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
