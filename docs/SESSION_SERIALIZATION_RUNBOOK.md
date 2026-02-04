# Session request serialization — runbook

Operational guide for **session request serialization** (one in-flight request per session, FIFO ordering, stuck reclaim) in the request-manager.

## Overview

- **One request in flight per session** across all request-manager replicas.
- **FIFO** per session by request timestamp.
- **Sequential agent processing**: The agent processes one request at a time per session (see [How FIFO works end-to-end](#how-fifo-works-end-to-end)).
- **Durable**: accepted requests stored before processing; no loss on crash (two-phase insert).
- **Reclaim**: stuck `processing` requests (pod dead or timeout) are reset to `pending` (default) or marked `failed` (configurable).

### Eventing requirements for FIFO

- **Partition key**: `REQUEST_CREATED` and `AGENT_RESPONSE_READY` events use `partitionkey=session_id` so the broker delivers in order per session. Required for sequential agent processing.
- **`agent_received_at`**: Timestamp when the agent started processing each request. Uses DB time (`SELECT now()`) to avoid pod clock skew across replicas—same approach as `created_at`. Included in responses for FIFO verification; sorting by `agent_received_at` should match send order per session.
- **Mock eventing**: Must run with 1 replica (`mockEventing.replicas: 1`). Multiple replicas would break per-session FIFO—events for the same `partition_key` could hit different pods.
- **Kafka (production)**: Triggers use `kafka.eventing.knative.dev/delivery.order: ordered` for per-partition FIFO.

### How FIFO works end-to-end

FIFO and sequential agent processing are achieved by two layers working together:

| Layer | Role | Dev branch | This branch |
|-------|------|------------|-------------|
| **Request-manager** | FIFO dequeue, sends to broker in order (oldest first) | No: concurrent send, arbitrary order | Yes: per-session lock, RequestLog status, dequeue by `created_at` |
| **Eventing (partition key)** | Delivers to agent in order per session | No partition key | `partitionkey=session_id` on REQUEST_CREATED, AGENT_RESPONSE_READY |
| **Agent** | Processes one-at-a-time per partition | Arbitrary order | Sequential: broker queues per partition + per-session DB advisory lock (cross-pod, same approach as request-manager) |
| **Agent RequestLog check** | Defense in depth: yield (503) if earlier request still pending/processing | N/A | Before acquiring lock, agent queries `request_logs` for earlier pending/processing; if any exist, returns 503 so broker retries later. |

Without both layers, the agent can receive msg1 before msg0 (wrong context, out-of-order responses). This branch adds both. The dev branch has neither.

---

## Quick Reference

| What | Value |
|------|-------|
| **503 causes** | RequestLog creation failure, session lock timeout, transient DB error, agent timeout |
| **Key env vars** | `SESSION_LOCK_WAIT_TIMEOUT`, `RECLAIM_ACTION`, `POD_HEARTBEAT_GRACE_SECONDS` |
| **Metrics** | `request_manager_session_lock_acquire_duration_seconds`, `request_manager_reclaim_total` |
| **Mock eventing** | Must run with 1 replica (`mockEventing.replicas: 1`) for FIFO |
| **Migration** | 004 — see [shared-models/MIGRATION.md](../shared-models/MIGRATION.md) |

---

## Environment variables

| Env var | Helm path | Default | Description |
|---------|-----------|--------|-------------|
| `SESSION_LOCK_WAIT_TIMEOUT` | `requestManagement.requestManager.sessionSerialization.lockWaitTimeoutSeconds` | 180 | Seconds to wait for session lock before failing (HTTP 503). Must be >= AGENT_TIMEOUT so queued requests can wait for current one. |
| `SESSION_LOCK_POLL_INTERVAL_SECONDS` | `requestManagement.requestManager.sessionSerialization.lockPollIntervalSeconds` | 0.05 | Interval between lock attempts (seconds). Increase to 0.1–0.2 under high contention to reduce DB load; slightly higher latency. |
| `DB_STATEMENT_TIMEOUT` | Override from `lockWaitTimeoutSeconds * 1000` (ms) | 180000 | Request-manager override; must exceed lock wait so lock operations are not cancelled by PostgreSQL |
| `SESSION_LOCK_STUCK_BUFFER_SECONDS` | `requestManagement.requestManager.sessionSerialization.stuckBufferSeconds` | 30 | Added to AGENT_TIMEOUT for time-based reclaim cutoff |
| `POD_HEARTBEAT_INTERVAL_SECONDS` | `requestManagement.requestManager.sessionSerialization.heartbeatIntervalSeconds` | 15 | How often each pod updates `pod_heartbeats` |
| `POD_HEARTBEAT_GRACE_SECONDS` | `requestManagement.requestManager.sessionSerialization.heartbeatGraceSeconds` | 30 | No check-in for this long → pod considered dead |
| `BACKGROUND_RECLAIM_INTERVAL_SECONDS` | `requestManagement.requestManager.sessionSerialization.backgroundReclaimIntervalSeconds` | 45 | How often background task scans for stuck `processing` |
| `RECLAIM_ACTION` | `requestManagement.requestManager.sessionSerialization.reclaimAction` | requeue | `requeue` (reset to pending) or `fail` (mark failed) |

---

## Stuck and failed behavior

### When a request is considered stuck

A request in `processing` is stuck if **either**:

1. **Time-based**: `processing_started_at` (or `updated_at` if null) is older than `now - (AGENT_TIMEOUT + SESSION_LOCK_STUCK_BUFFER_SECONDS)`.
2. **Pod heartbeat**: The pod that owns the request has not updated `pod_heartbeats.last_check_in_at` within `POD_HEARTBEAT_GRACE_SECONDS`.

### Reclaim action

- **`requeue`** (default): Reset stuck request to `pending` so another pod processes it. Best for Slack/email — user still gets a response.
- **`fail`**: Mark stuck request as `failed`. Use only if requeue is undesirable (e.g. strict no-retry policy).

### Recovery timing

- **Sessions with new traffic**: Reclaim runs on-demand when a handler acquires the session lock.
- **Sessions with no new traffic**: Background task runs every `BACKGROUND_RECLAIM_INTERVAL_SECONDS` to reclaim across all sessions.
- **Worst-case** for a session with no new requests: `POD_HEARTBEAT_GRACE_SECONDS` + `BACKGROUND_RECLAIM_INTERVAL_SECONDS` (e.g. 30 + 45 = 75s).

---

## HTTP 503 conditions

**Request-manager** returns **503 Service Unavailable** when:

1. **RequestLog creation fails**: Durable accept record could not be written.
2. **Session lock timeout**: Could not acquire the per-session advisory lock within `SESSION_LOCK_WAIT_TIMEOUT` (too many concurrent requests for the same session).
3. **Transient DB connection error**: Connection closed or recycled during `pg_try_advisory_lock` or other DB operations (e.g. pool recycle, connection dropped).

**Agent-service** returns **503** when:

4. **Earlier request still processing**: RequestLog check (defense in depth) found an earlier request (by `created_at`) still pending or processing. Broker retries later.
5. **Session lock timeout**: Could not acquire the per-session advisory lock within timeout.

Callers should **retry** on 503.

---

## Observability

- **`pod_heartbeats` table**: Shows which request-manager pods are alive (`last_check_in_at`).
- **`request_logs.status`**: `pending` | `processing` | `completed` | `failed`.
- **`request_logs.pod_name`**: Pod currently processing (set at dequeue; used for reclaim, not response delivery).

### OpenTelemetry metrics

When a MeterProvider is configured (e.g. OTLP), the following metrics are exported:

| Metric | Type | Description |
|--------|------|-------------|
| `request_manager_session_lock_acquire_duration_seconds` | Histogram | Time waiting to acquire session lock |
| `request_manager_session_lock_timeout_total` | Counter | Session lock timeouts (503) |
| `request_manager_request_log_creation_failure_total` | Counter | RequestLog creation failures (503) |
| `request_manager_reclaim_on_demand_total` | Counter | Stuck requests reclaimed before dequeue |
| `request_manager_reclaim_background_total` | Counter | Stuck requests reclaimed by background task |
| `request_manager_reclaim_total` | Counter | Total reclaims (on-demand + background) |

---

## Pool sizing and connection budget

PostgreSQL `max_connections` is set to 200 for all envs (test/prod). Pool sizes are unified: request-manager 8+8, agent/integration 8+8 async + sync 1–5.

### Connection budget (max_connections=200, 2 replicas each)

| Service | Replicas | Per-pod (async+sync) | Total |
|---------|----------|----------------------|-------|
| request-manager | 2 | 8+8 = 16 | 32 |
| agent-service | 2 | 8+8+5 = 21 | 42 |
| integration-dispatcher | 2 | 8+8+5 = 21 | 42 |
| llamastack | 2 | ~10–15 (raw psycopg2) | ~20–30 |
| overhead | — | — | ~5 |
| **Total** | — | — | **~140–150** |

### If you still hit the limit

1. **Reduce pools**: Lower `syncPoolMaxSize` (agent/integration use PostgresSaver) or scale request-manager to 1 replica: `kubectl scale deploy/self-service-agent-request-manager -n NAMESPACE --replicas=1`

2. **Increase max_connections**: Edit `pgvector.args` in values:

   ```yaml
   pgvector:
     args:
       - "-c"
       - "max_connections=350"
   ```

3. **Llama stack startup**: Llama stack may crash at startup if pgvector is not ready (connection refused). Add an init container that runs `pg_isready -h pgvector` until success, or rely on Kubernetes restart until pgvector is up.

---

## Troubleshooting

| Symptom | Possible cause | Action |
|---------|----------------|--------|
| Users see 503 "Service temporarily unavailable" | Lock timeout, RequestLog creation failure, or transient DB connection error | Check DB connectivity; increase `SESSION_LOCK_WAIT_TIMEOUT`; verify no session has excessive concurrent requests; retry on transient connection errors |
| Requests stuck in `processing` | Pod crashed before completing; heartbeat/reclaim not running | Verify `pod_heartbeats` has recent rows; ensure background reclaim task is running; check DB connectivity |
| No response in Slack/email | Request reclaimed but requeue loop never processed it | Verify `RECLAIM_ACTION=requeue`; check agent-service is processing; inspect `request_logs` for status transitions |
| Integration test: "response order is [1,0,2], expected [0,1,2]" | Stale test or dev branch (no FIFO): dev has no partition key and no session serialization | Ensure request-manager FIFO + partition key on events; use `STAGGER_MS=1200` (default) or `3000` for higher margin |
| Local dev: 503 on concurrent requests (fails in ~50–200ms) | Pool exhausted or RequestLog creation failure; 6 concurrent requests need ~12 connections | Start request-manager with `DB_POOL_SIZE=8` `DB_MAX_OVERFLOW=8`; check logs for "Failed to create RequestLog", "Session lock timeout", "pool", "connection" |
| In-cluster: 503 "sorry, too many clients already" | PostgreSQL max_connections exceeded | See **Pool sizing and connection budget** above. Scale to 1 replica or increase pgvector `max_connections` |
| Idle-in-transaction timeouts | `idle_in_transaction_session_timeout` too low | Ensure `DB_IDLE_TRANSACTION_TIMEOUT` (ms) > `SESSION_LOCK_WAIT_TIMEOUT` × 1000. Default 300000 > 180×1000; only adjust if you increase lock timeout. |

---

## Design overview

- **RequestLog**: `status` (`pending` → `processing` → `completed`/`failed`), `processing_started_at`, `pod_name` (set at dequeue = pod currently processing). Index `(session_id, status, created_at)` for dequeue and reclaim.
- **pod_heartbeats**: Each request-manager pod updates its row periodically; reclaim uses stale `last_check_in_at` to detect dead pods.
- **Lock**: PostgreSQL advisory lock keyed by `session_id` (UUID → bigint). Acquire with timeout, release in `finally`. Separate key space for request-manager vs agent.
- **Flow**: Accept (create RequestLog `pending`) → acquire lock → reclaim stuck `processing` → dequeue oldest `pending` → process one → release lock. If we processed another pod's request, loop (re-acquire, reclaim, dequeue) until we process our own.

### Two-phase flow (ASCII)

```
Phase 1 (durable accept):
  acquire lock → insert RequestLog (pending) → register future → release lock

Phase 2 (process loop):
  acquire lock → reclaim stuck → dequeue oldest pending → release lock
  → send to agent → wait for response
  → if dequeued our request: return
  → else: loop (re-acquire, reclaim, dequeue...)
```
- **Response poller**: Matches on `request_id` only (no `pod_name` filter) so the accepting pod receives the response when a different pod processes the request.

### Integration-dispatcher (email)

Email processing in integration-dispatcher supports FIFO and reliability:

- **INTERNALDATE sorting**: Unread emails are sorted by server receive time before processing so replies are handled in order (IMAP `SEARCH UNSEEN` returns IDs in undefined order).
- **Rate limit**: 2-second cooldown per user; uses **wait** (not skip) so all emails in a batch are processed in order. Same-user emails in one poll are spaced by ~2s.
- **Event claim**: Claim moved to immediately before forward to minimize crash window (~100–500ms vs ~2–3s). If the pod crashes after claiming but before forwarding, the email is lost (marked as read as "duplicate" on retry). The smaller window reduces this risk.
- **Mark as read**: Only after successful processing; failed or skipped emails stay unread for retry on the next poll.
- **IMAP poll interval**: Default 60s (`IMAP_POLL_INTERVAL`). Leader election ensures only one pod polls.

## Related docs

- [shared-models/MIGRATION.md](../shared-models/MIGRATION.md) — database migrations (revision 004)
- [guides/PERFORMANCE_SCALING_GUIDE.md](../guides/PERFORMANCE_SCALING_GUIDE.md) — pool sizing, connection budget

## Test strategy (session serialization)

Existing unit tests in `request-manager/tests/test_session_serialization.py` cover:

- Session lock key derivation (UUID → bigint, non-UUID fallback)
- `reconstruct_normalized_request` (full, minimal, empty payloads)
- `acquire_session_lock` / `release_session_lock` with mocked DB
- Lock timeout (returns False when lock never granted)

### Test coverage

| Scenario | Status | Approach |
|---------|--------|----------|
| **FIFO with concurrent requests** | ✅ CI | `test/session_serialization_integration.py` — 3 concurrent requests for same session; verifies all complete (no 503). Runs in PR e2e via `kubectl exec`. |
| **Lock timeout** | ✅ Unit | `acquire_session_lock` returns False on timeout (mocked DB). `SessionLockTimeoutError` → HTTP 503 verified in `test_session_lock_timeout_returns_503`. |
| **Cross-pod response delivery** | ✅ CI | Pod A accepts, Pod B processes. Poller resolves future on Pod A without `pod_name` filter. Session serialization test hits load-balanced service (REQUEST_MANAGER_URL) so cross-pod is exercised with 2+ replicas. |
| **Reclaim (time-based)** | ✅ CI | `test/session_reclaim_integration.py` — inserts stuck row (old `processing_started_at`), sends new request; reclaim resets row, new request completes. |
| **Reclaim (heartbeat)** | ✅ CI | Same script — stuck row has `pod_name` not in `pod_heartbeats`; reclaim triggers on heartbeat criterion. |
| **Background reclaim task** | ✅ CI | `test/session_background_reclaim_integration.py` — inserts stuck row, no follow-up request; polls until background task reclaims (~45–60s). Makefile uses `timeout 180`. |

### Implementation approach

1. **Unit tests** (mocked DB): Lock acquire/release, `reconstruct_normalized_request` — already covered in `test_session_serialization.py`.
2. **CI integration test**: `test/session_serialization_integration.py` runs via `kubectl exec` in the PR e2e workflow. Sends 3 concurrent requests for the same session and verifies all complete (no 503). Uses `REQUEST_MANAGER_URL=http://self-service-agent-request-manager:80` to hit the service (load-balances across replicas). Run with: `make test-session-serialization-integration NAMESPACE=test`.
3. **Reclaim integration test**: `test/session_reclaim_integration.py` runs via `kubectl exec` in the PR e2e workflow. Inserts a stuck `processing` row (old `processing_started_at`, `pod_name` not in `pod_heartbeats`), sends a new request; verifies the new request completes and the stuck row was reclaimed. Run with: `make test-session-reclaim-integration NAMESPACE=test`.
4. **Background reclaim integration test**: `test/session_background_reclaim_integration.py` runs via `kubectl exec` in the PR e2e workflow. Inserts a stuck row, does *not* send a follow-up request, polls the DB until the background task reclaims it (~45–60s). Makefile wraps the command in `timeout 180`. Run with: `make test-session-background-reclaim-integration NAMESPACE=test`.
5. **Integration tests** (real DB): Use testcontainers Postgres or CI Postgres for dequeue `FOR UPDATE SKIP LOCKED` (future work).
6. **Write tests alongside changes** for dequeue, reclaim, and poller behavior rather than deferring.
