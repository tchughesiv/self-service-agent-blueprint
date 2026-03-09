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

**`ZAMMAD_HTTP_TOKEN`** must be set in **this server’s environment** (Helm secret key **`zammad-http-token`**, or `export` when running locally). It is used for the REST user call above.

## Environment

| Variable | Purpose |
|----------|---------|
| `ZAMMAD_URL` | Zammad web origin for REST (no `/api/v1` in the value; paths are added in code). Helm secret key **`zammad-url`**. |
| `ZAMMAD_BASHER_MCP_URL` | Basher MCP URL. Defaults to **`http://127.0.0.1:8001/mcp`** (sidecar on the same pod); set only if you use another host or path. |
| `ZAMMAD_BASHER_MCP_MAX_WORKERS` | Size of the thread pool used for synchronous Basher MCP calls (default **`8`**; must be an integer, clamped to **`1`**–**`128`**). |
| `ZAMMAD_HTTP_TOKEN` | Zammad API token on this pod (secret key **`zammad-http-token`**). |
| `ZAMMAD_MCP_TIMEOUT_SECONDS` | Timeout (seconds) for Basher MCP calls and Zammad REST `GET /users/{id}` (default **`120`**; invalid value raises **`ValueError`**; values below **`1`** are clamped to **`1`**). |
| `ZAMMAD_AGENT_MANAGED_TAG` | Tag for `mark_as_agent_managed_laptop_refresh` (default `agent-managed-laptop-refresh`). |
| `ZAMMAD_LAPTOP_SPECIALIST_OWNER` | Optional owner email for that tool (Basher user search; default `agent.laptop-specialist@example.com`; empty skips owner update). |
| `ZAMMAD_STATE_IN_PROGRESS` | Optional state for `mark_as_agent_managed_laptop_refresh` (e.g. `open` so the ticket leaves **new**). If **unset** in the environment, no state change (conservative); Helm defaults **`open`**. Set empty string to skip when the variable is present. |
| `ZAMMAD_STATE_CLOSED` | State name for `close` (default `closed`; must exist in your Zammad). |
| `ZAMMAD_TAG_ESCALATE_HUMAN` | Tag for `escalate_for_human_review` (default `escalated-human-review`). |
| `ZAMMAD_GROUP_ESCALATED_LAPTOP` | Optional group for escalation (default `escalated_laptop_refresh_tickets`; empty skips group change). |
| `ZAMMAD_TAG_MANAGER_REVIEW` | Tag for `send_to_manager_review` (default `pending-manager-review`). |
| `ZAMMAD_GROUP_HUMAN_MANAGED` | Group for `route_to_human_managed_queue` (default `human_managed_tickets`). |
| `ZAMMAD_USER_MANAGER_FIELD` | Customer user field for manager (default `manager_email`). |
| `ZAMMAD_MANAGER_EMAIL` | Fallback manager when that field is empty (default in Helm matches bootstrap). |

## Tools

Used by the ticket laptop refresh flow (`allowed_tools` in agent config).

| Tool | Behaviour |
|------|-----------|
| `mark_as_agent_managed_laptop_refresh` | Tag; optional Basher `owner` and optional `state` from `ZAMMAD_STATE_IN_PROGRESS`. |
| `close` | Basher `zammad_update_ticket` to closed state. |
| `escalate_for_human_review` | Tag; optional group. |
| `send_to_manager_review` | Tag; `owner` from customer field or `ZAMMAD_MANAGER_EMAIL`. |
| `route_to_human_managed_queue` | Group change when configured. |
| *(removed)* | Customer-visible ticket replies are posted by **Integration Dispatcher** (canonical pipeline `content` → Zammad REST `ticket_articles`). This MCP server exposes ticket **actions** only (tags, state, escalate, close, etc.). |

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
