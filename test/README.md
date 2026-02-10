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
