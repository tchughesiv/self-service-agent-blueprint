# Session request serialization — implementation plan

This document describes the implementation plan for **single in-flight request per session** with **FIFO ordering by request timestamp** in the request manager. It is intended for implementers and reviewers.

## Goals

- **One request in flight per session** across all request-manager replicas.
- **Strict FIFO** per session by request timestamp (`created_at`).
- **Durable**: accepted requests are stored before processing; no loss on crash.
- **Sync API unchanged**: callers still block until response (reuse existing future + DB polling).
- **Works with multiple replicas**: all coordination via database (advisory lock or lock table).

## Out of scope for initial implementation

- **Stuck/failed UX**: Recovery today is “mark stuck `processing` as `failed` so the session unblocks.” Better UX (retry, user-facing messaging, dead-letter, etc.) should be designed and implemented in a follow-up.

---

## 1. Data model changes

### 1.1 RequestLog (shared-models)

- **Add column `status`**: `pending` | `processing` | `completed` | `failed`.
  - Existing rows: treat as `completed` (or backfill in migration).
  - New rows: created with `status = 'pending'` at accept time.
- **Optional column `processing_started_at`**: timestamp when status was set to `processing` (used for stuck-request recovery).
- **Add index**: `(session_id, status, created_at)` to support “oldest pending per session” dequeue and recovery queries.

### 1.2 Pod heartbeats table (shared-models)

- **New table `pod_heartbeats`**: so request-manager can detect that the pod that was processing a request is no longer alive and reclaim it.
  - Columns: `pod_name` (PK, e.g. `VARCHAR(255)`), `last_check_in_at` (`TIMESTAMP WITH TIME ZONE`, not null).
  - **Request-manager**: each pod updates its row periodically (e.g. every 15–30s). Reclaim uses “no recent check-in” to mark that pod’s `processing` requests as `failed`.
  - **Integration-dispatcher**: keep **advisory lock + in-memory lease** for leader election; do **not** move to the table for that. The lock is the right mechanism there: crash releases the connection and the lock immediately, no heartbeat interval delay, no extra writes. The table is for request-manager’s per-request reclaim only. Optionally, integration-dispatcher could write to `pod_heartbeats` for **observability** (one place to see “which pods are up”), but the source of truth for “who is IMAP leader” should remain the advisory lock.

### 1.3 Migration (Alembic)

- New revision in `shared-models/alembic/versions/`:
  - Add to **request_logs**: `status` (e.g. `VARCHAR` or enum; default `'completed'` for backfill), `processing_started_at` (nullable `TIMESTAMP WITH TIME ZONE`).
  - Create index `ix_request_logs_session_status_created` on `(session_id, status, created_at)`.
  - Create **pod_heartbeats**: `pod_name` PK, `last_check_in_at` NOT NULL.

---

## 2. Per-session lock

### 2.1 Mechanism

- **Recommended**: PostgreSQL **advisory lock** keyed by `session_id`.
  - Map `session_id` (UUID string) to a single bigint key (e.g. consistent hash or use a subset of bits).
  - `pg_try_advisory_lock(key)` to try; retry with backoff until timeout.
  - `pg_advisory_unlock(key)` in `finally` so it always releases (or rely on connection close).
- **Alternative**: Small lock table `session_locks(session_id, locked_until, locked_by_request_id)` with “acquire if not locked or lock expired” semantics.

### 2.2 Lock module (request-manager)

- New helper (e.g. in `request_manager/database_utils.py` or `request_manager/session_lock.py`):
  - `acquire_session_lock(session_id: str, db: AsyncSession, timeout_seconds: float) -> bool`
  - `release_session_lock(session_id: str, db: AsyncSession) -> None`
- Timeout for **waiting to acquire**: configurable (e.g. env `SESSION_LOCK_WAIT_TIMEOUT`, default 60–120s). If timeout is reached, return False (caller returns 503/409 or marks request failed).
- Lock is **held** for the duration of one request processing (one agent round-trip). Release in `finally` after processing completes or errors.

### 2.3 Session key for advisory lock

- PostgreSQL advisory lock takes one or two bigint arguments. Derive a single bigint from `session_id` (e.g. first 8 bytes of UUID as bigint, or hash and take lower 64 bits). Ensure same `session_id` always maps to same key. Document the mapping in code.

---

## 3. Request flow (one-per-turn)

### 3.1 Accept phase

1. Validate request (existing).
2. `create_or_get_session(request, db)` (existing).
3. Normalize request and obtain `session_id`, `request_id`, etc. (existing).
4. **Insert RequestLog** with:
   - `request_id`, `session_id`, normalized payload, `created_at`, etc. (as today),
   - **`status = 'pending'`**.
5. Register response future for `request_id` in `_response_futures_registry` (existing).
6. Proceed to “wait for turn” (below). Do **not** send to agent yet.

### 3.2 Wait for turn

1. Call `acquire_session_lock(session_id, db, timeout_seconds)`.
2. If acquisition fails (timeout): mark this request `failed` (optional), return 503 or 409 to caller (or leave `pending` and return error). Do **not** hold the lock.
3. If acquisition succeeds, proceed to “reclaim and dequeue.”

### 3.3 Reclaim stuck “processing” (before dequeue)

When a request in `processing` is considered stuck (pod dead or past time cutoff), **reset it to `pending`** so another pod will pick it up and process it—do **not** mark it `failed` by default. A request is stuck if **either**:

1. **Time-based**: `processing_started_at` (or `updated_at` if null) is older than cutoff = now − (AGENT_TIMEOUT + buffer), e.g. 150s.
2. **Pod check-in**: The pod that owns the request (`request_logs.pod_name`) has not updated `pod_heartbeats.last_check_in_at` within the heartbeat grace period, or has no row.

**Reclaim query (conceptually):** Update request_logs SET status = 'pending', processing_started_at = NULL WHERE session_id = :session_id AND status = 'processing' AND (stuck by time OR stuck by heartbeat). Then proceed to dequeue. The next handler (or the same one) will dequeue this request and process it.

**Why requeue (reset to pending) instead of fail?**

- **Slack / email (async)**: The request was sent via CloudEvent; the user is waiting for a reply in the channel. If we mark the request `failed`, no one processes it and the user never gets a response. If we **requeue**, another pod processes it, the agent responds, and the response event is delivered to Slack/email. So for async, requeue is clearly better.
- **CLI / sync REST**: The caller is blocked on the pod that accepted the request. If that pod crashed, the HTTP connection is gone—the client will see a timeout or connection error either way. Requeue doesn’t fix that. But requeue still helps: the work gets done (state consistent), and any delivery path that listens for the response event (e.g. webhooks, integrations) still gets the result. So prefer **requeue** for consistency and better UX on async; sync callers whose pod died still see failure/timeout, but we don’t leave the request permanently failed when another pod could complete it.

**Optional policy**: Config or env could allow “reclaim action: requeue | fail”. Default: requeue. If set to fail, reclaim marks stuck requests `failed` (session unblocks but request is not retried); use only if requeue is undesirable (e.g. strict no-retry policy).

- **Fallback**: If `pod_name` is NULL, use only time-based reclaim; same requeue vs fail choice.
- **Config**: `POD_HEARTBEAT_INTERVAL_SECONDS`, `POD_HEARTBEAT_GRACE_SECONDS`; optional `RECLAIM_ACTION` = `requeue` | `fail` (default `requeue`).

*Note: Idempotency—if the original pod completes after we requeue, two pods might both complete the same request_id; the second completion overwrites RequestLog. That’s acceptable; we only reclaim when we believe the pod is dead (heartbeat or timeout).*

### 3.4 Dequeue one and process

1. **Select** oldest pending for this session:
   - `SELECT ... FROM request_logs WHERE session_id = :session_id AND status = 'pending' ORDER BY created_at ASC LIMIT 1 FOR UPDATE SKIP LOCKED` (or equivalent to avoid double-dequeue across replicas).
2. If no row found: release lock and await this handler’s own future (with timeout); when resolved or timeout, return. (Edge case: our request was already processed by someone else or marked failed.)
3. If row found: set `status = 'processing'`, set `processing_started_at = now()`, commit (or keep in same transaction as needed).
4. **Process** that request: send to agent, wait for response via existing mechanism (event + DB polling on RequestLog). On completion: update RequestLog with response, set `status = 'completed'` (or `'failed'` on error).
5. **Release** session lock in `finally`.
6. **Return**:
   - If the request we just processed has `request_id == our_request_id`: our future was already resolved by the poller; return the result to the caller.
   - Else: **await our own future** (with timeout). When the future is resolved (another handler processed our request), return that result. On timeout, return appropriate error.

### 3.5 Sync response (unchanged from caller perspective)

- No change to how the response is delivered: in-memory future + per-pod poller that watches RequestLog for `response_content` and resolves the future. The only change is that the RequestLog row is created at accept with `status = 'pending'` and updated to `processing` then `completed`/`failed` by whoever processes it.

---

## 4. Where to plug in (request-manager)

### 4.1 Entry points

- **Sync REST** (e.g. web, CLI, Slack): already go through `_process_request_adaptive` → `UnifiedRequestProcessor.process_request_sync`. Change the flow so that:
  - After session + normalization, create RequestLog with `status = 'pending'` (instead of at “start of processing”).
  - Then: acquire lock → reclaim stuck → dequeue one → process that request → release lock → return or await own future.
- **CloudEvent path** (e.g. from integration-dispatcher): same logic. Request-manager handles the event, does create_or_get_session, creates RequestLog with `status = 'pending'`, then same lock → reclaim → dequeue → process → release.

### 4.2 Shared “process one for session” helper

- Extract a single helper used by both sync and CloudEvent paths, e.g.:
  - `wait_for_turn_and_process_one(session_id, our_request_id, db, timeout_seconds) -> result_or_raise`
  - Internally: acquire_session_lock → reclaim_stuck_processing(session_id, db) → dequeue_oldest_pending(session_id, db) → process_that_request → release_session_lock → if processed_our_request return result else await our_future and return.
- This keeps the one-per-turn and recovery logic in one place.

---

## 5. Pod heartbeat (check-in) — part of initial implementation

- **Startup**: When request-manager starts, start a background task (single per pod) that periodically updates `pod_heartbeats`: UPSERT row for this pod’s name (`HOSTNAME` or `POD_NAME`), set `last_check_in_at = now()`. Interval from `POD_HEARTBEAT_INTERVAL_SECONDS` (e.g. 15).
- **Shutdown**: On shutdown, the task stops; no need to delete the row (reclaim uses “last_check_in_at stale,” so the pod will be considered dead after grace period).
- **Reclaim**: Section 3.3 uses both time-based and heartbeat-based reclaim so a crashed pod’s requests are failed within roughly the heartbeat grace (e.g. 15–30s) instead of waiting for agent timeout. Same pattern as integration-dispatcher’s leader lease (see Section 8).

---

## 6. Configuration / env

- `SESSION_LOCK_WAIT_TIMEOUT`: seconds to wait for session lock before failing (default e.g. 120).
- `AGENT_TIMEOUT`: already exists; use for time-based reclaim cutoff.
- `SESSION_LOCK_STUCK_BUFFER_SECONDS`: added to AGENT_TIMEOUT for time-based reclaim (default e.g. 30).
- `POD_HEARTBEAT_INTERVAL_SECONDS`: how often this pod updates `pod_heartbeats` (default: **15**).
- `POD_HEARTBEAT_GRACE_SECONDS`: no check-in for this long → consider pod dead for reclaim (default: **30**).

**Recommended intervals and tradeoffs**

- **Heartbeat interval** (how often each pod writes to `pod_heartbeats`): **15 seconds** default.
  - Smaller (e.g. 10s): faster detection of a crashed pod, more DB writes (N pods × 6/min at 10s, or 4/min at 15s). 15s is a good balance: user wait after crash is bounded by grace (see below), and write load stays low.
  - Larger (e.g. 30s): fewer writes, but worst-case time-to-detect grows (pod could have just checked in then crashed, so we wait up to grace).
- **Grace** (how long without a check-in before we consider the pod dead): **30 seconds** default.
  - Should be **at least ~2× heartbeat interval** so a single missed write (GC pause, brief network blip) doesn’t mark the pod dead. With interval=15s, grace=30s we allow one missed check-in.
  - Worst-case time until a stuck request is considered dead = **grace** (e.g. 30s). After that, reclaim runs the next time a handler acquires the session lock (e.g. when another request for that session arrives, or a background reclaim pass if added). So the user-visible delay after a pod crash is on the order of **grace + one lock/reclaim cycle**, not “agent timeout + buffer.”
- **Reclaim is on-demand** today (when a handler acquires the session lock). So for a session with no new request, a stuck request is only reclaimed when the next request for that session arrives. If you need faster recovery when no one sends another message, a **background reclaim task** (e.g. every 30–60s scan for stuck `processing` and requeue) can be added later; that would add a small polling interval to tune.

---

## 7. Implementation order (suggested)

| Step | Task |
|------|------|
| 1 | **DB**: Add `status` and `processing_started_at` to RequestLog; create `pod_heartbeats` table; migration; index `(session_id, status, created_at)`. Update shared-models (RequestLog model + PodHeartbeat or equivalent). |
| 2 | **Pod heartbeat**: Implement heartbeat background task (periodic UPSERT of `pod_heartbeats` for this pod). Start on request-manager startup; config via POD_HEARTBEAT_INTERVAL_SECONDS. |
| 3 | **Lock**: Implement session lock helper (advisory lock, key from session_id, acquire with timeout, release in finally). |
| 4 | **Reclaim**: Implement reclaim_stuck_processing(session_id, db): reset to `pending` (or optionally mark `failed` if RECLAIM_ACTION=fail) where stuck by time or pod heartbeat. Default: requeue so another pod processes. |
| 5 | **Dequeue**: Implement dequeue_oldest_pending(session_id, db) returning one RequestLog row and setting status to `processing`, processing_started_at. |
| 6 | **Accept path**: Create RequestLog at accept with `status = 'pending'` (after session + normalize). Ensure response future is registered. |
| 7 | **Orchestration**: Implement wait_for_turn_and_process_one (acquire → reclaim → dequeue → process one → release → return or await own future). Integrate into process_request_sync and CloudEvent handler. |
| 8 | **Completion**: On success/error, set RequestLog status to `completed` or `failed` and write response/error as today. |
| 9 | **Tests**: Single session two concurrent requests (FIFO); multiple replicas; lock timeout; reclaim of stuck processing (time-based and pod heartbeat); sync response to correct caller; heartbeat task runs and updates DB. |
| 10 | **Docs**: Update any runbooks or ops docs; document SESSION_LOCK_*, POD_HEARTBEAT_*, and stuck/failed behavior. |

---

## 8. Stuck/failed (follow-up)

- Reclaim resets stuck `processing` to `pending` (default) so another pod processes, or marks `failed` if RECLAIM_ACTION=fail. For Slack/email, requeue means the user still gets a response; for CLI sync, if the pod died the client sees timeout either way.
- Follow-up work: finer UX (e.g. retry policy, user-visible message, dead-letter) if needed.

---

## 9. Why table for request-manager vs in-memory for integration-dispatcher

- **Integration-dispatcher (leader election)**: Advisory lock + in-memory lease is **recommended**. The leader holds the lock by keeping a DB connection open; when the leader crashes, the connection drops and the lock is released immediately. No table, no heartbeat interval delay, no extra writes. A table would not improve the election and would add latency and load.
- **Request-manager (reclaim)**: A **table** is the right choice. We need to answer “which pod was processing this request, and is that pod still alive?” across many pods and many requests. Hence `pod_heartbeats`; reclaim resets to `pending` (or marks failed) any `processing` request whose pod hasn’t checked in within the grace period so another pod can process it. Standard pattern for distributed worker liveness.
- **Summary**: Table for request-manager reclaim; in-memory (advisory lock) for integration-dispatcher leader election. Different problems, different best tools.

---

## 10. Summary

- **RequestLog**: `status` (pending → processing → completed/failed), `processing_started_at`; index for dequeue and reclaim. **pod_heartbeats**: pod liveness for reclaim.
- **Per-session advisory lock** (acquire with wait timeout, release in finally) ensures one in-flight request per session across replicas.
- **Pod heartbeat**: Each request-manager pod updates `pod_heartbeats` periodically; reclaim **resets to `pending`** (default) any `processing` request whose pod has not checked in within grace (or is past time-based cutoff) so another pod processes it—better for Slack/email; CLI sync still sees timeout if their pod died. Optional RECLAIM_ACTION=fail to mark failed instead.
- **Accept**: create RequestLog with `status = 'pending'`; register future.
- **Before dequeue**: reclaim stuck `processing` (time-based + heartbeat).
- **One-per-turn**: dequeue oldest `pending`, set `processing`, process one request, set `completed`/`failed`, release lock; return if we processed our request, else await our future.
- **Recovery**: Reclaim (with pod heartbeat + time-based fallback) unblocks sessions; stuck/failed UX improvements deferred.
