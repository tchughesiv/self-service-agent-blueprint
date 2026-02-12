# IT Self-Service Agent Quickstart — Demo Script (~3 min)

**Purpose:** Recording script for the architecture + UI demo video.  
**Reference:** [IT self-service agent AI quickstart video outline.md](IT%20self-service%20agent%20AI%20quickstart%20video%20outline.md)  
**Diagram:** `docs/images/top-level-architecture.png`

---

## HOOK (0:00–0:20)

**[On screen: title or splash]**

"In this demo we'll show you the **IT self-service agent AI quickstart**—with a laptop refresh workflow you can run out of the box.

We'll show you how a conversation can start in Slack and continue in email—same session, across channels, without losing context—then the architecture that keeps it continuous. With this quickstart you get a template you can extend with your own channels, MCP servers, and agents."


---

## UI DEMO (0:20–2:00)

**One continuous conversation: start in Slack, continue in email. Use the same user (same email) in both so the Request Manager keeps one session.**

**Speaker notes (glance as you go):** *Say* "We'll do a laptop refresh in one conversation—starting in Slack." → *Type* hi / laptop refresh → *Type* see options → *Say* "Same conversation—I'll continue in email." → *Switch to email* → *Type* option 1 → *Say* "Same conversation—I'll finish in Slack." → *Switch to Slack* → *Type* yes create ticket → *Say* "One session—started in Slack, continued in email, finished in Slack. Conversations survive channel switches." → *Say* "And here's that request in ServiceNow—conversation turned into action." → *Say* "Now let's take a look at the architecture."

**Speaker note (optional—when switching to email or at continuity callout):** The system looks up your email from Slack and uses it as your identity—so the same person in Slack and email is one session. Your data (e.g. current laptop, eligibility) comes from ServiceNow; that same email is what ServiceNow uses for the ticket when we create it.

### Part 1 — Start in Slack (~1:00)

**[Screen: Slack DM or channel with the Self-Service Agent bot]**

- "We’ll do a laptop refresh in one conversation—starting in Slack."
- **You (type/say):** `Hi` or `I need help with my laptop refresh`
- **Expected:** Agent greets and asks what you’d like to do, or goes straight to laptop refresh.
- **You:** `I would like to refresh my laptop` or `laptop refresh`
- **Expected:** Agent retrieves your laptop info, checks eligibility (3-year policy), gives a short summary.
- **You:** `I would like to see available laptop options`
- **Expected:** Agent lists 4 options for your region (e.g. NA: MacBook Air M3, MacBook Pro 14 M3 Pro, ThinkPad T14 Gen 5, ThinkPad P1 Gen 7) with specs and price.

**[Transition — same conversation, different channel]**

- "Same conversation—I'll continue in email." (Switch to email client; use the same user's email so the session is continuous.)

### Part 2 — Continue in Email (~0:20)

**[Screen: Email client or test webmail UI — same user as Slack]**

- **You:** Reply to the agent (or send to agent address, same user): e.g. "I'd like option 1, the Apple MacBook Air M3."
- **Expected:** Agent replies by email confirming selection and asking to create the ServiceNow ticket.

**Part 3 — Finish back in Slack (~0:15)**

**[Screen: Slack — same DM/thread as Part 1]**

- "Same conversation—I'll finish in Slack." Switch back to Slack.
- **You:** In the same thread: "Yes, please create the ticket."
- **Expected:** Agent replies in Slack: "A ServiceNow ticket for a laptop refresh has been created for you. The ticket number is REQ…"
- Call out briefly: one session—started in Slack, continued in email, finished in Slack. Conversations survive channel switches.

**Show request persisted in ServiceNow (~10–15 s)**

**[Screen: ServiceNow UI — Requests / requested item]**

- "And here's that request in ServiceNow—conversation turned into action." Show the ticket (e.g. All → Requests; open the REQ number from the conversation; optionally show requested item and details so user/laptop selection is visible).
- Keep it short: same ticket number, correct user and laptop—request persisted end-to-end.

**[Transition to architecture]**

- "Now let's take a look at the architecture."

---

## ARCHITECTURE (2:00–2:50)

**[Full-screen: `docs/images/top-level-architecture.png`]**

**Script (one diagram, one flow; no deep dive):**

- "Users reach the system through **Slack**, **email**, or the **API**—including the CLI."
- "The **Request Manager** receives from those channels—the Integration Dispatcher is part of that for Slack and email—validates and routes every request, ties it to the same user session so switching channels keeps one conversation, and delivers responses back to the configured channels. This is deployed to **OpenShift** using elements of **OpenShift AI**—components talk using **CloudEvents** over **Knative** with **Kafka**—which makes it event-driven and **scalable**."
- "The **routing agent** figures out what the user needs and hands off to a **specialist agent**—here, the laptop refresh agent. That agent uses **knowledge bases** and **tools** like ServiceNow via **MCP**, powered by an **LLM**."
- "The quickstart is built to be **easily extensible**. Add more channels (e.g. Discord, Teams, or SMS), MCP servers for new integrations, more IT services (e.g. access management, compliance workflows), or new specialist agents— all without changing the core platform."

**[If time]** Briefly point on diagram: channels → Request Manager → Router Agent → Agent(s) → Tools / IT Systems.

---

## OUTRO (2:50–3:00)

"One conversation across all channels, ready to extend. Link's in the description. Thanks."

*Alternative:* "You get **evaluations** and **tracing** out of the box. Try the quickstart—link's in the description. Thanks."

---

## PRODUCTION NOTES (for recording)

**Timing (pre-warmed):** With everything pre-installed and pre-warmed, expect **~3:00–3:20**. Rough breakdown: Hook ~20 s | Slack (Part 1) ~50–60 s | transition + Email (Part 2) ~25 s | Slack (Part 3) ~20 s | ServiceNow ~12 s | transition ~5 s | Architecture ~45–50 s | Outro ~10 s. If you run over, shorten one Slack exchange (e.g. go straight to "laptop refresh" without "Hi"); keep the ticket-created and ServiceNow beats.

- **UI:** Pre-warm the session or script the exchange so the path is smooth; trim pauses in post. Use the same user (same email) in Slack and in email so the Request Manager keeps one session across channels. Have ServiceNow open (or a second tab) so you can cut to the persisted request right after the agent confirms the ticket in Slack (Part 3); note the REQ number from that Slack message to look it up. (Avoid showing the OpenShift console in the recording if you want the demo to age well—the UI changes with releases; "deployed to OpenShift" in the hook is enough.)
- **Diagram:** Show `docs/images/top-level-architecture.png` full-screen for ~50 seconds; call out **Request Manager → agent service → MCP/integrations** as you say the flow (Integration Dispatcher is part of Request Manager arch, not a separate box on the diagram).
- **If over 3 min:** Shorten the conversation (e.g. one fewer exchange), not the architecture or the "ticket created" beat.

---

## QUICK REFERENCE — Exact flows (from README)

**One continuous flow (same user in Slack and email):**

**Slack (start):**
1. DM bot: `hi` → routing agent asks what you’d like to do.
2. `I would like to refresh my laptop` → specialist: laptop info + eligibility.
3. `I would like to see available laptop options` → agent shows 4 options.
4. *Switch to email (same user).* **Email (continue):** "I'd like option 1, the Apple MacBook Air M3" → agent confirms by email and asks to create ticket.
5. *Switch back to Slack.* **Slack (finish):** "Yes, please create the ticket" → agent replies in Slack: ticket created, REQ number.

**CLI (if you need to pre-warm, test, or wake ServiceNow):**
```bash
# Wake hibernating ServiceNow PDI (set SERVICENOW_DEV_PORTAL_USERNAME and SERVICENOW_DEV_PORTAL_PASSWORD first)
make servicenow-wake

# Interactive chat (pre-warm or test)
oc exec -it deploy/self-service-agent-request-manager -n $NAMESPACE -- \
  python test/chat-responses-request-mgr.py --user-id tchughesiv@gmail.com
```
Use `reset` then any message to start a fresh conversation.

---

## REPO SOURCES USED

- **Outline:** `IT self-service agent AI quickstart video outline.md`
- **Blog (language alignment):** [AI meets you where you are: Slack, email & ServiceNow](https://developers.redhat.com/articles/2026/02/09/self-service-ai-agent-slack-email-servicenow) — script uses "without losing context," "conversations survive channel switches," "conversation turned into action"; optional hook phrase: "meet users where they already work."
- **Architecture:** `README.md` (Key Request Flow, Architecture diagrams), `docs/ARCHITECTURE_DIAGRAMS.md`
- **Diagram:** `docs/images/top-level-architecture.png`
- **Flows:** `README.md` (Interact with the CLI, Integration with Slack, Integration with email)
- **Laptop options:** `agent-service/config/knowledge_bases/laptop-refresh/NA_laptop_offerings.txt`
- **Evaluations/tracing:** `README.md` (Run evaluations, Follow the flow with tracing), `guides/EVALUATIONS_GUIDE.md`
- **Channels:** `guides/SLACK_SETUP.md`, `guides/EMAIL_SETUP.md`
- **Helm/deploy:** `helm/templates/NOTES.txt`, `Makefile` (e.g. `helm-install-test`)

---

## AUDIT (technical accuracy)

Verified against repo/code:

- **Session continuity:** User identity is canonical by email (Slack resolves user to email; email channel uses sender). Same email in Slack and email → same `canonical_user_id` → same session (create_or_get_session_shared in request-manager; session in DB). Wording "Request Manager keeps one session" is consistent with README ("Request Manager retains conversation state").
- **Channels:** Script lists Slack, email, API (CLI uses API). Matches supported entry points; IntegrationType also includes WEB, WEBHOOK, TEST, TEAMS, etc. for extensibility.
- **Architecture:** Request Manager (Integration Dispatcher noted as part of it for Slack/email) receives, validates, routes, session, delivers back; CloudEvents over Knative and Kafka; routing agent → specialist agent; knowledge bases + MCP (ServiceNow), LLM-powered. Aligns with diagram (Request Manager as single box) and `docs/ARCHITECTURE_DIAGRAMS.md` (Flow 1).
- **Laptop refresh:** 3-year policy and 4 NA options (MacBook Air M3, MacBook Pro 14 M3 Pro, ThinkPad T14 Gen 5, ThinkPad P1 Gen 7) match `agent-service/config/knowledge_bases/laptop-refresh/` and README.
- **Ticket format:** REQ prefix for ServiceNow requests (evaluations and MCP expect REQ/INC/RITM).
- **CLI:** `python test/chat-responses-request-mgr.py --user-id tchughesiv@gmail.com` correct; `tchughesiv@gmail.com` is in mock-employee-data; `make servicenow-wake` exists (Makefile).
- **Diagram:** `docs/images/top-level-architecture.png` exists; script flow (channels → Request Manager → agent service → MCP/integrations) matches diagram; Integration Dispatcher is noted in script as part of Request Manager (not a separate box on diagram).
