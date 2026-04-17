#!/usr/bin/env python3
"""P2.1 demo — AI-to-AI commerce between two Mumega squads.

One squad (GAF, doing an SR&ED intake for a client) needs to draft a
Persian-diaspora-flavored cold-email. Instead of having GAF's own agents
do it, it invokes the **DNU squad's** outreach-drafter skill via the
marketplace. DNU's squad does the work; settlement happens in $MIND.

The demo:
  1. Loads both SkillCards from sos/skills/registry/
  2. GAF's agent issues a skill-invocation request
  3. DNU's skill "runs" (mocked output — the contract is what's being
     demonstrated, not the LLM output)
  4. Records the transaction:
     - UsageLog event (what was spent, by which tenant, what it cost)
     - WorkLedger-like settlement (author/operator/network split)
     - Registry.record_invocation() bumps DNU skill's earnings counter
  5. Prints a trace showing every step so the demo video has visible
     receipts at each stage

Run:
    uv run --with pydantic --with jsonschema --with referencing \\
        python3 -m scripts.demo_ai_to_ai_commerce

Expected result: a formatted transcript, the DNU skill's JSON file is
updated with +1 invocation + the payment amount added to earnings.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sos.contracts.skill_card import SkillCard
from sos.services.economy.usage_log import UsageEvent, UsageLog
from sos.skills.registry import Registry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _box(title: str, lines: list[str], width: int = 78) -> None:
    print("┌" + "─" * (width - 2) + "┐")
    print("│ " + title.ljust(width - 4) + " │")
    print("├" + "─" * (width - 2) + "┤")
    for ln in lines:
        # wrap at width-4 for readability
        remaining = ln
        while len(remaining) > width - 4:
            print("│ " + remaining[: width - 4] + " │")
            remaining = remaining[width - 4 :]
        print("│ " + remaining.ljust(width - 4) + " │")
    print("└" + "─" * (width - 2) + "┘")
    print()


def _apply_split(amount_micros: int, split: dict[str, float]) -> dict[str, int]:
    """Apply a revenue_split to a gross amount. Returns per-recipient micros."""
    return {
        "author": int(amount_micros * (split.get("author", 0.0))),
        "operator": int(amount_micros * (split.get("operator", 0.0))),
        "network": int(amount_micros * (split.get("network", 0.0))),
    }


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------


def run_demo() -> int:
    print()
    _box(
        "P2.1 — AI-to-AI Commerce Demo (2026-04-17)",
        [
            "Scenario: A GAF squad is running an SR&ED intake for a ",
            "Persian-Canadian dental practice. Instead of building ",
            "outreach drafts itself, GAF purchases one invocation of ",
            "DNU's 'Dentist Outreach Email Drafter' skill.",
            "",
            "This is the thing no incumbent has shipped as a product — ",
            "one Mumega squad buying a skill from another, with ",
            "provenance + settlement + earnings history updated live.",
        ],
    )

    registry = Registry()
    all_cards = registry.list()
    if len(all_cards) < 2:
        print(f"ERROR: need at least two cards in registry, found {len(all_cards)}")
        return 1

    buyer = registry.get("gaf-sred-scan-estimator-v1")
    seller = registry.get("dnu-dentist-outreach-drafter-v1")
    if not buyer or not seller:
        print("ERROR: could not find both skill cards.")
        return 1

    # Step 1 — print the two cards so the demo video has receipts
    _box(
        "Step 1 — The two skills involved (from the registry)",
        [
            f"Buyer-side: {buyer.name}",
            f"  id={buyer.id}  author={buyer.author_agent}",
            f"  price={buyer.commerce.price_per_call_micros if buyer.commerce else 0} micros",
            f"",
            f"Seller-side: {seller.name}",
            f"  id={seller.id}  author={seller.author_agent}",
            f"  price={seller.commerce.price_per_call_micros if seller.commerce else 0} micros",
        ],
    )

    price = seller.commerce.price_per_call_micros if seller.commerce else 0
    if price == 0:
        print("WARN: seller's skill price is 0; settlement will be trivial.")

    # Step 2 — GAF invokes DNU's skill (mocked output)
    request_id = f"req_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    buyer_input = {
        "practice_name": "Richmond Hill Family Dental",
        "practice_url": "https://example-practice.ca",
        "tone": "warm",
        "persian_diaspora_hook": True,
    }

    # The actual LLM call is mocked for the demo; the contract is what matters.
    mocked_output = {
        "subject": "A five-minute way to fill your Persian-Canadian family block — without cold calls",
        "body": (
            "Salaam — I'll keep this short. Richmond Hill Family Dental shows up for "
            "Farsi searches for cosmetic consults, but you're not capturing the "
            "referral loop once patients convert…"
        ),
        "tone_used": "warm",
        "ps_hook": "PS — if you want to see the first draft for the Vaughan location, we can spin one up in a day.",
    }

    _box(
        "Step 2 — Invocation (GAF → DNU skill)",
        [
            f"request_id: {request_id}",
            f"calling_squad: {buyer.author_agent}",
            f"target_skill: {seller.id}",
            f"input: {json.dumps(buyer_input, ensure_ascii=False)[:140]}",
            f"",
            f"↓ DNU skill executes (mocked LLM output for demo) ↓",
            f"",
            f"output.subject: {mocked_output['subject'][:100]}",
            f"output.tone_used: {mocked_output['tone_used']}",
        ],
    )

    # Step 3 — settle
    split = {"author": 0.7, "operator": 0.2, "network": 0.1}
    payout = _apply_split(price, split)
    _box(
        "Step 3 — Settlement (in $MIND micros, per revenue_split)",
        [
            f"gross:       {price:>12,} micros  (from seller.commerce.price_per_call_micros)",
            f"author:      {payout['author']:>12,} micros  → agent:{seller.author_agent.split(':')[1]}  (70%)",
            f"operator:    {payout['operator']:>12,} micros  → Mumega treasury    (20%)",
            f"network:     {payout['network']:>12,} micros  → $MIND pool         (10%)",
        ],
    )

    # Step 4 — log to UsageLog (the economy ledger)
    log = UsageLog()
    event = UsageEvent(
        tenant="gaf",                                # the tenant who paid
        provider="mumega-marketplace",
        model=f"skill:{seller.id}",
        endpoint="/marketplace/invoke",
        input_tokens=0,
        output_tokens=0,
        cost_micros=price,
        cost_currency="USD",
        metadata={
            "request_id": request_id,
            "buyer_skill": buyer.id,
            "seller_skill": seller.id,
            "seller_agent": seller.author_agent,
            "settlement": payout,
            "ai_to_ai_commerce": True,
        },
    )
    stored = log.append(event)

    _box(
        "Step 4 — UsageLog entry written",
        [
            f"id:          {stored.id}",
            f"received_at: {stored.received_at}",
            f"log file:    {log.path}",
            f"tenant:      {stored.tenant}",
            f"model:       {stored.model}",
            f"cost_micros: {stored.cost_micros:,}",
            f"metadata.ai_to_ai_commerce: True  ← this is the tag that makes",
            f"  the Dashboard 'AI-to-AI transaction count' panel light up",
        ],
    )

    # Step 5 — bump DNU skill's earnings record in the registry
    updated = registry.record_invocation(seller.id, tenant="gaf", amount_micros=payout["author"])
    if updated and updated.earnings:
        _box(
            "Step 5 — DNU skill earnings record updated",
            [
                f"skill: {updated.name}",
                f"  total_invocations:   {updated.earnings.total_invocations}  (was {(seller.earnings.total_invocations if seller.earnings else 0)})",
                f"  total_earned_micros: {updated.earnings.total_earned_micros:,}  "
                f"(+{payout['author']:,})",
                f"  last_invocation_at:  {updated.earnings.last_invocation_at}",
                f"  invocations_by_tenant: {updated.earnings.invocations_by_tenant}",
            ],
        )

    _box(
        "Done — one AI-to-AI transaction recorded",
        [
            f"The registry file at:",
            f"  sos/skills/registry/{seller.id}.json",
            f"now carries the updated earnings.",
            f"",
            f"The UsageLog at:",
            f"  {log.path}",
            f"now has an event tagged 'ai_to_ai_commerce': True.",
            f"",
            f"Dashboard Phase 2 (Money pulse, Skill-moat panel) will",
            f"read these two sources to show cross-squad commerce live.",
        ],
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(run_demo())
