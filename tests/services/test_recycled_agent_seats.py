"""Recycled agent seats must stop being polled AND must not become task graves.

Context (2026-08-06): sol, mizan, gemma, dandan and worker were seats nobody had
worked with. task_poller iterates AGENT_ROUTING and polls the squad service for
every key, so each dead seat produced an HTTP 401 every ~10 minutes — 167 of them
in the six hours before removal, visible only in the squad service's access log.

Two things had to be true for the removal to be safe, and each has a test here:

1. The seats are gone from AGENT_ROUTING, so nothing polls them. Setting the
   route to "none" would NOT have worked: the poller reads AGENT_ROUTING's keys
   and fetches tasks before it ever consults the route value.

2. Nothing can still ASSIGN to them. A seat removed from routing while some
   caller keeps dispatching to it is worse than the noise it fixed — the task is
   created, assigned, and never delivered, with nothing logged anywhere. That is
   the same stranding shape as mupot#744.
"""
from __future__ import annotations

import pytest

from sos.services.bus.delivery import AGENT_ROUTING

RECYCLED = ("sol", "mizan", "gemma", "dandan", "worker")


@pytest.mark.parametrize("agent", RECYCLED)
def test_recycled_seat_is_absent_from_routing(agent: str) -> None:
    # Absent, not "none" — see the module docstring for why the distinction
    # decides whether the 401s actually stop.
    assert agent not in AGENT_ROUTING


def test_live_seats_survived_the_recycle() -> None:
    # The removal was surgical. If this list ever shrinks, a recycle took out a
    # working seat, which is the expensive direction of this mistake.
    for agent in ("kasra", "athena", "mumega", "mumcp", "river", "gemini"):
        assert agent in AGENT_ROUTING, f"{agent} must remain routable"


@pytest.mark.asyncio
async def test_dispatch_to_an_unroutable_agent_raises_instead_of_stranding() -> None:
    """The guard that makes the next recycle safe.

    Without it, dispatching to a removed seat succeeds and the task disappears.
    The failure must be loud at dispatch time, where someone can act on it.
    """
    from sos.services.analytics.act import ActionAgent  # noqa: PLC0415

    agent = ActionAgent.__new__(ActionAgent)  # no __init__ — the guard runs before any I/O
    with pytest.raises(ValueError, match="not routable"):
        await agent._dispatch_to_agent(
            agent="sol",
            title="Write blog post: anything",
            description="should never be created",
        )


@pytest.mark.asyncio
async def test_guard_names_the_agent_and_refuses_before_touching_the_coordinator() -> None:
    # Ordering matters: the check sits above the Coordinator import, so a bad
    # target cannot reach task creation even if the squad service is reachable.
    from sos.services.analytics.act import ActionAgent  # noqa: PLC0415

    agent = ActionAgent.__new__(ActionAgent)
    with pytest.raises(ValueError) as exc:
        await agent._dispatch_to_agent(
            agent="definitely-not-a-seat",
            title="t",
            description="d",
        )
    assert "definitely-not-a-seat" in str(exc.value)
