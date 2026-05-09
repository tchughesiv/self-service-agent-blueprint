# Channel behavior policy — implementation plan

This document describes a unified **per–integration-type** (channel) policy for session semantics, default entry agents, return-to-router behavior, and delivery binding. The goal is to make new channel plugins **configuration-driven** instead of adding Zammad-style special cases across Request Manager, agent-service, and integration-dispatcher.

**Solution qualities (hold regardless of rollout phase):** Versioned policy documents, **snapshot-on-create** for stable semantics, **validation** against deployed agents, **bidirectional** unified-session isolation, and explicit **delivery binding** — these are architectural requirements for a sound multi-channel system, not optional polish.

**Rollout posture (operational, not architectural):** The first implementation may ship **without** zero-downtime migration automation, admin CRUD for policies, or compatibility shims for ancient session rows. That constrains **how** changes deploy (e.g. reset non-production DBs or tolerate breaking policy schema until tooling lands); it does **not** excuse ambiguous semantics or client-controlled policy injection. Always keep **`schema_version`** in stored blobs when the JSON shape evolves.

### TL;DR

- One **versioned policy** per `IntegrationType`, resolved at **session create** and **snapshotted** on `RequestSession.integration_metadata` (reserved key).
- **Session scope** (`PER_USER` vs `PER_TICKET`) + **delivery_binding** replace Zammad-only branches in RM, agent-service, and dispatcher.
- **`DEFAULT_AGENT_ID`** stays the global router/default entry; channels override **`entry_agent_id`** only when needed (e.g. ticket intake).
- Implement **bidirectional** unified-session filtering so ticket rows and web/Slack rows never cross-attach.
- **`requires_routing` / `target_agent_id`**: unchanged in v1 unless a channel explicitly needs “single agent only” later.

---

## 1. Goals

| Goal | Description |
|------|-------------|
| **Single abstraction** | Replace ad hoc `ZAMMAD` branches and paired env vars (`DEFAULT_AGENT_ID` + `ZAMMAD_DEFAULT_AGENT_ID`) with one policy model keyed by `IntegrationType`. |
| **Plugin-friendly** | New channels declare behavior in data (DB or packaged defaults), not scattered `if integration == X` in three services. |
| **Stable sessions** | Policy effective for a `RequestSession` is **resolved at session creation** and **snapshotted** on the row so mid-conversation edits do not change routing semantics unexpectedly. |
| **Safe defaults** | System-wide `DEFAULT_AGENT_ID` (router) remains the default entry when a channel does not override `entry_agent_id`. |

Non-goals for the first cut:

- Zero-downtime migration from existing env-only configuration.
- Supporting every hypothetical `session_scope` variant on day one (implement the ones needed to replace current behavior first).

---

## 2. Current state (summary)

| Area | Today |
|------|--------|
| **Default / entry agent** | `initial_current_agent_id_for_integration()` in `shared-models`: non-Zammad uses `DEFAULT_AGENT_ID`; Zammad uses `ZAMMAD_DEFAULT_AGENT_ID` or hardcoded `ticket-review-agent`, never falls back to `DEFAULT_AGENT_ID`. Duplicated logic in `agent-service` (`_zammad_default_agent_id_resolved`, `_entry_tier_agent_ids`). |
| **Return to routing agent** | `ResponsesSessionManager._handle_routing` and related paths use `_is_zammad_integration()` to disable auto return-to-router and to ignore `_should_return_to_routing` in some cases. |
| **Session cardinality** | Most channels: one active session per user (with `SESSION_PER_INTEGRATION_TYPE` to split by type). Zammad: stable `zammad-{ticket_id}` via `get_or_create_zammad_ticket_session`. Unified-session mode explicitly excludes `ZAMMAD` rows from cross-channel reuse. |
| **Dispatcher delivery** | Filters by `integration_context.platform == "zammad"` vs non-ticket traffic to avoid posting Slack/email replies to Zammad tickets and vice versa. |
| **DB** | `integration_default_configs` / `user_integration_configs` are **delivery** focused (Slack, email, Zammad API), not agent routing or session strategy. |

---

## 3. Proposed policy model

### 3.1 `ChannelBehaviorPolicy` (versioned document)

All fields are optional where a global default exists. Include **`schema_version`** (integer) on every stored blob.

| Field | Type | Purpose |
|-------|------|--------|
| `schema_version` | `int` | Increment when fields are added or semantics change. |
| `entry_agent_id` | `string \| null` | First agent for new sessions. `null` → use global `DEFAULT_AGENT_ID`. |
| `router_agent_id` | `string \| null` | Agent id used as the “return to router” target and for specialist-lock comparisons. `null` → use `DEFAULT_AGENT_ID` (single-router deployments). |
| `allow_return_to_router` | `bool` | If `false`, do not auto-reset to router from specialists (replaces Zammad’s “no auto return” behavior). Default `true`. |
| `session_scope` | `enum` | How to pick/create `RequestSession`. See §3.2. |
| `exclude_from_unified_session_pool` | `bool` | If `true`, other channels’ traffic must not reuse this integration’s session rows in unified-user mode (Zammad today). Default `false`. |
| `delivery_binding` | `enum` | How integration-dispatcher chooses sinks. See §3.3. |

**Validation (required):**

- `entry_agent_id` and `router_agent_id` must be **in the deployed agent allowlist** (same source as agent-service / LangGraph registration) or resolution **fails closed** at session create (structured log, operator-visible error, non-success response to the caller — avoid silent fallback to arbitrary agents).
- Reject unknown agent ids to avoid misconfiguration becoming privilege escalation to high-tool agents.

### 3.2 `session_scope` enum (first cut)

Start with values needed to **replace current code paths**; extend later.

| Value | Meaning |
|-------|--------|
| `PER_USER` | Reuse the latest active session for the canonical user, subject to `exclude_from_unified_session_pool` and global “per integration type” flag (see §4). |
| `PER_TICKET` | Stable session id derived from external ticket id (e.g. `zammad-{ticket_id}`). Requires `ticket_id` in request/session metadata. |
| `PER_THREAD` | (Future) Pin to `thread_id` + user. |
| `EXPLICIT` | Honor `metadata.session_id` / explicit id when provided; otherwise same as `PER_USER` or fail — define explicitly in code. |

Mapping from today:

- Slack / Web / Email / CLI (default path): `PER_USER` with `exclude_from_unified_session_pool: false` (or `true` if `SESSION_PER_INTEGRATION_TYPE` is enabled — see §4).
- Zammad: `PER_TICKET` + `exclude_from_unified_session_pool: true` + `allow_return_to_router: false` (match current product behavior unless you choose otherwise).

### 3.3 `delivery_binding` enum (first cut)

| Value | Meaning |
|-------|--------|
| `STANDARD` | Use existing user integration configs; non-ticket sessions must not use Zammad sink (equivalent to today’s “exclude ZAMMAD for non-ticket”). |
| `TICKET_THREAD` | Replies go only to the ticket integration (Zammad); other sinks excluded (equivalent to `platform == "zammad"` path). |

New channels with ticket-like threads add a value here instead of hard-coding `platform` strings in the dispatcher.

### 3.4 Ticket-specific UX (phased)

Ticket title merge and LangGraph state patches are **Zammad-shaped** in code today. **v1:** Gate them on `session_scope == PER_TICKET` (and integration type if needed). **Later:** Add an explicit `ticket_context: { enabled: bool, … }` to the policy schema when non-Zammad ticket backends appear.

---

## 4. Global toggles vs policy

**`SESSION_PER_INTEGRATION_TYPE`:** Today it changes whether session lookup filters by `integration_type`. Options:

- **A)** Map it to per–integration-type policy (e.g. a boolean `session_isolated_by_integration_type` on each channel) and remove the global env once migrated.
- **B)** Keep one global env and apply it only when `session_scope == PER_USER` (smaller initial change).

Recommend **A** for long-term clarity if operators can maintain per-channel flags in DB; else **B** as an incremental step.

**Recommended sequencing:** Implement **B** first (retain `SESSION_PER_INTEGRATION_TYPE` while behavior policy and ticket paths land), then migrate to **A** when per-channel isolation is worth the operational overhead — not a prerequisite for a correct design.

---

## 5. Storage

**Default approach:** On each row of **`integration_default_configs`** (already one row per `IntegrationType`), add a nested object **`config.channel_behavior`** holding the policy document for **that** integration. It is **not** a second map keyed by type—the row’s `integration_type` **is** the key.

Alternative: new table `channel_behavior_configs` with `integration_type` unique — cleaner separation if delivery JSON and behavior JSON start to fight for space.

**User overrides:** Defer. `user_integration_configs` is user-specific delivery; channel **behavior** is usually tenant-wide. If needed later, merge tenant defaults → user overrides with explicit precedence rules.

---

## 6. Resolution and snapshotting

### 6.1 Resolver

Implement **`resolve_channel_behavior(integration_type) -> ChannelBehaviorPolicy`** in `shared-models` (or a small dedicated module):

1. Load DB default for `IntegrationType`.
2. Merge with **code defaults** for each enum member (so tests run without DB).
3. Optional **environment overrides** for undeployed-operator setups (e.g. dev): map `DEFAULT_AGENT_ID` to default router and default entry when unset in DB — document that production should prefer DB-backed policy with env as bootstrap only.

### 6.2 Where to snapshot

On **every session create** (including `get_or_create_zammad_ticket_session` and generic `create_or_get_session_shared`):

1. Resolve policy.
2. Persist on `RequestSession`:
   - **`integration_metadata["_channel_behavior"]`** — preferred fixed key (underscore prefix signals system-owned); **never copy from client-supplied metadata**.
   - **Merge rule:** strip reserved keys (`_channel_behavior`, any future `_channel_*`) from inbound metadata, then `integration_metadata = { **client_metadata, **server_fields }` so server snapshot always wins.
   - Optionally duplicate `schema_version` on `conversation_context` for debugging — not required if the blob is self-describing.

3. Set **`current_agent_id`** from resolved `entry_agent_id` (replacing `initial_current_agent_id_for_integration()` for new sessions).

**Order of operations for `PER_USER` + explicit pin:** If `metadata.session_id` is present and valid, resolve that session **before** “pick latest active” (aligns `EXPLICIT` / continuation behavior with existing `create_or_get_session_shared`).

### 6.3 Reads on subsequent requests

- **agent-service:** Prefer snapshot from `RequestSession` loaded by `session_id`. If missing (legacy row without snapshot), re-resolve from `integration_type` via the resolver **or** fail closed with a clear error — pick one behavior and document it; do not silently invent policy from the inbound request alone.

---

## 7. Service-by-service changes

### 7.1 Request Manager (`communication_strategy.py`, `session_events.py`, normalizer)

- Centralize session creation in a **single strategy** driven by `session_scope`:
  - `PER_TICKET` → existing Zammad path generalized (ticket id from request/metadata).
  - `PER_USER` → existing shared session logic; honor `exclude_from_unified_session_pool` instead of hard-coded `IntegrationType.ZAMMAD` in SQL filters.
  - **Bidirectional isolation:** When scanning for an active `PER_USER` session, exclude rows whose snapshot/policy implies isolation **and** ensure non-isolated traffic never attaches to ticket-scoped rows (today’s dual-sided `ZAMMAD` exclusion).
- Ensure CloudEvent session-create path applies the same resolver + snapshot as HTTP/sync path.
- **Normalizer:** Keep setting `integration_context.platform` for backward compatibility during refactor, or replace dispatcher checks with `delivery_binding` read from session/policy.
- **Response → dispatcher (`main.py` `_forward_response_to_integration_dispatcher`):** Today the delivery event’s `integration_context` is built from **`RequestLog.normalized_request`** for the **`request_id`**, not from `RequestSession`. Session snapshots (`_channel_behavior`, `delivery_binding`) are **invisible** to `dispatcher.dispatch()` unless you **merge** policy from `RequestSession` (lookup by `session_id` when forwarding) **or** duplicate binding onto the stored normalized request at ingest. Otherwise session row and delivery path can drift.

### 7.2 Agent-service (`session_manager.py`)

- Replace **`ROUTING_AGENT_NAME`** class-constant usage with **`effective_router_id`** from policy snapshot (instance field updated when session loads).
- Replace `_entry_tier_agent_ids()` with policy: `{entry_agent_id, router_agent_id}` resolved to concrete strings.
- Replace `_is_zammad_integration()` checks with:
  - `not policy.allow_return_to_router` where routing-back is blocked,
  - or explicit `session_scope == PER_TICKET` where ticket-specific **prompt/state** patches apply (ticket title merge, LangGraph patch — consider renaming to `_needs_ticket_context()` fed by policy flags such as `ticket_context.enabled`).
- Thread policy into `handle_responses_message` from DB session row after load, not only from `integration_type` string on the request (request may supplement but session snapshot wins).
- After loading `RequestSession` by `session_id`, set **`_integration_type_str` and policy from the row**; if the inbound request’s `integration_type` disagrees, **log a warning** (integrity / bug signal).

**`requires_routing` / `target_agent_id` (v1):** Leave normalizer behavior as today (`target_agent_id` unset; routing from LangGraph). Revisit only if you add a policy flag such as `single_agent_channel` later.

### 7.3 Integration-dispatcher (`main.py`)

- Replace `is_zammad_ticket = ic.get("platform") == "zammad"` with policy-driven binding (see §7.3.1).
- **Interim:** Derive from `IntegrationType` + `integration_default_configs` defaults (no session read) — weaker if per-session overrides exist later.

Goal: ticket-bound replies stay isolated without mentioning Zammad by name.

#### 7.3.1 Delivery payload vs session snapshot (code trace)

Integration-dispatcher only sees what arrives on **`DeliveryRequest`** (CloudEvent or `/deliver`). That payload is assembled in **Request Manager** when forwarding the agent response: **`integration_context_for_delivery`** is copied from **`RequestLog.normalized_request.integration_context`**, not from **`RequestSession.integration_metadata`**.

Implications for this plan:

| Topic | Action |
|-------|--------|
| **`delivery_binding` on session** | Will **not** affect dispatch filtering until RM (or another writer) puts binding into the forwarded **`integration_context`** or a dedicated field on the delivery event. |
| **Align with snapshot** | Prefer: when forwarding, load **`RequestSession`** by **`session_id`**, read **`integration_metadata._channel_behavior`** (or denormalized `delivery_binding`), and **merge** into `integration_context_for_delivery` so dispatcher logic stays consistent with session-scoped policy. |
| **`platform` today** | Handlers and tests key off **`integration_context.platform`** (e.g. `zammad`). Either keep **`platform`** as a **derived** field for backward compatibility during refactor, or update handlers/tests in the same change set. |

### 7.4 Helm / env

- Document **`DEFAULT_AGENT_ID`** as the global router and default entry fallback (bootstrap + single source for router identity unless extended).
- Remove **`ZAMMAD_DEFAULT_AGENT_ID`** once Zammad’s row in `integration_default_configs` sets `entry_agent_id`.
- Remove or narrow **`SESSION_PER_INTEGRATION_TYPE`** if folded into policy (§4).

---

## 8. Security and correctness

| Topic | Action |
|-------|--------|
| **Trust** | Snapshot only server-side; strip client attempts to set `_channel_behavior` in public/metadata ingress paths. |
| **Agent ids** | Validate against allowlist at resolution time. |
| **Delivery isolation** | Preserve invariant: ticket-bound sessions do not deliver to non-ticket integrations and vice versa — encoded in `delivery_binding`. |
| **Observability** | Log `schema_version`, `integration_type`, and `session_id` on session create failures due to validation. |

---

## 9. Testing strategy (v1 scope)

- **Unit:** Resolver merge order; validation failures; entry-tier set from policy.
- **Integration:** Zammad path: one session per ticket, no auto return to router. Slack/Web path: return to router from specialist when allowed.
- **Dispatcher:** Matrix: ticket session vs non-ticket session → correct integration types included/excluded.
- **Regression:** Cross-channel session pollution — web request must not resume `zammad-*` session and vice versa (tests for bidirectional exclusion).

---

## 9.1 Definition of done (first milestone)

- [ ] No remaining **`if integration == ZAMMAD`** for routing/session/delivery **where policy fields apply** (wrappers that dispatch on `session_scope` / `delivery_binding` are OK).
- [ ] New channel can be described by **DB/code defaults only** without editing agent-service routing switch statements.
- [ ] Snapshot present on newly created sessions; agent-service reads behavior from row first.

---

## 10. Implementation checklist (ordered)

1. [ ] Add `ChannelBehaviorPolicy` Pydantic model + enum types + `schema_version` in `shared-models`.
2. [ ] Add resolver + DB/code defaults; seed Zammad row via migrations or bootstrap script. **Single shared helper** for agent-id allowlist used by RM + agent-service.
3. [ ] Request Manager: snapshot on create; set `current_agent_id` from policy; generalize session lookup (`exclude_from_unified_session_pool`, `PER_TICKET`, bidirectional exclusion).
4. [ ] Agent-service: load policy from session row; **`integration_type` from row authoritative**; replace `_is_zammad_integration` routing branches and entry-tier / router id logic; log request/row mismatch.
5. [ ] Integration-dispatcher + **Request Manager forwarder**: gate delivery using `delivery_binding`; ensure **`_forward_response_to_integration_dispatcher`** enriches `integration_context` from **`RequestSession`** (or equivalent) so dispatch does not rely only on **`RequestLog`** (**record approach** in §11 or code comment — see §7.3.1).
6. [ ] Helm/docs: single router env; channel-specific entry in DB for Zammad.
7. [ ] Delete dead env vars and hard-coded Zammad branches once tests pass.

---

## 11. Open decisions

- **Exact snapshot key name:** `_channel_behavior` vs `channel_behavior_v1` — pick one and document in code.
- **Dispatcher session lookup:** Optional DB read adds latency vs trusting normalized request fields only — document latency budget and consistency guarantees when choosing (**implementation detail:** RM may load session when **forwarding** the agent response — §7.3.1).
- **Multiple routers:** If `router_agent_id` can differ from `DEFAULT_AGENT_ID`, LangGraph / registry must expose both graphs; otherwise **constrain v1** to `router_agent_id == DEFAULT_AGENT_ID` until multi-graph support exists.

---

## 12. References (code)

| Concern | Location |
|---------|----------|
| Session create / Zammad ticket sessions | `request-manager/communication_strategy.py`, `shared-models/session_manager.py` (`get_or_create_zammad_ticket_session`) |
| Initial agent id | `shared-models/session_manager.py` (`initial_current_agent_id_for_integration`) |
| Routing / Zammad exceptions | `agent-service/session_manager.py` (`_handle_routing`, `_is_zammad_integration`, `_entry_tier_agent_ids`) |
| Delivery filter | `integration-dispatcher/main.py` (Zammad ticket vs non-ticket configs) |
| Delivery payload source | `request-manager/main.py` (`_forward_response_to_integration_dispatcher` — `integration_context` from `RequestLog.normalized_request`) |
| Helm agent defaults | `helm/values.yaml` (`defaultAgentId`, `zammadDefaultAgentId`) |

---

## 13. Plan audit (historical)

Earlier gap analysis; **most items are folded into §3–§7, §9.1, and §10** above. Remaining watch items:

| Topic | Where addressed |
|-------|------------------|
| Bidirectional session isolation | §7.1, §9 testing |
| Metadata merge / reserved keys | §6.2 |
| Agent-service authoritative row | §7.2, §10 item 4 |
| `requires_routing` v1 | TL;DR, §7.2 |
| Ticket UX deferral | §3.4 |
| Storage wording | §5 |
| SESSION_PER_INTEGRATION rollout choice | §4 |
| Delivery uses RequestLog, not session row | §7.1 bullet “Response → dispatcher”, §7.3.1, §12 |

### Risks (still worth a glance before coding)

| Risk | Note |
|------|------|
| **Two routers** | Until multi-graph support exists, constrain `router_agent_id` to `DEFAULT_AGENT_ID`. |
| **Dispatcher / RM enrichment** | Session snapshot alone does not reach dispatcher until RM forwarder merges it — §7.3.1, §10 item 5. |

---

## 14. Further improvements (optional doc / product)

- **Diagram:** One mermaid sequence: inbound → RM session policy snapshot → agent-service reads row → **RM forwarder merges binding into delivery `integration_context`** → dispatcher `delivery_binding`.
- **Admin/API:** Add CRUD for `integration_default_configs.channel_behavior` with validation when operators need self-service changes without raw SQL.
- **Failure modes:** User-visible message when policy validation fails at session create (vs opaque 500).
- **Cross-service contract:** Publish the JSON schema of `_channel_behavior` (or OpenAPI component) in-repo when Pydantic model stabilizes.
