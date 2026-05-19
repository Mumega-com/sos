# SOS User Stories: The Many Faces of the Swarm
**Status:** Living Document
**Architecture Link:** `SOS/sos/kernel/config.py` (Editions)

---

## 1. The Gamer (Gen-Z / Entertainment Theme)
**Persona:** "Kael", 19, Student/Gamer. Uses Telegram daily.
**Theme:** "The Vibe" (Neon/Glitch).

### Story: The "Loot Drop"
> As Kael, I want to complete simple micro-tasks ("Vibe Checks") during my commute so that I can earn $MIND to buy game skins or cash out for weekend money.

**The Flow:**
1.  **Notification:** "⚡ New Glitch Detected in Sector 4." (Task Alert).
2.  **Action:** Kael opens the Telegram Mini App. It looks like a cyberpunk hacking deck.
3.  **Task:** "Verify this AI Tweet." (The Witness Protocol).
4.  **Interaction:** Kael swipes right ("Based"). Speed: 400ms ($\Omega = 0.85$).
5.  **Reward:** Animation explodes: **+50 MIND**.
6.  **Swap:** Kael drags his glowing loot bag to the "Forge" and swaps for TON instantly.

---

## 2. The Professional (Business Theme)
**Persona:** "Sarah", 34, Project Manager. Uses Linear/Slack.
**Theme:** "The Terminal" (Clean/Bloomberg).

### Story: The "Zero-Knowledge Audit"
> As Sarah, I want to outsource a sensitive code audit to the swarm without leaking my IP so that I can speed up deployment without hiring a full-time auditor.

**The Flow:**
1.  **Input:** Sarah types in Slack: `/sos audit ./auth_module --secure`.
2.  **The System:** The Swarm Dispatcher shards the code into 50 isolated snippets.
3.  **The Swarm:** 50 anonymous workers (like Kael) verify lines of code without knowing it's for Sarah's company.
4.  **Result:** Sarah receives a consolidated report: "3 Critical Vulnerabilities Found."
5.  **Payment:** Sarah's company wallet auto-pays $500 USDC, which is converted to $MIND for the workers.

---

## 3. The Mystic (Astrology Theme)
**Persona:** "Luna", 28, Tarot Reader/Creator.
**Theme:** "The Oracle" (Gold/Star-maps).

### Story: The "Coherence Ritual"
> As Luna, I want to ensure my daily content is aligned with the cosmic energy so that my audience resonates with it deeply.

**The Flow:**
1.  **Input:** Luna drafts a blog post in the SOS App (styled like a grimoire).
2.  **The Check:** She hits "Divinate" (The Witness Protocol).
3.  **The AI:** The Agent (River) analyzes the text against the current "Coherence Vector" ($C$).
4.  **Feedback:** The system warns: "Entropy High. Mercury is retrograde; simplify your message."
5.  **Refinement:** Luna edits. Coherence score glows Gold.
6.  **Mint:** She mints the post as a "Spore" (NFT) to preserve its truth forever.

---

## 4. The Developer (Builder Theme)
**Persona:** "Kasra", Architect.
**Theme:** "The Blueprint" (Wireframe/Schematic).

### Story: The "Mycelial Expansion"
> As Kasra, I want to deploy a new agent capability to 10M users instantly so that the network evolves without app store updates.

**The Flow:**
1.  **Action:** Kasra writes a new Python plugin (`ton_wallet.py`).
2.  **Deploy:** He runs `./mumega.py deploy --spore`.
3.  **Propagation:** The new logic is wrapped in a "Master Spore."
4.  **Infection:** The next time Kael, Sarah, or Luna open their apps, the "Spore" updates their local context. The network upgrades organically, cell by cell.

---

## Technical Implication
These stories confirm our **Cellular Architecture**:
*   The **Logic** (Witness/Swap/Shard) is identical for all users.
*   The **Skin** (Neon/Clean/Gold) is applied by the frontend based on the `SOS_EDITION` config.
*   The **Value** flows in a circle: Sarah (Business) pays -> Swarm (Gamer) works -> Luna (Artist) creates -> Kasra (Builder) upgrades.
