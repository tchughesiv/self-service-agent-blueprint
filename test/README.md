# Scripts used by eval framework to test conversation flows

To run in pod using terminal you must use:

```
/app/.venv/bin/python XXX
```

where XXX is the script name (e.g. `test/chat-responses-request-mgr.py` or `test/get-conversations-request-mgr.py`).

## chat-responses-request-mgr.py

Command line chat interface that uses the Request Manager API
for responses mode (LangGraph-based) conversations. This provides
advanced conversation management with persistent threads. Run with:

```bash
# Using environment variables
REQUEST_MANAGER_URL=https://your-request-manager \
USER_ID=your-user-id \
python test/chat-responses-request-mgr.py

# Or with command line arguments
python test/chat-responses-request-mgr.py --user-id your-user-id --request-manager-url https://your-request-manager
```

## get-conversations-request-mgr.py

Retrieves conversations from Request Manager. No auth required (matches generic). Output is JSON to stdout.

- **Filters** (optional; same names as API): `--user-id`, `--user-email`, `--session-id`, `--start-date`, `--end-date`, `--integration-type`, `--agent-id`. Omit to get all conversations.
- **Pagination/options**: `--limit`, `--offset`, `--no-messages`, `--random`.

```bash
# No filter (all conversations)
python test/get-conversations-request-mgr.py

# Filter by email
python test/get-conversations-request-mgr.py --user-email user@example.com

# Pod exec
oc exec -it deploy/self-service-agent-request-manager -n NAMESPACE -- \
  python /app/test/get-conversations-request-mgr.py --user-email user@example.com

# More: --user-id, --session-id, --start-date, --end-date, --integration-type, --agent-id, --limit, --offset, --no-messages, --random
```

## ticket-responses-request-mgr.py

Zammad ticket harness: creates a ticket via REST, adds customer articles (integration trigger → dispatcher → RM), and polls **`GET /api/v1/conversations`** for agent replies. Requires **`ZAMMAD_URL`**, **`ZAMMAD_HTTP_TOKEN`**, **`REQUEST_MANAGER_URL`**, and **`USER_ID`** (customer email). For generic CLI RM without Zammad, use **`chat-responses-request-mgr.py`**.

Stdin/stdout matches **`run_conversations`** / **`OpenShiftChatClient`**: `agent:` lines and **`AGENT_MESSAGE_TERMINATOR`** (e.g. `:DONE`) in non-TTY test mode.

```bash
REQUEST_MANAGER_URL=http://localhost:8080 \
ZAMMAD_URL=https://zammad.example \
ZAMMAD_HTTP_TOKEN=… \
USER_ID=user@example.com \
python test/ticket-responses-request-mgr.py
```

**If the UI shows a different ticket number than the script (e.g. `61016` vs `640…`) or you cannot find the ticket:** the pod’s **`ZAMMAD_URL`** (from the **`zammad-url`** key in **`…-zammad-credentials`**) must target the **same Zammad** you browse. Bootstrap often sets that to the **in-cluster** URL (`http://…-nginx:8080`); the OpenShift Route should still be the **same app and database**, but a typo, old secret, or second Zammad install in another namespace will diverge. The script prints **`[zammad] ZAMMAD_URL=…`** and a **`verified GET /tickets/<id>`** line on **stderr** — use that host and ticket **number** / **id** when searching the UI (clear filters; search by number). Articles are created as **internal** notes; the ticket row should still appear for agents with access to group **Users**.
