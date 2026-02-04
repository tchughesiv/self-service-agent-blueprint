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

### 1.2 Migration (Alembic)

- New revision in `shared-models/alembic/versions/`:
  - Add `status` (e.g. `VARCHAR` or enum; default `'completed'` for backfill).
  - Add `processing_started_at` (nullable `TIMESTAMP WITH TIME ZONE`).
  - Create index `ix_request_logs_session_status_created` on `(session_id, status, created_at)`.

### 1.3 No new tables

- Reuse RequestLog only; no dedicated queue table.

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

1. **Cutoff** = now − (agent_timeout + buffer), e.g. `AGENT_TIMEOUT + 30` seconds.
2. **Update** RequestLog for this session:
   - `WHERE session_id = :session_id AND status = 'processing' AND (processing_started_at < :cutoff OR processing_started_at IS NULL AND updated_at < :cutoff)`
   - `SET status = 'failed'`, optionally set an error/message field (e.g. `"processing_timeout"`).
3. Then proceed to dequeue. This ensures the session is not blocked by a stuck request.

*Note: Stuck/failed UX (retries, user messaging, etc.) is deferred; see “Out of scope” above.*

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

## 5. Configuration / env

- `SESSION_LOCK_WAIT_TIMEOUT`: seconds to wait for session lock before failing (default e.g. 120).
- `AGENT_TIMEOUT`: already exists; use for “stuck” cutoff (processing_started_at < now - (AGENT_TIMEOUT + buffer)).
- Optional: `SESSION_LOCK_STUCK_BUFFER_SECONDS`: added to AGENT_TIMEOUT for reclaim cutoff (default e.g. 30).

---

## 6. Implementation order (suggested)

| Step | Task |
|------|------|
| 1 | **DB**: Add `status` and `processing_started_at` to RequestLog; migration; index `(session_id, status, created_at)`. Update shared-models RequestLog model. |
| 2 | **Lock**: Implement session lock helper (advisory lock, key from session_id, acquire with timeout, release in finally). |
| 3 | **Reclaim**: Implement reclaim_stuck_processing(session_id, db) using cutoff from AGENT_TIMEOUT + buffer. |
| 4 | **Dequeue**: Implement dequeue_oldest_pending(session_id, db) returning one RequestLog row and setting status to `processing`, processing_started_at. |
| 5 | **Accept path**: Create RequestLog at accept with `status = 'pending'` (after session + normalize). Ensure response future is registered. |
| 6 | **Orchestration**: Implement wait_for_turn_and_process_one (acquire → reclaim → dequeue → process one → release → return or await own future). Integrate into process_request_sync and CloudEvent handler. |
| 7 | **Completion**: On success/error, set RequestLog status to `completed` or `failed` and write response/error as today. |
| 8 | **Tests**: Single session two concurrent requests (FIFO); multiple replicas; lock timeout; reclaim of stuck processing; sync response to correct caller. |
| 9 | **Docs**: Update any runbooks or ops docs that refer to request flow; add note about SESSION_LOCK_* and stuck/failed behavior. |

---

## 7. Stuck/failed (follow-up)

- Current behavior: stuck `processing` is marked `failed` when the next handler runs reclaim so the session can progress. Caller may have already timed out.
- Follow-up work: design better UX (e.g. retry policy, user-visible message, dead-letter, or reset to `pending` with idempotency) and implement in a separate change.

---

## 8. Summary

- **RequestLog** gets `status` (pending → processing → completed/failed) and optional `processing_started_at`; index for dequeue and reclaim.
- **Per-session advisory lock** (acquire with wait timeout, release in finally) ensures one in-flight request per session across replicas.
- **Accept**: create RequestLog with `status = 'pending'`; register future.
- **Before dequeue**: reclaim stuck `processing` (mark old ones `failed`).
- **One-per-turn**: dequeue oldest `pending`, set `processing`, process one request, set `completed`/`failed`, release lock; return if we processed our request, else await our future.
- **Recovery**: Reclaim step unblocks sessions; stuck/failed UX improvements deferred.
