# Broker Publish Plan: Zero Duplicates

**Constraint**: We cannot tolerate duplicate events. Any retry strategy must guarantee exactly-once delivery.

---

## 1. Why Retries Cause Duplicates

When we POST to the broker and get a **timeout** or **connection reset**, we cannot know whether:

- **A**: The broker never received the request → safe to retry
- **B**: The broker received it, processed it, but our connection dropped before we got the response → retry = duplicate

Retrying in case B delivers the same logical event twice. Without idempotency at the receiver, duplicates cause:

- **REQUEST_CREATED**: Duplicate request processing, duplicate RequestLog rows (or constraint violation)
- **AGENT_RESPONSE_READY**: User receives the same Slack/email message twice
- **SESSION_***: Duplicate session operations
- **DATABASE_UPDATE**: Agent processes the same update twice

**Conclusion**: Retries are only safe when the receiver deduplicates by a stable idempotency key (e.g. `event_id` or `request_id`).

---

## 2. Existing Idempotency

### Receivers

| Subscriber | Event Type | Deduplication | Mechanism |
|------------|------------|---------------|------------|
| Request-manager | REQUEST_CREATED (from integration-dispatcher) | ✅ Yes | `try_claim_event_for_processing(event_id)` |
| Request-manager | AGENT_RESPONSE_READY | ✅ Yes | `try_claim_event_for_processing(event_id)` |
| Agent-service | REQUEST_CREATED | ❌ No | No try_claim; processes every delivery |
| Agent-service | DATABASE_UPDATE_REQUESTED | ❌ No | No try_claim; processes every delivery |
| Integration-dispatcher | AGENT_RESPONSE_READY | ✅ Yes | `try_claim_event_for_processing(event_id)` |

**Critical gap**: Agent-service does **not** use `try_claim` for REQUEST_CREATED or DATABASE_UPDATE. Retrying those events would cause duplicate agent processing (and duplicate AGENT_RESPONSE_READY from the agent).

### Event ID Stability (Critical for Safe Retry)

| Event Type | Current event_id | Stable across retry? | Safe to retry? |
|------------|------------------|----------------------|----------------|
| **REQUEST_CREATED** | `request_id` (or uuid if none) | ✅ Yes when request_id provided | ✅ Yes |
| **AGENT_RESPONSE_READY** | `uuid.uuid4()` | ❌ No – new each call | ❌ No |
| **SESSION_CREATE_OR_GET** | Passed in or uuid | ⚠️ Caller controls | Depends on caller |
| **SESSION_READY** | Passed in or uuid | ⚠️ Caller controls | Depends on caller |
| **DATABASE_UPDATE_REQUESTED** | `uuid.uuid4()` | ❌ New each call | ❌ No |

### CloudEventPublisher (DATABASE_UPDATE) – Existing Retry Risk

`CloudEventPublisher._publish_event` creates the event **once** before the retry loop, so the same `event_id` is sent on each attempt. However:

- Each retry sends a **new HTTP request** with the same event
- If the first attempt succeeded (broker got it, we timed out), the broker stores one event
- Our retry sends again – broker may accept and store a second event with the same `ce-id`
- Knative/Kafka brokers typically do **not** deduplicate by `ce-id` at ingest
- Subscriber receives two events with the same `event_id` – `try_claim` would reject the second

**Agent does NOT use `try_claim`** for REQUEST_CREATED or DATABASE_UPDATE. Therefore:

- **REQUEST_CREATED** retry from request-manager → agent would process duplicate (agent runs twice, publishes AGENT_RESPONSE_READY twice)
- **DATABASE_UPDATE** retry from CloudEventPublisher → agent would process duplicate

**Action**: Either (a) add `try_claim_event_for_processing(event_id)` to agent for both event types, or (b) remove retry from CloudEventPublisher and do not add retry for REQUEST_CREATED to agent.

---

## 3. Plan: Zero Duplicates

### Principle

**Retry only when the receiver can deduplicate.** That requires:

1. **Deterministic event_id**: Same logical event → same `event_id` on every send (including retries)
2. **Receiver deduplication**: Subscriber uses `try_claim_event_for_processing(event_id)` or equivalent

### Phase 1: Fix Event IDs (Prerequisite for Any Retry)

Make `event_id` deterministic for events that need retry:

| Event | Change | Idempotency Key |
|-------|--------|-----------------|
| **AGENT_RESPONSE_READY** | Use `request_id` as `event_id` | `request_id` – one response per request |
| **SESSION_CREATE_OR_GET** | Use `correlation_id` when provided | `correlation_id` |
| **SESSION_READY** | Use `correlation_id` when provided | `correlation_id` |
| **DATABASE_UPDATE_REQUESTED** | Use `request_id` (in subject/data) as `event_id` | `request_id` |

**AGENT_RESPONSE_READY** – `CloudEventBuilder.create_response_event`:

```python
# Current: "id": str(uuid.uuid4())
# Change:  "id": request_id  (one response per request; receiver dedupes)
```

**DATABASE_UPDATE** – `CloudEventPublisher.publish_database_update_event`:

```python
# Current: "id": str(uuid.uuid4())
# Change:  "id": f"db-update-{update_data.get('request_id')}"  (or request_id if unique enough)
```

**Verification**: Ensure every subscriber of these events uses `try_claim_event_for_processing(event_id)` before processing.

### Phase 2: Add Retry to CloudEventSender (Only After Phase 1)

After event IDs are deterministic and receivers dedupe:

1. Add retry to `CloudEventSender._send_event`:
   - Retry on: `ConnectError`, `TimeoutException`, 5xx, 408, 429
   - **Do not retry** on: 4xx (except 408, 429), successful 2xx
2. Config: `EVENT_MAX_RETRIES` (default 3), exponential backoff with jitter
3. Cap total retry time (e.g. 10s) to avoid long lock holds

**Lock-held paths**: Request-manager holds session lock during `send_request` (REQUEST_CREATED). Options:

- **A**: No retry for REQUEST_CREATED from request-manager (keep lock short)
- **B**: 1 retry only, 0.5s delay (small improvement, minimal lock extension)

### Phase 3: Audit and Fix CloudEventPublisher

1. **Agent does not use try_claim** – Step 0 adds it. Once done, agent dedupes DATABASE_UPDATE.
2. Ensure `event_id` is deterministic (Phase 1, step 2)
3. Keep retry in CloudEventPublisher – safe once agent has try_claim and event_id is deterministic

### Phase 4: Strengthen Caller Retry (No Publish Retry)

For paths where we **do not** add publish retry (e.g. lock-held REQUEST_CREATED):

1. **Request-manager → REQUEST_CREATED**: Caller gets 500. Document that callers must retry. For sync API: client retries. For async (integration-dispatcher → broker → request-manager): the request-manager is the one that fails to send to agent; the "caller" is the session loop – it marks failed and raises. The original user request was already accepted. Recovery: user retries (new request with new request_id).
2. **Integration-dispatcher → REQUEST_CREATED**: 
   - **Slack**: Return 500 to Slack so Slack retries the webhook (Slack retries 3x over 30 min)
   - **Email**: Already safe – don't mark as read until success; next IMAP poll retries
3. **Request-manager → AGENT_RESPONSE_READY forward**: After Phase 1 (event_id=request_id), add retry here – integration-dispatcher dedupes by event_id

---

## 4. Implementation Order

| Step | Action | Duplicate Risk |
|------|--------|-----------------|
| 0 | **Add `try_claim_event_for_processing(event_id)` to agent** for REQUEST_CREATED and DATABASE_UPDATE | None – enables safe retry |
| **0.5** | **Integration-dispatcher**: Set `request_id` before sending REQUEST_CREATED. Slack: use `slack-{event_id}` from payload; Email: generate uuid. Include in event_data. Use 0–1 retries for Slack path. | None – required for integration-dispatcher retry |
| 1 | Change AGENT_RESPONSE_READY to use `event_id=request_id` | None |
| 2 | Change DATABASE_UPDATE to use `event_id=request_id` (or derived) | None |
| 3 | Verify all subscribers use try_claim (agent from step 0; request-manager, integration-dispatcher already do) | None |
| 4 | Add retry to CloudEventSender (only for paths where receiver dedupes) | None if steps 0, 0.5, 1–3 done |
| 5 | CloudEventPublisher: ensure deterministic event_id (step 2), then keep retry | None |
| 6 | Add retry for SESSION_* with correlation_id as event_id (optional) | None if deterministic |

**Blocking dependencies**: (1) Step 0 must complete before adding retry for REQUEST_CREATED (request-manager → agent) or DATABASE_UPDATE. (2) Step 0.5 must complete before adding retry for integration-dispatcher → broker REQUEST_CREATED. Without these, retries would cause duplicates.

---

## 5. What We Do NOT Do

- **No retry without idempotency**: Never add retry to a path where the receiver cannot deduplicate
- **No retry with random event_id**: If `event_id` is `uuid.uuid4()`, retry = duplicate
- **No retry on lock-held paths** (or 1 retry max): Avoid extending session lock hold time
- **No retry for REQUEST_PROCESSING**: Best-effort notification; duplicate is harmless but we avoid for consistency

---

## 6. Verification Checklist

Before enabling retry on any path:

- [ ] Event uses deterministic `event_id` (request_id, correlation_id, or equivalent)
- [ ] Subscriber uses `try_claim_event_for_processing(event_id)` before processing
- [ ] Subscriber treats "already processed" as success (idempotent)
- [ ] Retry delay and count are bounded (e.g. 3 retries, 10s total)
- [ ] For lock-held paths: retry count ≤ 1, delay ≤ 0.5s

---

## 7. Summary

| Path | Event ID Fix | Add Retry? | Notes |
|------|--------------|------------|-------|
| Request-manager REQUEST_CREATED | Already request_id | 0–1 retry | Lock held; minimal retry |
| Request-manager AGENT_RESPONSE_READY | request_id | Yes (3) | After event_id fix |
| Integration-dispatcher REQUEST_CREATED | **Add request_id** (Step 0.5) | Yes (3) | Must generate request_id before send; receivers dedupe |
| Agent AGENT_RESPONSE_READY | request_id | Yes (3) | After event_id fix |
| DATABASE_UPDATE | request_id | Keep/verify | Ensure agent dedupes |
| SESSION_* | correlation_id | Optional | Fallback to DB exists |
| REQUEST_PROCESSING | N/A | No | Best-effort only |

**Core rule**: Deterministic event_id + receiver deduplication = safe retry = zero duplicates.

---

## 8. Performance Effects

### Latency

| Scenario | Effect |
|----------|--------|
| **Happy path** | No change – single attempt succeeds |
| **Transient failure** | Adds retry delay: `base_delay × backoff^attempt` (e.g. 1s, 2s, 4s for 3 retries) before each retry |
| **Worst case** | Original timeout (30s) + sum of retry delays (~7s for 3 retries) = ~37s before failure |

### Lock Hold Time (Request-manager)

- Request-manager holds the **session lock** during `send_request` (REQUEST_CREATED)
- Retries extend lock hold → more 503s for other requests in the same session
- **Mitigation**: 0–1 retry only for REQUEST_CREATED, short delay (0.5s max)

### Throughput and Broker Load

- Retries increase broker and network load during outages
- Under load, many concurrent retries can amplify a failing broker (thundering herd)
- **Mitigation**: Exponential backoff with jitter; cap total retry time (e.g. 10s)

### Caller Timeout Considerations

- **Slack webhook**: Expects response in ~3s. Total retry window must stay within caller timeout or Slack retries the webhook (new request).
- **Sync API**: Caller timeout (e.g. 120s) should exceed `original_timeout + total_retry_time`.
- **Recommendation**: Cap total retry time at 5–10s so we fail fast enough for Slack; document for sync API callers.

### Slack-Specific Behavior

Slack has a ~3s timeout and retries webhooks automatically (3x over ~30 min). We treat Slack differently:

| Aspect | Slack | Email / Sync API |
|--------|-------|------------------|
| Caller timeout | ~3s | 60s+ (polling) or 120s (sync) |
| Caller retries | Yes (Slack retries webhook) | Email: next poll; Sync: client retries |
| Our retries | Often exceed 3s before we finish | More useful |

**Slack-specific changes:**

1. **Use Slack's event identifier as `request_id`** – When the request is from Slack, derive `request_id` from Slack's payload (e.g. `event_id` or `channel`+`ts`) instead of generating a new UUID. When Slack retries the webhook (same event), we get the same `request_id` → same `event_id` → `try_claim` dedupes → no duplicate processing.

2. **0 retries (or 1 short retry) for Slack → broker publish** – For integration-dispatcher when source is Slack: use `EVENT_MAX_RETRIES=0` (or 1 with 0.5s delay). Fail fast, return 500, let Slack retry the webhook. Avoid holding Slack's connection for 30+ seconds.

3. **Optional: shorter HTTP timeout for Slack path** – Use 5s timeout (instead of 30s) when publishing to broker for Slack requests. Fail within ~5s so Slack gets 500 sooner and retries sooner.

**Implementation**: Integration-dispatcher (Slack) sets `request_id = f"slack-{event_id}"` or `slack-{channel}-{ts}` from Slack payload; uses path-specific retry config (0–1 retries) when sending REQUEST_CREATED for Slack.

### try_claim Overhead

- Each event handler does a DB round-trip for `try_claim_event_for_processing` (SELECT + INSERT or UPDATE)
- Adds ~5–20ms per event depending on DB latency
- Acceptable for correctness; monitor if event volume is very high

---

## 9. Testing Strategy

### Unit Tests

| Test | Purpose |
|------|---------|
| **CloudEventSender retry logic** | Mock httpx; assert retries on ConnectError, TimeoutException, 5xx; no retry on 2xx, 4xx (except 408, 429) |
| **Retry backoff** | Verify delay increases exponentially; verify jitter applied |
| **Deterministic event_id** | Assert AGENT_RESPONSE_READY uses request_id; DATABASE_UPDATE uses db-update-{request_id} |
| **Agent try_claim** | Mock DB; assert try_claim called before processing; assert skip when claim returns False |

### Integration Tests

| Test | Purpose |
|------|---------|
| **Duplicate delivery – receiver skips** | Send same event (same event_id) twice to a subscriber; verify second delivery is skipped (try_claim returns False), no duplicate processing |
| **Retry on transient failure** | Simulate broker timeout on first attempt, success on retry; verify request completes |
| **Integration-dispatcher request_id** | Verify REQUEST_CREATED from Slack/Email includes request_id in event_data; verify same request_id on retry |

### Verification Tests (Pre Go-Live)

- [ ] Run session serialization integration test with retry enabled; verify no duplicates
- [ ] Inject broker timeout; verify retry succeeds and response delivered
- [ ] Send duplicate AGENT_RESPONSE_READY (same request_id); verify integration-dispatcher delivers once
- [ ] Load test: concurrent requests during broker blip; verify no thundering herd, eventual recovery

### Existing Tests to Extend

- `test/session_serialization_integration.py` – add assertion that no duplicate responses for same request
- `request-manager/tests/test_session_serialization.py` – add tests for retry behavior when `strategy_send_request` fails then succeeds

---

## 10. Audit: Holes and Improvements

### Holes Identified

**1. Integration-dispatcher REQUEST_CREATED has no stable event_id**

- Integration-dispatcher does **not** include `request_id` in event_data (comment: "Request Manager will generate request_id and session_id")
- `CloudEventBuilder` uses `event_id = request_id or uuid.uuid4()` → without request_id, every send gets a **new** uuid
- **Retry = different event_id = request-manager processes both** (try_claim sees different event_ids, both succeed)
- **Fix**: Integration-dispatcher must set `request_id` **before** sending and include it in event_data. **Slack**: use `slack-{event_id}` (or `channel`+`ts`) from payload so Slack retries dedupe. **Email**: generate uuid. See also Slack-specific behavior (Section 8).

**2. Stale re-claim can cause duplicate processing**

- `try_claim_event_for_processing` allows re-claiming events stuck in "processing" for >120s (stale_timeout_seconds)
- If Pod A is **slow** (not dead) and takes >120s, Pod B can re-claim the same event → **both process**
- Tradeoff: Remove stale re-claim → stuck events never recover after pod crash. Keep it → accept rare duplicate when processing exceeds 120s
- **Recommendation**: Document this tradeoff. Consider increasing stale_timeout_seconds to AGENT_TIMEOUT + buffer (e.g. 180s) to reduce false re-claims. Or accept as acceptable risk for crash recovery.

**3. CloudEventPublisher DATABASE_UPDATE – retry before fix**

- CloudEventPublisher **already has retry** but uses `uuid.uuid4()` for event_id
- Each retry sends same event object (same uuid) – but if first succeeded and we timed out, retry = duplicate at broker
- Agent does **not** use try_claim → duplicate processing today
- **Fix**: Step 0 (agent try_claim) + Step 2 (deterministic event_id) must land **before** or **with** any retry. Consider **removing retry from CloudEventPublisher** until agent has try_claim (immediate mitigation).

### Alignment with Standard Patterns

| Pattern | Our approach | Standard? |
|--------|--------------|-----------|
| At-least-once + idempotent consumer | try_claim by event_id | Yes – industry standard for exactly-once over unreliable transport |
| Deterministic idempotency key | request_id, correlation_id | Yes – CloudEvents spec: `id` enables deduplication |
| Retry on transient errors only | ConnectError, Timeout, 5xx | Yes |
| Exponential backoff with jitter | Plan specifies this | Yes – avoids thundering herd |
| No retry on lock-held paths | 0–1 retry for REQUEST_CREATED | Yes – minimizes lock hold |

### Implementation Order Correction

Add **Step 0.5** (or fold into Step 0):

| Step | Action |
|------|--------|
| **0.5** | **Integration-dispatcher**: Set `request_id` before sending REQUEST_CREATED. **Slack**: `slack-{event_id}` from payload (dedupes Slack retries). **Email**: uuid. Include in event_data. Use 0–1 retries for Slack path. |

### Verification Before Go-Live

- [ ] Integration-dispatcher REQUEST_CREATED includes request_id in event_data (Slack: `slack-{event_id}`; Email: uuid)
- [ ] Slack path uses 0–1 retries for broker publish; Email uses full retries
- [ ] All event types use deterministic event_id (no uuid.uuid4() for events that can be retried)
- [ ] Agent has try_claim for REQUEST_CREATED and DATABASE_UPDATE
- [ ] update_processed_event called in finally block after processing (so events don't stay "processing" indefinitely)
- [ ] CloudEventPublisher: remove retry OR add agent try_claim + deterministic event_id before enabling

---

## 11. Future Improvements: Loss Prevention

The current plan (Steps 0–6) achieves **zero duplicates** via deterministic event_id + receiver deduplication. It does **not** eliminate **producer-side loss**: if all retries fail, the event is dropped. Standard patterns to reduce or eliminate producer-side loss:

### 11.1 Transactional Outbox

**Goal**: Never lose an event due to publish failure. Write event to DB in the same transaction as the business write; a background job publishes and retries until success.

| Aspect | Plan |
|--------|------|
| **Outbox table** | `event_outbox(id, event_type, payload, created_at, status, last_error, retry_count)` |
| **Write path** | In same DB transaction as business write: INSERT into outbox. Commit. |
| **Publisher job** | Cron or worker polls outbox for `status=pending`; POST to broker; on success UPDATE status=published (or DELETE); on failure UPDATE last_error, retry_count, exponential backoff |
| **Deduplication** | Outbox row id or (event_type, idempotency_key) as event_id; receivers dedupe as today |
| **Ordering** | Optional: process in created_at order per session/correlation_id |

**When to implement**: After Steps 0–6 are stable. Highest impact for REQUEST_CREATED and AGENT_RESPONSE_READY (user-facing).

### 11.2 Dead Letter Queue (DLQ)

**Goal**: When publish retries are exhausted, write to DLQ instead of dropping. Enables manual or automated recovery.

| Aspect | Plan |
|--------|------|
| **DLQ storage** | Separate table `event_dlq` or broker DLQ topic (if broker supports it) |
| **Trigger** | When CloudEventSender/Publisher exhausts retries: write event + metadata (error, attempt count) to DLQ |
| **Recovery** | Manual: operator inspects DLQ, fixes root cause, re-publishes. Automated: job retries DLQ entries with backoff. |
| **Alerting** | Emit metric/alert when event lands in DLQ |

**When to implement**: Can be added independently. Complements transactional outbox (outbox publisher writes to DLQ when retries exhausted).

### 11.3 Background Retry Job (Simpler Variant)

**Goal**: Simpler than full outbox: a job that finds failed/pending records and retries publishing.

| Aspect | Plan |
|--------|------|
| **Source** | RequestLog rows with `status=send_failed` or similar; or a lightweight `publish_attempts` table |
| **Job** | Periodic job queries for retriable records; calls CloudEventSender for each; updates status on success |
| **Limitation** | Not transactional – business write and "publish intent" are separate. Risk: we record success but never publish (e.g. crash before job runs). Outbox is stronger. |

**When to implement**: Quick win if we already have `send_failed` or equivalent. Prefer outbox for new design.

### 11.4 Implementation Order (Future)

| Priority | Improvement | Rationale |
|----------|-------------|-----------|
| 1 | **DLQ** | Low effort, prevents silent loss; enables recovery and alerting |
| 2 | **Transactional outbox** | Highest correctness; requires schema + publisher job |
| 3 | **Background retry job** | Only if we have existing failed-state records and need a stopgap before outbox |

