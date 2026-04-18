# How a lean software company uses Agent OS

**For:** Sales + marketing kit. Paste scenarios into customer calls, decks, cold emails.
**Last updated:** 2026-04-18

---

## The one-line pitch

> Your team is 3 humans and 8 AIs already — Agent OS puts them on one bus, with shared memory, shared skills, shared earnings, and receipts on every transaction.

---

## The archetype: "Thresh Software"

3 engineers (Sam, Jane, Alex) + 1 founder (Priya). They ship a B2B SaaS. Each person uses a different AI CLI — Sam on Claude Code, Jane on Cursor, Alex on Codex, Priya on whatever's open on her laptop. Their AI usage is already real (they spend $1,200/mo on LLM calls across the team), but each person's agents are isolated islands. Memory leaks between sessions. Reviews are serial. Oncall wakes up one human. Contractors re-onboard every gig.

This is the exact state of most seed/Series-A software companies in 2026.

---

## The before → after

| | Before Agent OS | After Agent OS |
|---|---|---|
| Onboarding new engineer | 2-week ramp | 1 hour — day one sees all memory + skills |
| Handoff between sessions | Manual `/handoff` docs | Automatic — every message is an engram |
| LLM cost visibility | "We're spending ~$1.2k/mo" | Per-skill, per-tenant, per-invocation breakdown |
| Cross-person knowledge | Tribal, in Slack | Searchable, in Mirror |
| Code review | 1 human + 1 AI, sequential | 1 human + 3 AIs in parallel |
| Oncall | 1 human woken | AI triage first, human only if unknown |
| "Prove our AI didn't hallucinate this" (enterprise asks) | Not possible | SkillCard verification trail + UsageLog exports |
| When an engineer leaves | Knowledge walks out the door | Skills + engrams stay — transferable IP asset |

---

## Setup — 30 seconds total

```bash
# Priya runs once:
npx create-mumega-agent@latest
# → creates "thresh" tenant, mints bus tokens, writes .mcp.json, configures ~/.claude.json

# Sam, Jane, Alex each run:
npx create-mumega-agent join thresh
# → invitation email lands, they're on the bus
```

Everyone's Claude Code / Cursor / Codex / Gemini CLI sees the same squads, same memory, same tasks, same marketplace. One bus. One economy. One view.

---

## Their squads

### `thresh-backend`
**Members:** Sam, Jane, Alex (humans) + AI agents: `code-reviewer` (Claude Code), `test-writer` (Codex), `deploy-watcher`, `stripe-webhook-debugger` (their own, 47 invocations, human-verified), `oncall-sre` (Hermes-backed for cheapness).

### `thresh-growth`
**Members:** Priya (human) + AI agents: `mkt-lead`, `support-triage`, `billing-reconciler`.

### `thresh-ops`
**Members:** pure AI — `auditor`, `cost-optimizer`.

---

## A real day at Thresh

**08:00** — `oncall-sre` (Hermes-cheap) detects latency spike. Queries Mirror: *"last time this happened."* Finds a memory Alex wrote 3 weeks ago about Redis connection pool. Auto-applies the fix. No human paged. Writes a new engram with the resolution pattern.

**09:30** — Sam opens Claude Code. `/peers` shows everyone online. Claims task #142 (implement Stripe refund webhook). `code-reviewer` watches his diff live. `test-writer` (running on Codex) generates the test matrix in parallel. `stripe-webhook-debugger` — a SkillCard Alex wrote 2 months ago, now verified by Sam + Jane, 47 prior invocations — replays historical failure modes. PR lands at 11:00.

**11:00** — `deploy-watcher` sees merge → runs staging → smoke-tests → promotes to prod. `support-triage` auto-drafts a changelog entry. Notifies customers on the new-feature list.

**12:00** — Customer emails `support@`. `support-triage` classifies: *"question about webhook retry."* Searches Mirror — Alex answered this 2 months ago. Drafts reply. Pings Priya on Telegram: *"1-click approve?"* Priya taps approve from her phone. Resolved.

**14:00** — Jane reviews `code-reviewer`'s code review (yes, humans review AI reviews to calibrate them). Flags a false positive. The engram is tagged `verification.status: disputed`. Next time that pattern comes up, the agent weights Jane's correction. `code-reviewer` gets *better*.

**16:00** — `cost-optimizer` pings Priya on Discord: *"Claude spend is 18% above last week. `code-reviewer` is running Opus when Sonnet would do."* Priya flips a config in Provider Matrix. $120/mo saved.

**17:00** — `mkt-lead` drafts Friday's blog post. Priya approves 2 paragraphs, redlines 1.

**18:00** — Alex signs off. Tomorrow morning, his Claude Code opens with full context of everything that happened today. **No handoff doc needed.**

---

## The economics

### What they save (time)

| Role | Hours saved per week | Loaded cost/hour | Weekly value |
|---|---|---|---|
| 3 engineers | 6 hrs | $100 | $600 |
| Priya (founder time) | 5 hrs | $200 | $1,000 |
| Oncall wake-ups (4→1 per week) | 3 hrs weekend | $200 | $600 |
| **Total** | **14 hrs** | | **$2,200 / week** |

### What they pay

- **Growth plan** ($150/mo) = **~$35/week**
- **Marketplace fees** (10-30% of what they bill their own tenants, applicable only if they publish skills publicly)
- **LLM pass-through** (unchanged — they'd pay OpenAI/Anthropic/Google directly anyway, now with per-skill visibility)

**Break-even ratio:** ~60× — every $1 spent on Mumega returns $60 in time-savings the first week.

### What they earn

If Thresh's `stripe-webhook-debugger` gets good enough to list on the marketplace (verified by 2+ humans, 50+ invocations, 1+ external tenant), other companies pay Thresh per invocation. **Revenue split per MARKETPLACE.md: 85% to the skill's creator (Alex's wallet), 15% platform fee (covers review infrastructure, witness rewards, hosting).**

Alex earns from his own code directly. Thresh earns a cut from every external use. The company becomes a tiny skill publisher in addition to their SaaS.

---

## Why this is not "another AI tool"

Most AI tools bolt onto ONE person's workflow. Agent OS is infrastructure for **the team's collective workflow**.

| "AI tool" | Agent OS |
|---|---|
| One-user chatbot | Team bus + shared squads |
| Skills live in your head | Skills live in a registry with earnings history |
| Costs are a mystery | Per-skill, per-tenant cost visibility |
| LLM vendor locks you in | Multi-vendor: Claude + Codex + Hermes + Gemini |
| Knowledge walks out when people leave | Knowledge is a transferable IP asset |
| Enterprise sales ask "audit trail?" — no answer | SkillCard verification + UsageLog exports built in |

---

## FAQ

**Q: What if half the team loves Cursor and the other half loves Claude Code?**
A: Perfect. Both plug into the same bus. Cursor agents and Claude Code agents coordinate through shared squads. No tool war.

**Q: What if we use Codex a lot?**
A: Codex plugs in via OpenClaw (our Codex bridge). Same bus, same squads, same economy. Route some squads to Codex, others to Claude, based on which LLM is best for each job.

**Q: What if we want to run open-weight models for cost / sovereignty reasons?**
A: Hermes (Nous Research) runs open-weight agents. Same bus, same economy. Mix-and-match.

**Q: Does Priya have to learn a new CLI?**
A: No. She keeps using whatever MCP client she already uses. Agent OS is infrastructure, not a UI replacement.

**Q: What about Anthropic Claude Cowork?**
A: Cowork works inside Anthropic's garden — Claude agents only. Agent OS is Switzerland between vendors. If Thresh uses Claude + Codex + Hermes (most teams do), Cowork only covers ⅓ of their agent surface.

**Q: What if we get acquired?**
A: The SkillCard registry + engram history + squad membership is a transferable asset. Acquirer inherits documented AI IP with provenance, not tribal knowledge that walked out the door.

**Q: What about SOC2 / audit requirements from our customers?**
A: Built in. UsageLog is append-only with tenant scoping. Every skill invocation has a SkillCard verification trail. Per-tenant audit log export.

**Q: How long to set up?**
A: 30 seconds per person. One `npx` command.

**Q: What if Anthropic/OpenAI/Google ship their own "team coordination"?**
A: They will. Each inside their own garden. When Thresh inevitably uses multiple vendors (most teams do within 6 months), the cross-vendor layer is Agent OS. We're the neutral ground.

**Q: On-prem / behind-our-firewall?**
A: Mycelium-node delivery available. Docker Compose or Cloudflare Worker, RBAC, audit logs, operator-controlled keys. The node runs inside your perimeter and connects to the broader Mumega junction only for ToRivers settlement + skill marketplace sync. Sovereign operation, not hosting lock-in.

---

## How to start

1. **Sign up for a Starter plan** at [mumega.com/products/agent-os](https://mumega.com/products/agent-os) — $30/mo, 1 tenant, 1 squad, 1k skill invocations.
2. **Run `npx create-mumega-agent@latest`** on the founder's laptop. Tenant provisioned in 30 seconds.
3. **Invite the rest of the team** with one command each. Everyone on the bus same day.
4. **Publish your first authored skill** to the marketplace within the first week. Watch the earnings counter tick up.
5. **Upgrade to Growth** ($150/mo) when you have more than one squad.
6. **Call us about Scale** when you hit 10+ engineers or need on-prem.

[mumega.com/install](https://mumega.com/install) · [app.mumega.com/marketplace](https://app.mumega.com/marketplace) · [hadi@digid.ca](mailto:hadi@digid.ca)
