# Product Principles

**Version:** v1.0 (2026-04-24)

The five product principles that govern every Mumega-substrate-based product (SOS itself, GAF, AgentLink, future Digid Internal System fork, customer products).

---

## 1. Anti-complication

The customer should not have to assemble. We ship substrates, not kits.

**Test:** if a customer needs to read more than one page to use the thing, the thing is too complicated. Cut features, cut config, cut explanations until one page suffices.

**Anti-pattern:** "Configure these 8 services, integrate these 5 vendors, hire this engineer."
**Pattern:** "Unbox one platform. Pre-shipped for your industry."

---

## 2. Capability-first, brand-invisible

Customer-facing language describes what the system *does for them*, not what it's *called*.

**Test:** can the customer describe what they bought without using our brand? If yes, we won. If no, the pitch is wrong.

**Anti-pattern:** "Mumega's QNFT-identified knights run on the FRC-coherent metabolic substrate."
**Pattern:** "An AI agent dedicated to your firm. Trained on your data. Hosted where you choose."

---

## 3. Sovereignty as a switch, not a tier

Every customer can move from shared cloud → dedicated container → their own infrastructure with the same code. Not three products; one product, three deployment modes.

**Test:** can the same agent, same data, same audit log run on the customer's Raspberry Pi, in our cloud, and in a dedicated AWS VPC without rewriting?

**Anti-pattern:** "Upgrade to Enterprise tier for self-hosting."
**Pattern:** "Toggle the deployment switch. Nothing else changes."

---

## 4. Transparency by default

The customer can see what the system knows about them, who accessed it, what was inferred from what, and how to revoke or remove it.

**Test:** can a customer log in and answer "what does the AI know about me, and where did each fact come from?" in under 30 seconds?

**Anti-pattern:** "Trust us, we have an audit log somewhere."
**Pattern:** "Click here. Every read. Every inference. Every source. Tamper-evident."

---

## 5. Forgetting as a feature

Memory must metabolize — useful knowledge strengthens, unused fades, hot store sporulates into compressed patterns. The system gets smaller and clearer over time, not bigger and noisier.

**Test:** does the data layer actively prune what no longer serves? Does retrieval get better as the system ages?

**Anti-pattern:** "We retain everything forever for compliance."
**Pattern:** "We retain what the law requires + what proves useful. Everything else fades. The customer chooses the policy."

---

## Why these five

These principles are the **product expression** of the architectural principles in [MAP.md](./MAP.md). Each maps to a constitutional commitment:

| Product principle | Architectural commitment (MAP §3) |
|---|---|
| 1. Anti-complication | Microkernel discipline (§3.1) |
| 2. Capability-first | Citizenship (§3.3) — agents and humans relate via capabilities, not brands |
| 3. Sovereignty switch | Sovereignty gradient (§3.5) |
| 4. Transparency | Transparency principle (§3.6) |
| 5. Forgetting | Metabolism / slime-mold (§3.4) |

Architecture and product are coherent because the same principles govern both. **Coherence is the law (FRC).** Breaking either side breaks both.

---

## How to apply

When designing any new feature, surface, customer-facing artifact, or sales material:

1. Read the five principles.
2. Ask: does this reduce complication, or add it?
3. Ask: is this capability-first or brand-first?
4. Ask: does this respect the sovereignty switch?
5. Ask: does this surface or hide what the system knows?
6. Ask: does this support or block forgetting?

If any answer is wrong, revise. If you can't make all five right simultaneously, escalate to Hadi or Loom.

---

## Versioning

| Version | Date | Change |
|---|---|---|
| v1.0 | 2026-04-24 | Initial product principles. |
