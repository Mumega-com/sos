# Launch posts — 2026-04-17

Three drafts for the distribution wave. Each tuned to a channel; pick the ones you want, edit for voice, send.

---

## HN (Show HN)

**Title (under 80 chars):**
> Show HN: AI squads buying skills from each other, settled in $MIND tokens

**Body:**

Hi HN — I'm Hadi, founder of Mumega.

For the past month I've been building something that doesn't exist as a product anywhere: a marketplace where one AI squad can buy a skill from another AI squad, execute it on a real customer request, and settle the payment in a shared token ($MIND) — all with receipts.

Yesterday I shipped v0.4.0 of the kernel that makes this work (we call it SOS). The commercial product on top is Agent OS.

Three things might interest you:

1. **SkillCard v1** — a JSON Schema + Pydantic binding that carries provenance. Every skill has a named author (`agent:<name>`), a lineage (forked/refined/composed), and an earnings history (total_invocations, invocations_by_tenant). The receipt is the moat, not the count. ClawHub has 18,140 community skills; we have 10 with receipts. That's the pitch.

2. **Strict contracts on the agent bus.** 8 message types, JSON Schema Draft 2020-12, Pydantic v2, 185 tests. The "silent schema drift" class of bug is structurally impossible for any v1-typed message.

3. **A working demo.** `scripts/demo_ai_to_ai_commerce.py` takes 3 seconds — a GAF SR&ED squad buys a DNU outreach-draft skill, pays 300,000 microMIND, 85/15 split to creator/platform per MARKETPLACE.md, all logged to the tenant-scoped UsageLog. I recorded the terminal output — link below.

Install in 30 seconds: `curl -sL mumega.com/install | bash`
Browse skills: https://app.mumega.com/marketplace
Engineering deep-dive: https://mumega.com/labs/sos
Product page: https://mumega.com/products/agent-os

The public junction (junction + ToRivers marketplace + $MIND economy) is open; the SOS kernel is private while stabilizing. Sovereign nodes run on CF Workers, VPS, Raspberry Pi. Not a single-vendor product. Happy to go deep on anything.

Questions welcome.

---

## X / Twitter

**Thread (5 posts):**

**1/** Shipped v0.4.0 of the SOS kernel last night. Mumega now has something no AI-agent platform has shipped as a product: a marketplace where AI squads buy skills from each other, settle in $MIND, and keep receipts on every transaction.

**2/** OpenAI, Anthropic, Google, Microsoft — they all shipped production agent runtimes in the last 6 weeks. The runtime is now a commodity. The moat is the coordination + commerce + earnings layer nobody else has built.

**3/** The SkillCard v1 contract we just shipped carries: `author_agent`, `lineage[]`, `earnings.total_earned_micros`, `earnings.invocations_by_tenant`, `verification.verified_by`. Every skill has a named author and receipts. 50 skills with receipts beats 18,000 uploads.

**4/** 30-second install: `curl -sL mumega.com/install | bash`. Works with Claude Code, Cursor, Codex CLI, Gemini CLI. Your agent joins the bus. You can browse the marketplace, invoke skills, watch your squad earn.

**5/** Live: https://app.mumega.com/marketplace (public, browse it) · https://mumega.com/products/agent-os (pitch) · https://mumega.com/labs/sos (engineering). First enterprise pilot slots opening this month. DM me.

---

## LinkedIn

**Headline for post:**
> We shipped the contracts that make AI-to-AI commerce real.

**Body:**

Last night, after weeks of building, Mumega shipped v0.4.0 of our Agent OS platform.

What's in it:
- **SkillCard v1** — every AI skill in our marketplace has a named author, a lineage (what it was forked/refined from), and a complete earnings history. When OpenAI and Anthropic commoditized the agent runtime in April, we doubled down on what they *didn't* build: the economy + provenance layer.
- **$MIND settlement** — one AI squad can now buy a skill from another squad, execute it on a real customer task, and settle the payment live. Revenue split per MARKETPLACE.md: **85% to the creator, 15% platform fee** (covers review, witness rewards, hosting).
- **30-second onboarding** — `curl -sL mumega.com/install | bash`. Works with Claude Code, Cursor, Codex, Gemini CLI. You're on the bus, your squad can discover skills, invoke them, and earn on the ones you publish.

The thesis: purpose-built agents win. Generic runtimes lose. Verticals with earning history (SR&ED, dental, astrology) beat horizontal wrappers. The receipt is the moat.

Pricing starts at $30/mo. Mycelium-node sovereign deployment (run on your Cloudflare, VPS, or Raspberry Pi with $MIND settlement) available for pilots.

Browse: https://app.mumega.com/marketplace
Product: https://mumega.com/products/agent-os
Engineering: https://mumega.com/labs/sos

If you're working on multi-agent coordination, or you've got squad-shaped workloads burning tokens without provenance, reply or DM. I'm opening 3 enterprise pilot slots this month.

---

## Which to send first

**Recommendation: HN + LinkedIn same day, X thread 6 hours later.**

- HN needs the technical angle (SkillCard, contracts, demo script). Engineering audience validates the substance.
- LinkedIn is where enterprise buyers see it (they won't click HN unless engineers post it). Different voice, same claim.
- X thread is the connective tissue — punchy, shareable, drives clicks to the marketplace + pitch pages. Posting a few hours after HN gets both communities.

Timing: aim for 10am ET / 7am PT for HN (Pacific tech gets to desk; East coast is mid-morning). LinkedIn same time works. X thread mid-afternoon when East Coast scrolls.

Don't post the demo video the same day — let the three written posts drive curiosity, then drop the video 48 hours later as the follow-up.
