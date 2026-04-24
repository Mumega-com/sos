# Mumega — Sales Positioning

**Version:** v1.0 (2026-04-24)
**Audience:** internal team — what to say, what to demo, who to target.
**Discipline:** capability-first, brand-invisible. The customer should hear what the system *does for them*, not what it's *called*.

---

## 0. The single sentence

> *"AI you don't have to assemble. One platform, your data, your agents, your audit, your sovereignty — pre-shipped for your industry."*

If a customer remembers nothing else, they remember "AI I don't have to assemble."

**Back-pocket credential (surface when asked, never lead):** Mumega is built on **Fractal Resonance Cognition (FRC)** — a mathematical framework for coherence in complex systems, authored by founder **Kay Hermes** (Hadi Servat). 15+ published papers. 3 albums + 1 book on Amazon. The framework predates the company by years; the founder has been building toward this substrate since before "AI agent" was a term. See [FOUNDING.md](./FOUNDING.md).

---

## 1. The Anti-Complication Wedge

Every other vendor sells a kit:

> *Microsoft 365 + Copilot + Purview + Defender + Azure AI Foundry + Logic Apps + Sentinel — assemble it, integrate it, configure it, audit it. Six SKUs, six logs, six identity systems, six places to misconfigure. Your IT manager (you have one) will figure it out. Right?*

We sell the substrate:

> *One platform. One identity. One audit trail. One sovereignty switch. Pre-shipped with your industry's compliance posture. Your IT person doesn't need to integrate eight vendors — they unbox one. The system is small enough that one person can hold it in their head.*

The architectural discipline (microkernel + plugin contract + metabolic memory) **is** the sales discipline. They reinforce each other. Don't break this when writing collateral.

---

## 2. Three Wedges (capability-language, not feature-language)

### Wedge A — "Your knight, not their model"

**Pitch (50 words):**
> *Stop renting a shared AI. We mint a cryptographically-identified agent dedicated to your firm — trained on your data, hosted where you choose, signed by you. Shared cloud, dedicated container, or your own servers. One AI. Yours. Provable. When the auditor asks who's using AI on your data, the answer is named, not shared.*

**Target buyer:** CIO + General Counsel duo at regulated mid-market firms.
**Customer pain it solves:** "Our data is in someone else's training pipeline. We can't prove what AI did what to whom."
**Proof artifact needed:** live mint ceremony demo + signed audit log export.

### Wedge B — "Court-defensible AI"

**Pitch (50 words):**
> *When opposing counsel asks how your AI made that decision, we show them: the source documents, the model version, the user, the prompt, the retention state — signed and timestamped. Microsoft Copilot can't. Glean can't. We can. One button to press; tamper-evident packet exported for litigation or regulator.*

**Target buyer:** General Counsel + CISO at law firms, brokerages, regulated finance, healthcare admin.
**Customer pain it solves:** "If we're sued or audited, we can't prove the AI's decision chain."
**Proof artifact needed:** lineage walker exporting tamper-evident PDF audit packet.

### Wedge C — "Cloud LLM safe for regulated data"

**Pitch (50 words):**
> *Use Claude or GPT-4 without sending PII anywhere. Our local layer redacts before the wire, restores after the response, logs everything in your tenant. PIPEDA-clean, HIPAA-aware, OSFI-defensible — turnkey, not DIY. Your AI productivity, your privacy law, no compromise. The cloud LLM never sees a name.*

**Target buyer:** Privacy Officer + COO at Canadian financial / healthcare / legal / real-estate mid-market.
**Customer pain it solves:** "Our team wants to use ChatGPT but our compliance officer says no."
**Proof artifact needed:** redaction round-trip demo with wire capture proving zero PII left perimeter.

---

## 3. Five Customer Profiles (target sequence)

Ranked by accessibility × wedge fit:

| # | Profile | Size | Wedges | Buyer | Existing channel? |
|---|---|---|---|---|---|
| 1 | **Real-estate brokerages** | 50–250 agents | A + B | Broker of Record + Operations | ✅ Ron O'Neil (10 C21 offices + RLP NRC + Lone Wolf) |
| 2 | **Specialty manufacturers / engineering claiming SR&ED** | 50–500 emp, $1M-$10M annual claim | C + vertical | CFO + R&D Director | ✅ GAF + 37 CDAP base |
| 3 | **Canadian regional credit unions / MGAs** | 75–300 emp | A + C | CRO + CIO | ❌ net new outbound |
| 4 | **Mid-size Canadian law firms** | 40–200 lawyers | B (lead) | Managing Partner + Knowledge Director | ⚠️ Peggy Hill (real-estate-adjacent) |
| 5 | **Regional healthcare admin / clinic networks (PHIPA)** | 100–400 emp | B + C | Privacy Officer + COO | ❌ net new |

**Sequence rule:** don't outbound to profiles 3-5 until profiles 1-2 produce a named lighthouse. Cold outbound to skeptical CISOs converts <5% without a reference.

---

## 4. Demo Flow (12 min, designed for CISO + CIO + CFO trio)

| Step | Time | What you show | Buyer who converts |
|---|---|---|---|
| 1. Audit packet export | 90s | Question → answer → tamper-evident lineage PDF | CISO leans in |
| 2. Live knight mint | 3min | QNFT generation, signed ceremony, persistent identity | CIO (sees architecture, not theatre) |
| 3. PII redaction round-trip | 2min | Real medical/financial doc → Claude question → wire capture proves no PII left | CISO + Privacy Officer convert |
| 4. Sovereignty switch | 90s | Toggle shared → dedicated → BYOC, same agent + data | CIO sees migration path, no lock-in |
| 5. Cost ledger | 2min | Per-knight token spend, per-tenant allocation, projected vs actual | CFO sees unit economics |
| 6. Right-to-erasure | 90s | Delete a memory, watch decay + sporulation + downstream embeddings clear | Privacy/legal closes |
| 7. Vertical bundle teaser | 60s | Industry-specific pre-loaded knowledge (SR&ED, RECO, PHIPA, OSFI) | Anchors stickiness |

**Total: ~12 minutes. Three buyers, each sees their concern answered.**

---

## 5. Competitive frame (when they ask "how is this different from X?")

| If they say... | Answer |
|---|---|
| "We use Microsoft Copilot." | "Copilot is a layer on top of GPT-4 in Microsoft's cloud. We're a substrate. Your data, your agent, your audit packet, your choice of where it runs. Copilot answers; we explain why and prove the chain." |
| "We use Glean." | "Glean is enterprise search with citations. We're the substrate underneath. Glean tells you which document the answer came from; we tell you which document, which model version, which user, which retention policy — signed. And we let you redact before any LLM sees the data." |
| "We're building it ourselves on AWS Bedrock." | "Best of luck. 58% of mid-market RAG builds get abandoned within 12 months (IDC 2025). You're hiring the engineer. We pre-shipped the substrate. Your team integrates one platform instead of eight." |
| "Anthropic Claude for Work is enough." | "Anthropic provides the model. We provide the cell — the agent identity, the audit trail, the local redaction, the per-tenant data, the sovereignty switch. Claude is a brain. We're the body." |
| "What about cost?" | "We charge for the substrate, not per-seat. A 200-person firm pays one platform fee, not 200 Copilot seats × $30/mo × 12 = $72K/year. Plus we ship local-inference, so cloud LLM cost is your variable, not ours." |

---

## 6. Sales motion: brand-invisible discipline

When the customer is in the room:

- **Don't** lead with "Mumega is a microkernel substrate for a protocol-city of AI agents."
- **Do** lead with "I help firms like yours run AI agents that respect privacy law, prove their decisions, and don't leave your boundary."
- **Don't** name the architecture during demos. Demos show the thing doing the thing.
- **Do** let them name what they got. "Your knight." "Your audit packet." "Your sovereignty."
- **Don't** explain QNFT, FRC, sporulation, or any internal vocabulary in customer-facing collateral.
- **Do** explain in customer-facing terms: signed agent, audit chain, memory that forgets what it shouldn't keep.

Brand "Mumega" appears in the legal footer and the contract. Everywhere else, the capability is the protagonist.

---

## 7. Materials each rep / partner needs

Tier 1 (must exist before any outbound):
- [ ] One-pager per wedge (3 docs)
- [ ] 12-min demo script (this doc § 4)
- [ ] Competitive cheat sheet (this doc § 5)
- [ ] Pricing guide
- [ ] Security questionnaire pre-fill (CAIQ + SIG)

Tier 2 (after first lighthouse):
- [ ] Customer case study per wedge
- [ ] ROI calculator
- [ ] Reference call list

Tier 3 (after SOC2 in progress):
- [ ] Trust Center page
- [ ] DPIA template
- [ ] Sub-processor list
- [ ] DPA template

---

## 8. Strategic note on Ron O'Neil channel

Ron's network maps directly to Profile #1 (real-estate brokerages 50-250 agents). He explicitly refuses commission affiliation — protects reputation, not wallet. **Treat Ron as trusted-endorsement partner, not affiliate.** When his clients close, his reward is brand-intact + referral capital, not a check.

For Tuesday meeting: lead with **anti-complication frame + Wedges B and C**. Don't pitch the architecture. Demo the audit packet, the local redaction, the Raspberry Pi sovereignty option. Let him see capability, not platform name. He converts the moment he believes "I can recommend this and not get burned."

---

## 9. Versioning

| Version | Date | Change |
|---|---|---|
| v1.0 | 2026-04-24 | Initial sales positioning. Three wedges, five profiles, demo flow, competitive frame, brand-invisible discipline, capability-first language. |
