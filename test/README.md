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

Retrieves conversations from Request Manager (same auth and URL as the chat script). Output is JSON to stdout.

- **Auth**: `--user-id` or env `USER_ID` / `AUTHORITATIVE_USER_ID` (who is calling the API).
- **Filters** (all optional; `--filter-*` for API query params): `--filter-user-id`, `--filter-user-email`, `--filter-session-id`, `--filter-start-date`, `--filter-end-date`, `--filter-integration-type`, `--filter-agent-id`. Use one of `--filter-user-id` or `--filter-user-email` to restrict by user; omit to get all conversations the caller can see.
- **Pagination/options**: `--limit`, `--offset`, `--no-messages`, `--random`.

```bash
# Auth via env; no filter (all conversations)
REQUEST_MANAGER_URL=http://localhost:8080 USER_ID=your-user-id \
python test/get-conversations-request-mgr.py

# Filter by email
python test/get-conversations-request-mgr.py --user-id your-user-id --filter-user-email user@example.com

# Pod exec
oc exec -it deploy/self-service-agent-request-manager -n NAMESPACE -- \
  python /app/test/get-conversations-request-mgr.py --filter-user-email user@example.com

# More: --filter-user-id, --filter-session-id, --filter-start-date, --filter-end-date, --filter-integration-type, --filter-agent-id, --limit, --offset, --no-messages, --random
```
