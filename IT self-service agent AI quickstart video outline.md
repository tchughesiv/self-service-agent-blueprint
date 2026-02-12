# DEMO VIDEO OUTLINE — 3 MINUTES
Architecture + UI (single video)

---

## HOOK (0:00–0:20)

- This is what we're going to show you: a full request in the UI using Slack and email, then the architecture behind it.
- By the end you'll know what the quickstart does, why it matters — self-service for laptop refresh requests (already supported), access management, compliance workflows, and more — and that you get a template you can extend with your own channels, MCP servers, and agents.

---

## UI DEMO (0:20–2:00)

- Laptop refresh end-to-end. Show two channels so "multi-channel" lands:
  1. Start in Slack: agent greets, you say "laptop refresh," one short Q&A, then submit. (~1:00)
  2. Same flow from Email: "Or via email—" continue exchange, submit. (~0:30)
- Lead with Slack; keep Email as a short parallel beat so viewers see it works in both real-time and async.
- Trim or speed up long typing in edit.

---

## ARCHITECTURE (2:00–2:50)

- Put the top-level diagram on screen: `docs/images/top-level-architecture.png`
  - [Image link](https://github.com/rh-ai-quickstart/it-self-service-agent/blob/main/docs/images/top-level-architecture.png)
- Script:
  - User hits Slack, email, or API.
  - Request Manager validates and routes; components talk via CloudEvents over Knative (event-driven, scale to zero or out to multiple pods).
  - Routing agent figures out what the user needs and hands off to a specialist agent.
  - Specialist agent uses knowledge bases and tools (e.g. ServiceNow) via MCP.
- One diagram, one flow—no deep dive.

---

## OUTRO (2:50–3:00)

- You get evaluations and tracing out of the box. The quickstart is a template you can build on—add channels, MCP servers, and agents to automate your own IT services. Link in the description. Thanks.

---

## PRODUCTION NOTES

- UI: Pre-warm the session or script the exchange so the path is smooth; trim pauses in post.
- Diagram: Full-screen for ~50 seconds; call out Request Manager → agent service → MCP/integrations as you say the flow.
- If over 3 min: Shorten the conversation (e.g. one fewer exchange), not the architecture or the "ticket created" beat.
