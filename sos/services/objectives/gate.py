"""Completion gate for v0.8.0 objectives.

When an objective reaches enough acks (parent-holder OR N peer acks, whichever
first), transition state shipped → paid and trigger the economy bounty payout.

Fail-soft: economy errors are logged but do not revert the ack or state
transition. The objective becomes paid on the books; reconciliation is the
economy service's job.
"""
from __future__ import annotations

import os
from typing import Optional

from sos.contracts.objective import Objective
from sos.services.objectives import read_objective, write_objective

# v0.8.0: hardcoded. v0.8.1 reads per-node completion_policy.
_REQUIRED_ACKS = int(os.getenv("SOS_OBJECTIVES_REQUIRED_ACKS", "1"))


async def check_completion(
    obj_id: str,
    *,
    project: str | None = None,
    economy_client=None,  # inject for testability; default constructed lazily
    logger=None,
) -> Optional[Objective]:
    """If obj is shipped and has enough acks, flip to paid + pay bounty.

    Returns the updated Objective if transitioned, else None (not ready / not
    shipped / missing / already paid).
    """
    obj = read_objective(obj_id, project=project)
    if obj is None:
        return None
    if obj.state != "shipped":
        return None
    if not _enough_acks(obj):
        return None

    # Transition to paid BEFORE calling economy, so duplicate ack races
    # don't double-pay. The economy call is idempotent by memo in a
    # real system; for v0.8.0, we accept that a failed economy call
    # still marks paid (operators reconcile).
    obj.state = "paid"
    obj.updated_at = Objective.now_iso()
    write_objective(obj)

    if obj.bounty_mind > 0 and obj.holder_agent:
        await _pay_bounty(obj, economy_client=economy_client, logger=logger)

    return obj


def _enough_acks(obj: Objective) -> bool:
    # parent-holder ack counts as sufficient on its own
    # else >= _REQUIRED_ACKS peer acks
    if obj.parent_id and obj.parent_id in obj.acks:
        # NOTE: v0.8.0 treats parent_id itself as the "parent-holder" ack
        # signal. v0.8.1 will resolve parent_id -> parent.holder_agent and
        # check that instead. This is a known simplification.
        return True
    return len(obj.acks) >= _REQUIRED_ACKS


async def _pay_bounty(
    obj: Objective,
    *,
    economy_client=None,
    logger=None,
) -> None:
    if economy_client is None:
        from sos.clients.economy import AsyncEconomyClient
        economy_client = AsyncEconomyClient()
    try:
        await economy_client.credit(
            user_id=obj.holder_agent,
            amount=obj.bounty_mind,
            reason=f"objective:{obj.id}",
        )
    except Exception as exc:  # noqa: BLE001
        if logger:
            logger.warn("bounty payout failed", obj_id=obj.id, error=str(exc))
        # swallow — objective stays in 'paid' state; reconciliation is separate
