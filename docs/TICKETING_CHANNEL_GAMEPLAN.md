# Ticketing Channel: Game Plan

**North star:** [APPENG-4759](../APPENG-4759) — Zammad → app webhook flow, synthetic user id `{customer-email}-{ticket_number}`, **both new tickets and follow-ups inject into request manager** (same endpoint); product rules still differ for **which** tickets get automation vs **waiting for human review**. **Customer-visible** replies are delivered via **Integration Dispatcher** (canonical **`content`** → Zammad REST); **MCP** is for **ticket actions**, not for mirroring the primary reply ([§4.2.2](#422-plan-zammad-customer-visible-reply-via-integration-dispatcher)).  
**Status:** Webhook ingress landed (`POST /zammad/webhook`); **Zammad Webhook + Trigger bootstrap** via `zammad-bootstrap` + Helm (`§5.2.1`). **Still open for APPENG-4759:** ticket-state / agent-managed vs human-review mapping, **dispatcher** customer-visible delivery + **`zammad_add_article` removal**, ingest branching beyond env filters; cluster E2E.  
**Branch:** `ticketingChannel` (should be narrowed to 4759-only before merge; see §1.2).  
**Last updated:** 2026-04-27 (§4.2.2: dispatcher owns customer-visible reply; **mandatory** removal of `zammad_add_article` from prompts + MCP with dispatcher ship)

---

## 0. APPENG-4759 — Scope and acceptance (summary)

Source of truth: repo file [`APPENG-4759`](../APPENG-4759) at repository root (commit it so reviewers and CI share the same text; Jira APPENG-4759 remains the system of record for status).

| Requirement (4759) | Meaning for this codebase |
|--------------------|---------------------------|
| **New ticket** → request manager | Ingest initial customer message; synthetic **`user_id` = `{email}-{ticket_number}`** (not only `zammad-{ticket_id}` session keys). |
| **New comment** on **agent-managed laptop refresh** tickets | Same webhook endpoint → **request manager** (same pipeline as new tickets); stable **`user_id`**; 4759 notes **ticket owner’s id** when the article author is not the customer (not yet implemented in webhook). |
| **Agent reply** only for agent-managed laptop refresh | **Dispatcher** must not post customer-visible **`content`** when ticket is **waiting for human review**; align with Zammad state / group / tag convention TBD. **MCP** must not use **`zammad_add_article`** for the primary reply once §4.2.2 ships — tool is **removed**. |
| **Triggers** | Zammad **Manage → Triggers** → POST `https://<integration-dispatcher>/zammad/webhook` with shared secret; **HMAC-SHA1** `X-Hub-Signature` ([Zammad triggers](https://admin-docs.zammad.org/en/latest/manage/trigger/how-do-they-work.html)). |
| **Customer articles only** | Fire on ticket create and/or article create with sender **Customer** (or External); ignore internal/agent notes. |
| **Replies (customer-visible)** | **Integration Dispatcher** posts **canonical pipeline `content`** to Zammad REST when `integration_context.platform == zammad` (Slack/email-style **delivery**). **Mandatory (after dispatcher ships):** remove **`zammad_add_article`** from **ticket LangGraph prompts**, **`allowed_tools`**, and the **`zammad_mcp`** server so the model cannot duplicate the pipeline reply or bypass delivery. **MCP** remains for **ticket operations** (escalate, close, tags, `get_employee_laptop_info`, etc.). See [§4.2.2](#422-plan-zammad-customer-visible-reply-via-integration-dispatcher). |

---

## 1. Branch audit and PR hygiene

### 1.1 What this branch currently contains (vs `dev`)

The branch is **not** “docs only.” It typically includes **multiple merged intents**:

| Slice | Typical origin | Needed for 4759? |
|-------|----------------|------------------|
| Phase 1 **ticket agents** + **evals** (`ticket-review-agent`, `ticket-laptop-refresh`, `evaluations/flows/ticket_laptop_refresh`, etc.) | e.g. [#347](https://github.com/rh-ai-quickstart/it-self-service-agent/pull/347) on `dev` | **Indirectly:** defines the **laptop refresh ticket** agent story 4759 refers to; **not** the webhook or id format. |
| **Zammad Helm / bootstrap users** | `helm/zammad` + **`zammad-bootstrap`** Job (`bootstrap.py`: groups, users, attrs, **optional Webhook + Trigger**) | **Operational prerequisite** for a real Zammad env; **not** specified in 4759 text. Webhook triggers: **automated** when `bootstrap.integrationWebhook.enabled` / §5.2.1; **manual** §5.2 for BYO Zammad. |
| **ServiceNow client** tweaks | e.g. #348 | **Out of scope** for 4759. |
| **Zammad foundation** (this effort) | `ZAMMAD` type, migration, `ZammadRequest`, RM handler, normalizer → **`ticket-laptop-refresh`**, `POST /zammad/webhook`, **`zammad_service`** (HMAC, dedupe, filters), **`user_id` = `{email}-{ticket}`**, dispatcher **REST** customer-visible delivery ([§4.2.2](#422-plan-zammad-customer-visible-reply-via-integration-dispatcher)), Zammad MCP for **actions** (no **`zammad_add_article`** in steady state) | **Remaining 4759:** strict **ticket-type branching** (only laptop-refresh follow-ups vs all customer articles), **owner/author `user_id`**, **dispatcher** delivery gating vs **waiting for human review**. |

**Regroup recommendation:** For a PR titled around **APPENG-4759**, **rebase onto current `dev`** (so #347/#353/#348 are not duplicated in PR history), then ensure the **diff is only** webhook + routing + id rules + reply gating + tests/docs for 4759. Keep ticket eval agents on `dev` as their own concern unless this PR truly depends on them for E2E.

### 1.2 Documents in this branch

| Document | Keep for 4759 PR? |
|----------|-------------------|
| `TICKETING_CHANNEL_GAMEPLAN.md` (this file) | **Yes** — scope and checklist. |
| `ZAMMAD_TICKETING_CHANNEL_PLAN.md` | **Yes** — technical payloads/MCP reference. |

### Key decision: Zammad

Zammad is the ticketing channel because it provides:

- Purpose-built helpdesk
- Built-in chat widget (no custom UI)
- Straightforward webhooks and official Helm chart
- Mature community MCP (basher83/Zammad-MCP)

---

## 2. Implementation Touchpoints (from Audit)

All references verified against current codebase. *Note: Line numbers approximate; reqMgrOrder rebase altered request-manager structure (session_orchestrator, etc.).*

| Component | Location | Current State | Required Change |
|-----------|----------|----------------|-----------------|
| **IntegrationType enum** | `shared-models/.../models.py` | Includes `ZAMMAD` | — |
| **Request schemas** | `request-manager/.../schemas.py` | Includes `ZammadRequest` | — |
| **Request Manager handler** | `request-manager/.../main.py` | CloudEvent path includes `ZAMMAD` → `ZammadRequest` | — |
| **Normalizer** | `request-manager/.../normalizer.py` | `_normalize_zammad_request` → `ticket-laptop-refresh` | **4759 gap:** Single specialist for all Zammad events today. Still need **branching:** new ticket vs **agent-managed laptop refresh** + **`user_id` = `{email}-{ticket_number}`** from webhook (see §2.3). |
| **Integration Dispatcher** | `integration-dispatcher/.../main.py` | Slack, **POST `/zammad/webhook`**, email | Configure trigger URL + secret (Helm). |
| **Webhook services** | `integration-dispatcher/` | `slack_service`, **`zammad_service`**, `email_service` | `zammad_service`: HMAC, filters, outbox + `send_request_event`. |
| **Helm MCP blocks** | `helm/values.yaml` | `mcp-servers.mcp-servers.zammad` + top-level `zammad` values (optional enable) | Wire secrets/URL + `zammad.webhookSecret` for production HMAC |
| **Agent config** | `agent-service/config/agents/` | `ticket-laptop-refresh-agent.yaml` includes **snow + zammad** MCP | Phase 1 ticket agent + Zammad MCP for APPENG-4759 replies |
| **Agent Service** | `agent-service/.../main.py`, `session_manager.py` | Passes `target_agent_id` / `requires_routing`; specialist session when `requires_routing=False` | — |

**DB migration:** Adding enum values to PostgreSQL `IntegrationType` requires an Alembic migration (used in `UserIntegrationConfig`, `RequestLog`, etc.).

### 2.3 APPENG-4759 — Implemented vs remaining

| Item | Status | Notes |
|------|--------|--------|
| `IntegrationType.ZAMMAD` + Alembic `002` | Done | Enables RM + dispatcher to recognize Zammad. |
| `ZammadRequest` + RM CloudEvent branch | Done | Assumes `user_id` already in event; **must** be set by webhook to **`{email}-{ticket_number}`** per 4759. |
| `session_id` / partition key | Done | **`zammad-{ticket_id}`** is the stable `RequestSession.session_id` (per ticket, per user); migration **003** relaxes the one-active-session rule for `ZAMMAD`. Broker ordering unchanged. |
| Specialist entry (`target_agent_id`, `requires_routing`) | Done | Agent-service + session manager; normalizer targets **`ticket-laptop-refresh`**. **4759 follow-up:** webhook-level **ticket-type branching** (e.g. only ingest laptop-refresh follow-ups; triage for new tickets) when product rules are fixed. |
| `ticket-laptop-refresh` + `laptop-refresh` KB + Zammad MCP | Done (actions); delivery **planned / in flight** | Same agent as phase-1 ticket flow; **Zammad MCP** for ticket **actions**. **Customer-visible** text via **Integration Dispatcher** + **mandatory removal** of `zammad_add_article` ([§4.2.2](#422-plan-zammad-customer-visible-reply-via-integration-dispatcher)). |
| Dispatcher **Zammad** delivery | Planned / in flight | **REST** posts canonical **`content`**; replaces historical **no-op** + MCP-only public reply ([§4.2.2](#422-plan-zammad-customer-visible-reply-via-integration-dispatcher)). **`zammad_add_article` removal mandatory** with dispatcher ship. |
| **`POST /zammad/webhook`** | **Done** | FastAPI route on integration-dispatcher. **In-cluster Zammad:** set trigger URL to `http://<release>-integration-dispatcher.<ns>.svc.cluster.local/zammad/webhook` — no external Route. External **`ssa-integration-dispatcher`** stays **`path: /slack`** for Slack only. |
| HMAC, idempotency (`X-Zammad-Delivery`), feedback-loop, group allowlist | **Partial** | HMAC-SHA1 `X-Hub-Signature`; atomic claim `zammad-delivery-{id}`; optional `ZAMMAD_AI_AGENT_USER_ID`, `ZAMMAD_ALLOWED_GROUP_IDS`, blocked states / required tags. **Prefer** narrowing with **Zammad trigger conditions** (`ZAMMAD_TRIGGER_GROUP_IDS` / `ZAMMAD_TRIGGER_TAGS_*` in bootstrap — §5.2.1); dispatcher env vars are optional defense-in-depth. |
| Ticket state: **agent-managed laptop refresh** vs **waiting for human review** | **Not done** | **4759 blocker** for correct routing and **dispatcher** customer-visible gating. Define mapping (Zammad tag, group, custom field, or state). |

### 2.0 Rebase: reqMgrOrder (Session Serialization)

*Rebased on reqMgrOrder; 3 commits: session request serialization, retry docs, legacy resolver.*

**Migration chain:** `001_consolidated_schema.py` → `002` (Zammad enum) → **`003_zammad_ticket_scoped_sessions`** (partial unique index excludes `ZAMMAD`; widens `session_id` columns).

**Session serialization:** Zammad requests participate in the same FIFO/session-lock pipeline. **`session_id = zammad-{ticket_id}`** scopes context per ticket (multiple active Zammad sessions per user). Flows through `session_orchestrator`, `RequestLog`, advisory lock like Slack/Email.

**Integration Dispatcher:** reqMgrOrder adds `outbox_publisher`, `thread_lock`, `outbox_metrics`. Phase 2 `zammad_service` should follow the same event-send pattern as `slack_service`/`email_service` (e.g. outbox for durable publish if they use it).

**Reference:** [SESSION_SERIALIZATION_RUNBOOK.md](SESSION_SERIALIZATION_RUNBOOK.md) — ordering, partition keys, reclaim, 503 behavior. Zammad aligns with existing model.

### 2.1 ZammadRequest Schema & event_data Structure

**ZammadRequest fields** (per [ZAMMAD_TICKETING_CHANNEL_PLAN.md](ZAMMAD_TICKETING_CHANNEL_PLAN.md)):

| Field | Type | Required | Purpose |
|-------|------|----------|---------|
| `integration_type` | IntegrationType | yes | ZAMMAD |
| `user_id` | str | yes | From `customer_id`/`origin_by_id` mapping |
| `content` | str | yes | Article body (user message) |
| `ticket_id` | int | yes | Ticket ID for session continuity |
| `article_id` | int | yes | Article ID |
| `group_id` | int | yes | For allowlist check |
| `group_name` | str | no | Human-readable |
| `owner_id` | int | no | Ticket owner |
| `created_by_id` | int | yes | Article creator (for feedback-loop filter) |
| `zammad_delivery_id` | str | yes | From X-Zammad-Delivery header; idempotency |
| `request_type` | str | yes | e.g. `zammad_ticket_article` |
| `metadata` | dict | no | Additional context |

**event_data** (what `zammad_service` must produce for `send_request_event`):

```python
{
    "user_id": str,                    # Required
    "content": str,                   # Article body
    "integration_type": "ZAMMAD",
    "request_type": "zammad_ticket_article",
    "session_id": f"zammad-{ticket_id}",
    "metadata": {"ticket_id": int, "article_id": int, "group_id": int, ...},
    "integration_context": {"ticket_id": int, "article_id": int, "group_id": int, "zammad_delivery_id": str},
    "ticket_id": int,
    "article_id": int,
    "group_id": int,
    "created_by_id": int,
    "zammad_delivery_id": str,
}
```

### 2.2 Agent Service `target_agent_id` flow

**Status: implemented.** `agent-service/main.py` passes `target_agent_id` and `requires_routing` into `handle_responses_message`; `session_manager` creates a specialist session when `target_agent_id` is set and `requires_routing=False` (Zammad normalizer uses this for `ticket-laptop-refresh`).

---

## 3. Product direction (APPENG-4759)

Scope is **laptop refresh** in the **Zammad ticket channel**, not a separate generic IT triage agent.

| Topic | Direction |
|-------|-----------|
| **Conversation** | Session continuity via `ticket_id` / `session_id`; multi-turn in ticket articles |
| **Laptop refresh** | `ticket-laptop-refresh` agent (phase-1 ticket flow) + **laptop-refresh** KB + **snow** + **zammad** MCP |
| **Safeguards** | Existing shields on `ticket-laptop-refresh-agent`; KB from `knowledge_bases/laptop-refresh/` |
| **Escalation / human** | Zammad MCP (`escalate_for_human_review`, `close`, `send_to_manager_review`, `mark_as_agent_managed_laptop_refresh`, etc.) — **not** `zammad_add_article` once §4.2.2 is implemented; **waiting for human review** must suppress automated replies (see §0) |

### Open / TBD

- Mapping Zammad state/tag/group → **agent-managed laptop refresh** vs **waiting for human review**
- Optional later: time-based triggers, richer triage outside 4759

---

## 4. Possibilities & Trade-offs

### 4.1 Phase Ordering

**Quickstart scope:** Assumes **Zammad is already deployed** (or teams bring their own). Phases focus on webhook + MCP integration.

**Quickstart phases:**

0. **Prerequisite:** Deploy Zammad (`ticketingZammad.enabled` via `make helm-install-ticketing`, or external / BYO instance)
1. MCP + **`ticket-laptop-refresh`** agent config (snow + zammad MCP)
2. Webhook route + `IntegrationType` + schemas + normalizer (**ticket-type branching** when product rules are fixed)
3. Chat widget setup (AI agent user, availability) — optional demo
4. Demo seed data, Helm integration — optional

**Recommendation:** Stick with plan order — MCP without incoming requests is hard to validate end-to-end. Webhook in Phase 2 gives a real trigger.

---

### 4.2 Outgoing delivery: Zammad (evolving)

**Historical note:** The stack originally treated **customer-visible** ticket text as **MCP-only** (`zammad_add_article`), with a **no-op** `ZammadIntegrationHandler` so the agent-response delivery pipeline did not error. In practice, **LLM tool compliance is not sufficient** for “the ticket always shows what Request Manager received,” and **duplicates** appear if both MCP and a future REST poster run.

**Direction:** Treat **canonical pipeline `content`** like other channels: **Integration Dispatcher** **posts the customer-visible article** when the originating request carries **Zammad `integration_context`** (`ticket_id`, etc.). **MCP** stays for **agent-driven ticket actions** — **not** for posting the primary customer-visible paragraph once delivery is live.

The dispatcher's `ZammadIntegrationHandler` (or equivalent branch) **performs REST `ticket_articles`** posting for that **`content`**. **`zammad_add_article` is removed** from prompts, agent **`allowed_tools`**, and the **Zammad MCP server** in the same release train as dispatcher delivery ([§4.2.2](#422-plan-zammad-customer-visible-reply-via-integration-dispatcher)) so there is **no** duplicate path.

#### 4.2.2 Plan: Zammad customer-visible reply via Integration Dispatcher

| Topic | Decision |
|-------|----------|
| **Problem** | Model often skips **`zammad_add_article`**; user sees **no** agent text on the ticket even when RM/agent completed successfully. |
| **Source of truth for “what the customer should see”** | **`content`** in the **agent.response** / **RequestLog** path (same string RM polling and downstream treat as the answer). |
| **Delivery owner** | **Integration Dispatcher**: on **`com.self-service-agent.agent.response-ready`**, if **`integration_context.platform == "zammad"`** and **`ticket_id`** is present, **POST** a **customer-visible** article (e.g. `ticket_articles` with `internal: false`, type as required by your Zammad config) using **`ZAMMAD_URL` + `ZAMMAD_HTTP_TOKEN`** from **`zammad-credentials`**. |
| **Plumb `integration_context`** | Request Manager already loads it from **`RequestLog.normalized_request`** for Slack/email fields; **include the full `integration_context` object** (or at least `platform`, `ticket_id`, `group_id`) on the **forwarded delivery CloudEvent** so the dispatcher does not re-query RM. |
| **Helm** | Inject **`ZAMMAD_URL` / `ZAMMAD_HTTP_TOKEN`** into **integration-dispatcher** and **Zammad MCP** when **`ticketingZammad.enabled`**, from **`zammad-credentials`**. **Request Manager** does not mount Zammad REST credentials. |
| **Rollouts after secret changes** | Pods do **not** reload `secretKeyRef` env until recreated. **`zammad-bootstrap`** (token path) restarts **MCP** + **integration-dispatcher** so they pick up **`zammad-credentials`**; **Request Manager** is not restarted (it does not mount Zammad REST token/url). After changing **`integration-secrets`** (e.g. `ZAMMAD_WEBHOOK_SECRET`), roll any pod that consumes that secret (e.g. integration-dispatcher) if needed. |
| **MCP `zammad_add_article` (removal mandatory)** | After **dispatcher REST posting** is verified in the target environment: **(1)** strip all **`zammad_add_article`** instructions and tool entries from **ticket-laptop-refresh** LangGraph YAML (**small / big / scout**); **(2)** **delete** the **`zammad_add_article`** tool from **`mcp-servers/zammad`** (and Basher passthrough if exclusive); **(3)** update unit/integration tests and docs. Internal-only articles in product, if ever needed, use a **different** mechanism (e.g. new MCP tool name or ops-only REST), not a second public mirror of **`content`**. |
| **Escalate / close / manager / tags** | Remain **MCP** (agent decisions) unless product later moves them to rules/workflow. |
| **Idempotency / dedup (later)** | Optional: skip dispatcher post if a prior turn already created an article for the same `request_id`, or feature-flag **`ZAMMAD_POST_PIPELINE_REPLY`**. |
| **Extra LangGraph “retry until article” hop** | Optional **mitigation** only; still **model-dependent**. Prefer **dispatcher** for a **hard** guarantee of visibility. |
| **Docs / APPENG-4759** | Update acceptance language from “MCP-only replies” to “**visible reply** = pipeline **content** delivered to Zammad; **MCP** for **actions**.” |

**Implementation checklist (when built):**

1. Extend **Request Manager** `_forward_response_to_integration_dispatcher` to attach **`integration_context`** to the delivery payload.  
2. Extend shared **`DeliveryRequest`** (or event shape) to carry **`integration_context`**.  
3. **Integration Dispatcher** `handle_cloudevent`: after building `DeliveryRequest`, if Zammad context + creds → **`post_customer_visible_article`**.  
4. **Helm:** dispatcher env for Zammad credentials when ticketing enabled.  
4b. **Operations:** document **integration-dispatcher** / **MCP** rollout when **`zammad-credentials`** or webhook secrets change; **`zammad-bootstrap`** already restarts MCP + integration-dispatcher after token refresh (see §4.2.2 table).  
5. **LangGraph (mandatory):** remove **`zammad_add_article`** from all **`ticket-laptop-refresh`** prompts and **`allowed_tools`** (**small / big / scout** YAML).  
6. **MCP server (mandatory):** remove **`zammad_add_article`** from **`zammad_mcp/server.py`**; fix **`mcp-servers/zammad/tests`** and any callers.  
7. **Tests:** unit test REST helper; route test with mocked Zammad; confirm **no** MCP tool remains for public article.  
8. **Evals:** E2E asserts **dispatcher-posted** article (or ticket content), not MCP **`zammad_add_article`**.

#### 4.2.1 Audit trail and LangGraph context (already in stack)

**Question:** If the user-visible reply is delivered by **Integration Dispatcher** (not MCP `zammad_add_article`), do we still persist the agent output for audit and for the next turn?

**Yes** — logging and session state are unchanged; only the **transport** to Zammad’s UI moves to dispatcher **REST** for the primary customer-visible body:

| Concern | How it works today |
|---------|-------------------|
| **`RequestLog`** | After each request, **agent-service** `publish_response()` writes **`response_content`**, **`response_metadata`**, **`agent_id`**, **`completed_at`**, **`status`** on the row keyed by **`request_id`** (same path for all integrations). **Request-manager** then consumes the **`agent.response`** CloudEvent and **`UnifiedResponseHandler.process_agent_response`** updates **`RequestLog`** again for consistency / polling. |
| **Delivery pipeline** | Request-manager **forwards** a delivery-oriented event to integration-dispatcher. For **ZAMMAD**, **`ZammadIntegrationHandler.deliver`** **posts** the customer-visible article via REST once §4.2.2 is implemented (today may still be **no-op** until shipped). |
| **LangGraph / next-turn context** | Specialist flow uses **`ResponsesSessionManager`** + **`ConversationSession`** with **`AsyncPostgresSaver`** (see `agent-service/.../postgres_checkpoint.py`, `lg_flow_state_machine.py`). **`request_sessions.conversation_thread_id`** ties the RM session (`zammad-{ticket_id}`) to the checkpointed thread so **multi-turn** ticket conversations stay coherent. |

**Caveats (document, don’t over-promise):**

- **`RequestLog.response_content`** is the **agent pipeline’s final text** for that **`request_id`**. The **ticket UI** should show that same text via **dispatcher→REST** ([§4.2.2](#422-plan-zammad-customer-visible-reply-via-integration-dispatcher)); **`zammad_add_article`** is **not** part of the steady-state path. Optional **`response_metadata`** (e.g. Zammad article id from dispatcher) can tie **`RequestLog`** rows to Zammad records for audit.
- **Compliance / long-term archival** in Zammad itself is orthogonal: the helpdesk record is the ticket; our DB is the **blueprint processing** audit.

---

### 4.3 Alembic Migration Strategy

`IntegrationType` is stored in DB (enum column). Adding new values:

- PostgreSQL: `ALTER TYPE integrationtype ADD VALUE 'ZAMMAD'`
- Alembic: Migration that runs the `ALTER TYPE` for each new value
- Order: Add migration in same PR as IntegrationType code change, or in a preceding migration-only PR

---

## 5. Proposed Implementation Roadmap

**Aligned to APPENG-4759:** The **merging PR** should close the ticket, not just land adjacent work. Foundation-only PRs are fine **only** if followed immediately by webhook + routing + gating in the same release train.

*Phase ordering: See [Section 4.1](#41-phase-ordering).*

### PR Strategy — Usable Functionality Per PR

| PR | Scope | Usable outcome | How to verify |
|----|-------|----------------|---------------|
| **PR A (foundation)** | `ZAMMAD` type, migration, `ZammadRequest`, RM path, normalizer → `ticket-laptop-refresh`, specialist plumbing, dispatcher **REST** customer-visible delivery + **remove** `zammad_add_article`, Zammad MCP on ticket agent for **actions** | Injected CloudEvent → agent runs → dispatcher posts **`content`** to ticket | Unit tests + manual / mock CloudEvent |
| **PR B (4759 core)** | `POST /zammad/webhook`, `zammad_service`, HMAC, dedup, customer-only + feedback-loop filters, **`{email}-{ticket}`** `user_id`, **ticket-type branching** (new ticket vs laptop-refresh follow-up), **reply gating** | Zammad trigger → dispatcher → RM → agent → MCP article on correct tickets only | Mock POST + Zammad E2E |
| **PR C (optional)** | Chat widget, seeding, Helm-only Zammad deploy polish | Demo UX | Manual |

**Do not** label PR B as complete until **§0** table rows are satisfied. **PR A alone** does not complete APPENG-4759.

---

### Phase 0: Zammad Instance Deployment — Prerequisite

**Goal:** Deploy a Zammad instance for local/dev testing of MCP and webhooks (or use external Zammad).

- [x] **`ticketingZammad` subchart** — Zammad ships as a dependency of the main chart (`helm/Chart.yaml`); installed with **`make helm-install-ticketing`** (uses `helm/values-ticketing.yaml` + Makefile overlay). No standalone `deploy-zammad` target.
- [x] **Remove Zammad** — Disable/uninstall via the main Helm release (or delete namespace); there is no separate `undeploy-zammad`.
- [x] **Overrides** — `helm/values-ticketing.yaml`, `helm/zammad/values.yaml`, and the temp overlay written by `helm-install-ticketing` (not `values-zammad-deploy.yaml`).
- [ ] **Manual:** Complete Zammad Web UI setup (admin user, org), create API token
- [ ] **Manual:** Set `zammad.url` and create Secret with `zammad.mcp.token` when enabling ticketing

**Zammad chart: autoWizard** — The official Zammad Helm chart supports `autoWizard.enabled` with a JSON config that can seed:
  - **Users** (admin: login, email, password, organization)
  - **Organizations**
  - **Settings** (e.g. product_name, system_online_service)
  - **Token** (for the autowizard URL itself; not the API token)

Example (`helm show values zammad/zammad`):
```yaml
autoWizard:
  enabled: false
  config: |
    {
      "Token": "secret_zammad_autowizard_token",
      "Users": [{"login": "admin@example.org", "firstname": "Admin", "lastname": "User", "email": "...", "organization": "Demo", "password": "..."}],
      "Organizations": [{"name": "Demo"}],
      "Settings": [{"name": "product_name", "value": "..."}]
    }
```

**API token:** Not configurable via autowizard. Token creation (Admin → Token Access → HTTP Token) is manual, or can be automated via Zammad API: `POST /api/v1/user_access_token` (requires auth). A post-install script/job could: sign in with admin creds from autowizard → create token via API → `kubectl create secret`. TBD whether to implement.

**Usage:** `make helm-install-ticketing NAMESPACE=my-namespace`. First bring-up of the Zammad stack often takes ~10–15 minutes (elasticsearch, postgresql, redis, memcached).

**Idempotency:** Re-running `helm-install-ticketing` / `helm upgrade` is idempotent. If Helm `--wait` times out, fix cluster capacity or increase `--timeout` and re-run; Zammad init jobs are typically safe to re-run.

### Phase 0.5: One-Shot Ticketing Deploy (helm-install-ticketing)

**Goal:** Single-command deploy for ticketing dev/demo—mirrors `helm-install-demo` (email + Greenmail) pattern.

- [x] Add `helm-install-ticketing` Makefile target
- [x] Target flow: create placeholder **zammad-credentials** secret → `helm upgrade --install` main chart with `-f helm/values-ticketing.yaml` (`ticketingZammad.enabled`, `mcp-servers.mcp-servers.zammad`, bootstrap Job for token + optional **integrationWebhook**)
- [x] Print follow-up checklist (URLs, FQDN, webhook HMAC, admin defaults)
  1. Get the Zammad URL (Route or port-forward)
  2. Complete initial setup at the Web UI (create admin, org, etc.)
  3. Create API token: Admin → Token Access → add HTTP Token
  4. Enable ticketing in quickstart: set `zammad.enabled=true`, `zammad.url`, create Secret with token
  5. (Optional) Configure webhook trigger (see Section 5.2)
- [x] Document in `make help` and HELM_EXPORT_ANSIBLE.md (or quickstart)

**Design / token chicken-and-egg:** Implemented **option (2) allow broken state** plus **automation**: autoWizard seeds admin; token creation runs via `kubectl exec` into `zammad-railsserver` (Ruby one-liner to call Zammad REST API). No external reachability required; always uses in-cluster exec.

### Phase 0 (legacy)

Superseded by §1.1 and §5 PR A/B split. Any merge to `dev` should match **APPENG-4759** scope or be explicitly labeled as prerequisite-only.

### Phase 1: Foundation (Zammad) — PR A — partial (not sufficient for 4759 alone)

**Dependency order:** shared-models (IntegrationType) → Alembic migration → request-manager, agent-service, helm.

- [x] Alembic migration: Add `ZAMMAD` to IntegrationType enum (`002_add_zammad_integration_type.py`)
- [x] shared-models: Add `ZAMMAD` to IntegrationType
- [x] request-manager: Add ZammadRequest schema
- [x] request-manager: Add ZAMMAD branch in CloudEvent handler
- [x] normalizer: `_normalize_zammad_request` with `session_id="zammad-{ticket_id}"`, `integration_context` — **4759 follow-up:** branch `target_agent_id` + ensure `user_id` from webhook is **`{email}-{ticket_number}`**
- [x] agent-service: `ticket-laptop-refresh-agent.yaml` includes **zammad** MCP (alongside snow) for ticket-channel replies
- [x] agent-service: Pass `target_agent_id` / `requires_routing` to session manager; specialist session when `requires_routing=False`
- [x] agent-service: `laptop-refresh` KB (existing); no separate generic ticket-resolution KB
- [x] helm: `mcp-servers.mcp-servers.zammad` + top-level `zammad` values (see §5.1)
- [ ] integration-dispatcher: `ZammadIntegrationHandler` posts customer-visible article via REST ([§4.2.2](#422-plan-zammad-customer-visible-reply-via-integration-dispatcher))
- [x] Unit test `test_normalize_zammad_request`

### Phase 2: Incoming Webhook + 4759 routing — PR B (closes APPENG-4759)

- [x] integration-dispatcher: `POST /zammad/webhook` route
- [x] integration-dispatcher: `zammad_service.py` (outbox / `send_request_event` per existing dispatcher patterns):
  - Parse Zammad trigger payload (`ticket`, `article`; see [ZAMMAD_TICKETING_CHANNEL_PLAN.md](ZAMMAD_TICKETING_CHANNEL_PLAN.md#technical-details))
  - Verify `X-Hub-Signature` (HMAC-SHA1 + `webhookSecret`); **401** if invalid
  - **Customer / external only**; skip internal notes and agent-created articles
  - **Feedback-loop:** skip when creator matches `zammad.aiAgentUserId`
  - **Group allowlist** via `zammad.allowedGroups` (empty = all)
  - **`user_id`:** **`{customer_email}-{ticket_number}`** (4759); resolve email from Zammad customer / article fields
  - **Ticket-type branching (4759):** new ticket vs follow-up on **agent-managed laptop refresh** may need **different webhook filters or metadata**; today both hit **request manager** → same normalizer → **`ticket-laptop-refresh`** until classification is implemented
  - **Reply gating:** agent/MCP reply only when ticket is **agent-managed laptop refresh**, not **waiting for human review** (implement via tag/state/group — document chosen rule in `APPENG-4759` or here)
  - Build `event_data` per §2.1; `cloudevent_sender.send_request_event()`
- [x] **Idempotency:** `X-Zammad-Delivery` required on webhook; `DatabaseUtils.try_claim_event_for_processing` + outbox idempotency key `zammad-delivery-{id}` (same pattern as Slack)
- [x] No-op `ZammadIntegrationHandler` (already done)
- [ ] **Deliverable (E2E):** Zammad trigger → dispatcher → RM → agent → MCP article in UI — verify on cluster with real trigger + `zammad.webhookSecret` set

### Phase 3: Chat Widget & Seeding — PR 3

- [ ] Zammad instance deployment (optional component or external; pattern: `make helm-install-ticketing` like `helm-install-demo`)
- [ ] AI agent user in Zammad, Agent role, availability config (or "Leave a message" mode)
- [ ] Chat widget config in Zammad Admin (Channels → Chat); embed script on target site
- [ ] **Seeding** (see below)
- [ ] **Deliverable:** End-to-end demo: user sends chat → agent replies via MCP → reply in chat

---

### 5.1 Helm Values Structure

```yaml
# helm/values.yaml additions
zammad:
  enabled: false
  url: "https://zammad.example.com"   # MCP needs url + /api/v1
  webhookSecret: ""                    # From K8s Secret; HMAC key for X-Hub-Signature
  allowedGroups: []                    # Empty = allow all; e.g. [1, 3] = only groups 1, 3
  aiAgentUserId: null                 # Zammad user ID of AI agent; required for feedback-loop filter
  mcp:
    enabled: true
    uri: "http://mcp-zammad:8000/mcp"
    # Env from Secret: ZAMMAD_URL (url + /api/v1), ZAMMAD_HTTP_TOKEN
```

**MCP block** (add to `mcp-servers.mcp-servers`):
```yaml
zammad:
  enabled: true
  replicas: 1
  image:
    repository: ghcr.io/basher83/zammad-mcp
    tag: latest
  env:
    ZAMMAD_URL: "{{ .Values.zammad.url }}/api/v1"
    ZAMMAD_HTTP_TOKEN: "..."  # From Secret
    MCP_TRANSPORT: "http"
```

---

### Seeding Checklist

All components must be seeded for a working demo. Pattern: init jobs, **`zammad-bootstrap`** Job, or `make helm-install-ticketing`-style deploys.

| Component | What to seed | Location / mechanism |
|-----------|--------------|------------------------|
| **Knowledge bases** | Laptop refresh KB | `agent-service/config/knowledge_bases/laptop-refresh/` (used by `ticket-laptop-refresh`) |
| **Zammad instance** | Zammad stack (postgres, redis, app) | **`ticketingZammad`** subchart (`helm install` via **`make helm-install-ticketing`**); or external/BYO Zammad |
| **Zammad MCP** | Deployed, connected to Zammad API | Helm `mcp-servers.mcp-servers.zammad` block; `ZAMMAD_URL`, `ZAMMAD_HTTP_TOKEN` from Secret |
| **AI agent user** | Zammad user for AI; Agent role | Init job or manual; **record user ID** and set `zammad.aiAgentUserId` in Helm values — required for feedback-loop filter |
| **Groups** | Sample support groups | Init job or Zammad Admin; add group IDs to `zammad.allowedGroups` if restricting |
| **Sample tickets** | 1–2 demo tickets | Optional; for demo/testing; no GH sync (Zammad is not git-based) |
| **Webhook trigger** | Zammad trigger → POST `/zammad/webhook` | **`zammad-bootstrap`** can create/update **Webhook** + **Trigger** via REST (§5.2.1). Enable **`ticketingZammad.bootstrap.integrationWebhook`** in **`helm/values-ticketing.yaml`** so the bootstrap Job gets the in-cluster dispatcher URL + **HMAC** (via **`zammad.webhookSecret`** → integration-secrets). Manual §5.2 remains valid for BYO Zammad / non-Helm installs. |

**KB content examples:** Printer locations, common error resolutions, "how to reset password" SOPs — enough for agent to demonstrate point-first and A-to-Z resolution.

**Zammad instance:** External Zammad — teams bring their own; quickstart adds webhook + MCP. Optional: deploy in-cluster via **`ticketingZammad`** (`make helm-install-ticketing`) for local/dev validation.

### 5.2 Zammad Webhook Trigger Configuration

In Zammad Admin (Manage → Triggers):

1. **Create trigger:** e.g. "Ticket Article Created (Customer)"
2. **Conditions:** Ticket → Article → Created; Article → Sender → Customer (or External)
3. **Perform:** Webhook → URL: `https://<integration-dispatcher>/zammad/webhook`; Method: POST
4. **Secret:** Generate and store in K8s Secret; set in trigger for X-Hub-Signature
5. **Exclude:** Add condition to skip when article created by AI agent user (or rely on backend filter via `aiAgentUserId`)

Alternative: "Ticket Created" trigger for new tickets; "Article Created" for follow-up messages. Both POST to same endpoint.

#### 5.2.1 Automated Webhook + Trigger (`zammad-bootstrap` / bootstrap Job)

**Implemented.** When `ZAMMAD_INTEGRATION_WEBHOOK_URL` is set (Helm `bootstrap.integrationWebhook.enabled=true`), the Job creates or updates:

| Piece | Role |
|-------|------|
| **Webhook** (`POST/PUT /api/v1/webhooks`) | Named `Self-Service Agent — Integration Webhook`; endpoint = dispatcher URL; `signature_token` = **`ZAMMAD_WEBHOOK_SECRET`** when provided (must match **`zammad.webhookSecret`** on the main chart so integration-dispatcher verifies `X-Hub-Signature`). |
| **Trigger** (`POST/PUT /api/v1/triggers`) | Named `Self-Service Agent — Customer article → blueprint`; **action** activator, **selective** execution; conditions **`article.action` is create** and **`article.sender_id` is Customer** (default sender id `2`, override with **`ZAMMAD_CUSTOMER_SENDER_ID`** if your DB differs); **perform** `notification.webhook` → webhook id. **`ticket.group_id`:** **`bootstrap.integrationWebhook.triggerGroupNames`** (comma-separated names — bootstrap resolves via API, e.g. **`Users`**) or **`triggerGroupIds`** (numeric ids; wins if both set). **`ZAMMAD_TRIGGER_SKIP_GROUP_FILTER=true`** omits group condition. **Tags:** **`ZAMMAD_TRIGGER_TAGS_ANY`** (`contains one`) **or** **`ZAMMAD_TRIGGER_TAGS_ALL`** (`contains all`) — not both (bootstrap exits if both set). Helm: **`triggerGroupNames`**, **`triggerGroupIds`**, **`triggerSkipGroupFilter`**, **`triggerTagsAny`**, **`triggerTagsAll`**. |

**Helm (`helm/zammad`):** `bootstrap.integrationWebhook` — default URL `http://<mainChartReleaseName>-integration-dispatcher.<namespace>.svc.cluster.local/zammad/webhook`; optional **`hmacSecretRef`** (default targets `$(MAIN_CHART_NAME)-integration-secrets` / `zammad-webhook-secret`, **optional** so installs without `zammad.webhookSecret` still run).

**`make helm-install-ticketing`** uses **`helm/values-ticketing.yaml`**, where **`ticketingZammad.bootstrap.integrationWebhook`** is enabled by default so the bootstrap Job receives dispatcher URL + **`hmacSecretRef`** (override there for non-default release names). Same webhook REST behavior as before; there is no separate **`deploy-zammad`** step.

**Ordering:** Works when the main chart is installed **before** Zammad (ticketing recipe order) so the in-cluster dispatcher hostname is stable. **Prod / BYO Zammad:** keep §5.2 manual trigger setup or replicate the same REST payloads with GitOps.

### 5.3 Chat Widget Setup (for full flow understanding)

Enable Zammad's built-in chat to see the end-to-end flow: user sends chat → webhook → agent → MCP adds article → reply appears in chat.

1. **Channels → Chat:** Zammad Admin → Channels → Chat → Add website
2. **AI agent availability:** Chat widget appears only when an agent is available. Options:
   - Create a dedicated AI agent user (Agent role); keep it "online" or use triggers to auto-assign
   - Or use "Leave a message" mode: messages become tickets when no agent is online; agent processes asynchronously
3. **Embed:** Copy the widget script; embed on a test page (or use Zammad's preview)
4. **Flow:** User sends message → Zammad creates ticket + article → webhook fires (if trigger configured) → agent runs → **Integration Dispatcher** posts **`content`** to the ticket (see [§4.2.2](#422-plan-zammad-customer-visible-reply-via-integration-dispatcher)) → reply appears in chat

Reference: [Zammad Chat docs](https://admin-docs.zammad.org/en/latest/channels/chat.html)

---

### Success Criteria (APPENG-4759)

- [ ] **Synthetic id:** Request events use **`user_id` = `{email}-{ticket_number}`** (4759)
- [ ] **New tickets** ingested into request manager with correct content and id
- [ ] **Agent-managed laptop refresh** tickets: follow-up customer articles ingested via **request manager** with correct **`user_id`** (owner vs author resolved per 4759)
- [ ] **Customer-visible** replies via **Integration Dispatcher** REST (canonical **`content`**); **no** **`zammad_add_article`** on the ticket-laptop-refresh path ([§4.2.2](#422-plan-zammad-customer-visible-reply-via-integration-dispatcher))
- [ ] Replies **only** when ticket is agent-managed laptop refresh, **not** when waiting for human review
- [ ] Webhook: **HMAC** verified; **customer/external** articles only; **no feedback loop**
- [ ] **Idempotency** for Zammad retries (`X-Zammad-Delivery`)
- [ ] Session continuity per ticket; multi-turn works
- [ ] Triggers documented (URL, secret, conditions) — see §5.2; optional bootstrap automation — §5.2.1

**Stretch (not 4759):** Chat widget demo; full `helm-install-ticketing` polish; eval flow `ticket_laptop_refresh` CI.

---

## 6. Security & Performance

### Security

| Item | Action |
|------|--------|
| Webhook signature | Verify X-Hub-Signature (HMAC-SHA1) with trigger secret; reject invalid; store secret in K8s Secret; never log |
| Group allowlist | Only process webhooks for tickets in configured groups (zammad.allowedGroups) |
| MCP token | Store ZAMMAD_HTTP_TOKEN in K8s Secret; never log or expose |
| Safety shields | Input/output shields on `ticket-laptop-refresh` agent (see Phase 1) |

### Performance

| Item | Action |
|------|--------|
| Idempotency | X-Zammad-Delivery for dedup; Zammad retries up to 4× on failure |
| Rate limiting | Rate-limit POST /zammad/webhook (middleware or ingress) |
| MCP monitoring | Monitor MCP call volume; back off on Zammad API errors |

---

## 7. Testing Strategy

| Phase | Test scope |
|-------|------------|
| **Phase 1** | Unit tests: ZammadRequest schema validation; `_normalize_zammad_request` output (`target_agent_id`, `requires_routing`, `session_id`); Request Manager ZAMMAD branch creates ZammadRequest; agent-service passes `target_agent_id` to session manager; session manager creates specialist session when `requires_routing=False` |
| **Phase 2** | Integration: POST to `/zammad/webhook` with mock payload; verify signature rejection (invalid HMAC → 401); verify event_data shape and `send_request_event` called; verify idempotency (duplicate X-Zammad-Delivery → skip); verify feedback-loop (article from aiAgentUserId → skip); verify group allowlist; **`ZammadIntegrationHandler`** posts article when §4.2.2 implemented (or mock REST in tests) |
| **Phase 3** | E2E or manual: Inject Zammad-like CloudEvent → agent processes → verify **dispatcher** created customer-visible article with expected **`content`** (not MCP **`zammad_add_article`**); chat widget: user sends message → reply appears |

---

## 8. Open Questions

| Question | Owner / Next Step |
|----------|-------------------|
| KB sync: static files only? | Yes — static `laptop-refresh` KB; no Zammad wiki sync |
| Escalation flow: trigger logic, state names, owner resolution | TBD; MCP supports it; flow details pending |
| Promptguards: Windows laptop approval authority | TBD |
| Zammad Time Event trigger for idle tickets | Later phase; Zammad trigger preferred over CronJob |

---

## 9. Risk Summary

| Risk | Mitigation |
|------|------------|
| Feedback loop (agent’s own articles) | Filter webhooks where article creator = AI agent’s Zammad user |
| Zammad MCP community-maintained | Review code; consider contributing; fallback: minimal MCP wrapper |
| Chat requires agent available | AI agent user + availability config; or “Leave a message” mode |
| Specialist session not created for Zammad | Implemented (§2.2); verify E2E when webhook lands |
| No customer-visible reply on ticket | Implement §4.2.2 dispatcher REST post + **remove** `zammad_add_article`; roll **integration-dispatcher** after Zammad secrets change |

---

## 10. Related Documents

- [APPENG-4759](../APPENG-4759) — ticket scope
- [Zammad Ticketing Channel Plan](ZAMMAD_TICKETING_CHANNEL_PLAN.md) — Technical reference (payloads, MCP tools)
