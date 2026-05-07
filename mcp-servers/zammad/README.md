# Zammad MCP server

FastMCP **ticket** tools for the IT self-service quickstart. The model never supplies a ticket id: the ticket is fixed by **`AUTHORITATIVE_USER_ID`** and checked against the Zammad customer before any change.


## Authoritative user id

Format:

```text
{customer_email}-{zammad_ticket_id}
```

Example: `alice@company.com-42`

- **email** — must match the ticket customer in Zammad (case-insensitive).
- **ticket_id** — Zammad’s numeric ticket **`id`**, not the display **number**.

## Token

**`ZAMMAD_HTTP_TOKEN`** (and **`ZAMMAD_URL`**) must be set on this pod: Basher MCP uses them for ticket operations, and this server also calls Zammad **REST** `GET /api/v1/users/{id}` for user profile fields (object-manager / custom attributes) that Basher’s `zammad_get_user` JSON does not reliably return.

## Ticket updates (Basher MCP only)

Ticket **writes** use **`zammad_update_ticket`**. Basher’s Pydantic **`TicketUpdateParams`** (see [basher83/Zammad-MCP](https://github.com/basher83/Zammad-MCP) `models.py`) accept **`state`**, **`owner`**, and **`group`** as **human-readable names / email** — not **`state_id`** / **`owner_id`** / **`group_id`**. This wrapper passes those string fields so Basher validation succeeds.

**Reads:** Customer profile fields for **`send_to_manager_review`** and **`get_employee_laptop_info`** come from **Zammad REST** (`GET /users/{id}` via `zammad_rest_client`). **`zammad_search_users`** (Basher) is still used inside **`assert_ticket_customer_matches_basher`** for customer authorization checks.

## Environment

| Variable | Purpose |
|----------|---------|
| `ZAMMAD_URL` | Zammad origin passed to Basher (same as Basher sidecar env; no `/api/v1` suffix in this chart — Basher adds API paths). Helm secret **`zammad-url`**. |
| `ZAMMAD_BASHER_MCP_URL` | Basher MCP URL. Defaults to **`http://127.0.0.1:8001/mcp`** (sidecar on the same pod); set only if you use another host or path. |
| `ZAMMAD_BASHER_MCP_MAX_WORKERS` | Size of the thread pool used for synchronous Basher MCP calls (default **`8`**; must be an integer, clamped to **`1`**–**`128`**). |
| `ZAMMAD_HTTP_TOKEN` | Zammad API token on this pod (secret key **`zammad-http-token`**). |
| `ZAMMAD_MCP_TIMEOUT_SECONDS` | Timeout (seconds) for Basher MCP calls from this wrapper (default **`120`**; invalid value raises **`ValueError`**; values below **`1`** are clamped to **`1`**). |
| `ZAMMAD_AGENT_MANAGED_TAG` | Tag for `mark_as_agent_managed_laptop_refresh` (default `agent-managed-laptop-refresh`). |
| `ZAMMAD_GENERAL_AGENT_MANAGED_TAG` | Tag for `mark_as_general_agent_managed` (default `agent-managed-general-support`). |
| `ZAMMAD_LAPTOP_SPECIALIST_OWNER` | Optional owner email for `mark_as_agent_managed_laptop_refresh` (default `agent.laptop-specialist@example.com`; empty skips **`owner`**). Sent in a **second** `zammad_update_ticket` after **`state`** = **`open`** (hard-coded). |
| `ZAMMAD_SPECIALIST_OWNER` | Optional owner email for `mark_as_general_agent_managed` (default `agent.general@example.com`; empty skips **`owner`**). Second update after **`open`**. |
| `ZAMMAD_STATE_CLOSED` | State name for `close` (default `closed`; must exist in your Zammad). |
| `ZAMMAD_TAG_ESCALATE_HUMAN` | Tag for `escalate_for_human_review` and `route_to_human_managed_queue` (default `escalated-human-review`). |
| `ZAMMAD_GROUP_ESCALATED_LAPTOP` | Optional group for `escalate_for_human_review` (default `escalated_laptop_refresh_tickets`; empty skips group change). **`owner` is never set** — pooled queue. |
| `ZAMMAD_TAG_MANAGER_REVIEW` | Tag for `send_to_manager_review` (default `pending-manager-review`). |
| `ZAMMAD_GROUP_HUMAN_MANAGED` | Group for `route_to_human_managed_queue` (default `human_managed_tickets`). **`owner` is never set** — pooled queue. |
| `ZAMMAD_USER_MANAGER_FIELD` | Customer user field for manager (default `manager_email`). |
| `ZAMMAD_MANAGER_EMAIL` | Fallback manager when that field is empty (default in Helm matches bootstrap). |

## Tools

Used by the ticket laptop refresh flow (`allowed_tools` in agent config).

| Tool | Behaviour |
|------|-----------|
| `mark_as_agent_managed_laptop_refresh` | Tag; update with **`state`** = `open` and optional **`owner`** from env. |
| `mark_as_general_agent_managed` | Tag; update with **`state`** = `open` and optional **`owner`** from env. |
| `close` | Basher `zammad_update_ticket` with **`state`** = `ZAMMAD_STATE_CLOSED` name. |
| `escalate_for_human_review` | Tag; optional **`group`** only (no **`owner`**). |
| `send_to_manager_review` | Tag; **`owner`** from customer field or `ZAMMAD_MANAGER_EMAIL` (no **`group`** change). |
| `route_to_human_managed_queue` | Escalation tag + optional **`group`** from `ZAMMAD_GROUP_HUMAN_MANAGED` (no **`owner`**). |
| `get_employee_laptop_info` | Reads `current_laptop` JSON via Zammad REST; returns a fixed multi-line block (see below). |

**`get_employee_laptop_info` output lines:** Employee Name, Employee Location, Laptop Model, Laptop Serial Number, Laptop Purchase Date, Laptop Age (derived), Laptop Warranty Expiry Date, Laptop Warranty (Active/Expired/Unknown).

Customer-visible ticket replies are **not** MCP tools: **Integration Dispatcher** posts them to Zammad REST (`ticket_articles`). This server exposes ticket **actions** only (tags, state, escalate, close, etc.).

## Run locally

```bash
cd mcp-servers/zammad
uv sync
export ZAMMAD_URL=https://your-zammad
export ZAMMAD_HTTP_TOKEN=your_token
uv run python -m zammad_mcp.server
```

Basher’s MCP endpoint defaults to **`http://127.0.0.1:8001/mcp`** (same layout as the chart). Set **`ZAMMAD_BASHER_MCP_URL`** only if your Basher listens somewhere else.

For streamable HTTP during development you can run **`uvicorn zammad_mcp.server:app --host 0.0.0.0 --port 8002`**. In the chart, the service listens on **8000**; the agent uses **`http://mcp-zammad-mcp:8000/mcp`** (`helm/values.yaml`; MCP chart key `zammad-mcp` avoids selector collision with Zammad subchart pods).

## Container build

From the repo root:

```bash
make build-mcp-zammad-image
```

Uses **`Containerfile.mcp-template`** (same pattern as the Snow MCP image).
