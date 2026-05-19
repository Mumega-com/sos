"""Tests for sos.services.operations.organism.

The organism is a long-running loop that fires one pulse per project per
window per day, plus posts postmortems for newly-paid root objectives. Every
test here mocks both the AsyncObjectivesClient and Redis so nothing hits
the wire — fakeredis gives us a real Redis-like backend, monkeypatch gives
us a frozen clock.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pytest

from sos.clients.objectives import AsyncObjectivesClient
from sos.contracts.objective import Objective
from sos.services.operations import organism, pulse

try:
    import fakeredis  # type: ignore[import-untyped]

    HAS_FAKEREDIS = True
except ImportError:  # pragma: no cover
    HAS_FAKEREDIS = False

skipif_no_fakeredis = pytest.mark.skipif(
    not HAS_FAKEREDIS, reason="fakeredis not installed"
)


# ULIDs for stand-in Objective ids.
# Crockford base32 — no I, L, O, U characters.
ULID_ROOT = "01HWZZZZZZZZZZZZZZZZRT0001"
ULID_CHILD_PREFIX = "01HWZZZZZZZZZZZZZZZZCH"
PAID_ROOT_ID = "01HWZZZZZZZZZZZZZZZZPADR01"


def _mk_objective(obj_id: str, **overrides: Any) -> Objective:
    now = Objective.now_iso()
    defaults: dict[str, Any] = {
        "id": obj_id,
        "title": "x",
        "created_by": "test",
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(overrides)
    return Objective(**defaults)


# ---------------------------------------------------------------------------
# _current_window — hour-based dispatch
# ---------------------------------------------------------------------------


def _freeze_hour(monkeypatch: pytest.MonkeyPatch, hour: int) -> None:
    """Force datetime.now() inside organism.py to return a fixed local hour."""
    fixed = datetime(2026, 4, 18, hour, 30, 0)

    class _FrozenDT(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:  # type: ignore[override]
            if tz is not None:
                return fixed.replace(tzinfo=tz)
            return fixed

    monkeypatch.setattr(organism, "datetime", _FrozenDT)


def test_current_window_morning(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_hour(monkeypatch, 7)
    assert organism._current_window() == "morning"


def test_current_window_noon(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_hour(monkeypatch, 12)
    assert organism._current_window() == "noon"


def test_current_window_evening(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_hour(monkeypatch, 19)
    assert organism._current_window() == "evening"


def test_current_window_off_hours(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_hour(monkeypatch, 3)
    assert organism._current_window() is None


# ---------------------------------------------------------------------------
# step() — pulse firing with per-window dedupe
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_redis():
    if not HAS_FAKEREDIS:  # pragma: no cover
        pytest.skip("fakeredis not installed")
    return fakeredis.FakeStrictRedis(decode_responses=True)


@skipif_no_fakeredis
@pytest.mark.asyncio
async def test_step_fires_pulse_once_per_window(
    monkeypatch: pytest.MonkeyPatch, fake_redis: Any
) -> None:
    """Morning window: first tick fires; second tick same window does not."""
    _freeze_hour(monkeypatch, 7)

    fire_count = {"n": 0}

    async def fake_morning(project: str, *, client: AsyncObjectivesClient | None = None) -> str:
        fire_count["n"] += 1
        return ULID_ROOT

    monkeypatch.setattr(pulse, "post_morning_pulse", fake_morning)

    org = organism.Organism(
        projects=["trop"],
        redis_client=fake_redis,
        client=object(),  # unused when pulse is monkeypatched
    )

    await org.step()
    await org.step()

    assert fire_count["n"] == 1
    # Cache key exists and holds today's date.
    raw = fake_redis.get("sos:organism:last_ran:trop:morning")
    assert raw == datetime.now().strftime("%Y-%m-%d")


@skipif_no_fakeredis
@pytest.mark.asyncio
async def test_step_fires_different_pulses_across_windows(
    monkeypatch: pytest.MonkeyPatch, fake_redis: Any
) -> None:
    """As the clock advances morning→noon→evening, each pulse fires once."""
    fired: list[str] = []

    def _make(window_name: str):
        async def _fn(project: str, *, client: Any = None) -> str:
            fired.append(window_name)
            return ULID_ROOT
        return _fn

    monkeypatch.setattr(pulse, "post_morning_pulse", _make("morning"))
    monkeypatch.setattr(pulse, "post_noon_pulse", _make("noon"))
    monkeypatch.setattr(pulse, "post_evening_pulse", _make("evening"))

    org = organism.Organism(
        projects=["trop"],
        redis_client=fake_redis,
        client=object(),
    )

    _freeze_hour(monkeypatch, 7)
    await org.step()
    _freeze_hour(monkeypatch, 12)
    await org.step()
    _freeze_hour(monkeypatch, 19)
    await org.step()

    assert fired == ["morning", "noon", "evening"]


# ---------------------------------------------------------------------------
# step() — postmortem on paid roots
# ---------------------------------------------------------------------------


@skipif_no_fakeredis
@pytest.mark.asyncio
async def test_step_posts_postmortem_on_paid_root(
    monkeypatch: pytest.MonkeyPatch, fake_redis: Any
) -> None:
    """XADD a paid-root event; step() posts a postmortem; second tick is a no-op."""
    # Off-hours so no pulse fires — isolate the postmortem scan.
    _freeze_hour(monkeypatch, 3)

    # Seed the audit stream with a paid-root event in the flat shape.
    fake_redis.xadd(
        organism._AUDIT_STREAM,
        {
            "type": "objective.state_changed",
            "payload": json.dumps({
                "id": PAID_ROOT_ID,
                "prior_state": "shipped",
                "new_state": "paid",
                "project": "trop",
            }),
        },
    )

    # Stub client: .get returns a root (parent_id=None), .create records calls.
    create_calls: list[dict[str, Any]] = []

    class _FakeClient:
        async def get(self, obj_id: str, *, project: str | None = None) -> Objective | None:
            if obj_id == PAID_ROOT_ID:
                return _mk_objective(PAID_ROOT_ID, project="trop")
            return None

        async def create(self, title: str, **kwargs: Any) -> Objective:
            create_calls.append({"title": title, **kwargs})
            return _mk_objective(f"{ULID_CHILD_PREFIX}0001")

    client = _FakeClient()
    org = organism.Organism(
        projects=["trop"],
        redis_client=fake_redis,
        client=client,  # type: ignore[arg-type]
    )

    await org.step()

    # Exactly one postmortem posted.
    postmortems = [c for c in create_calls if c["title"].startswith("postmortem-")]
    assert len(postmortems) == 1
    pm = postmortems[0]
    assert pm["title"] == f"postmortem-{PAID_ROOT_ID}"
    assert "kind:postmortem" in pm["tags"]
    assert f"parent_of_postmortem:{PAID_ROOT_ID}" in pm["tags"]
    assert pm["project"] == "trop"

    # Dedupe set populated.
    assert fake_redis.sismember(organism._POSTMORTEM_SET_KEY, PAID_ROOT_ID)

    # Second tick with the same event in the stream must not double-post.
    create_calls.clear()
    await org.step()
    assert create_calls == []


@skipif_no_fakeredis
@pytest.mark.asyncio
async def test_step_skips_postmortem_for_non_root(
    monkeypatch: pytest.MonkeyPatch, fake_redis: Any
) -> None:
    """A paid event for a child objective (parent_id set) must not trigger a postmortem."""
    _freeze_hour(monkeypatch, 3)

    child_id = "01HWZZZZZZZZZZZZZZZZCHDDD1"
    fake_redis.xadd(
        organism._AUDIT_STREAM,
        {
            "type": "objective.state_changed",
            "payload": json.dumps({
                "id": child_id,
                "new_state": "paid",
                "project": "trop",
            }),
        },
    )

    create_calls: list[dict[str, Any]] = []

    class _FakeClient:
        async def get(self, obj_id: str, *, project: str | None = None) -> Objective | None:
            # Return objective WITH a parent — so the scan treats it as non-root.
            return _mk_objective(child_id, parent_id=ULID_ROOT, project="trop")

        async def create(self, title: str, **kwargs: Any) -> Objective:
            create_calls.append({"title": title, **kwargs})
            return _mk_objective("01HWZZZZZZZZZZZZZZZZNEW001")

    org = organism.Organism(
        projects=["trop"],
        redis_client=fake_redis,
        client=_FakeClient(),  # type: ignore[arg-type]
    )

    await org.step()
    assert [c for c in create_calls if c["title"].startswith("postmortem-")] == []


# ---------------------------------------------------------------------------
# step() — fail-soft on objectives service errors
# ---------------------------------------------------------------------------


@skipif_no_fakeredis
@pytest.mark.asyncio
async def test_step_fail_soft_on_objectives_error(
    monkeypatch: pytest.MonkeyPatch, fake_redis: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """If AsyncObjectivesClient.create raises, step() must not propagate."""
    _freeze_hour(monkeypatch, 7)  # morning → triggers pulse

    async def boom_create(self: AsyncObjectivesClient, title: str, **kwargs: Any) -> Objective:
        raise RuntimeError("objectives service down")

    monkeypatch.setattr(AsyncObjectivesClient, "create", boom_create)

    org = organism.Organism(
        projects=["trop"],
        redis_client=fake_redis,
    )

    # Must not raise.
    await org.step()

    # The per-window cache must NOT be set — we want the next tick to retry.
    assert fake_redis.get("sos:organism:last_ran:trop:morning") is None


# ---------------------------------------------------------------------------
# Guards — construction errors and defaults
# ---------------------------------------------------------------------------


def test_organism_rejects_empty_projects() -> None:
    with pytest.raises(ValueError):
        organism.Organism(projects=[])


def test_pulse_for_window_dispatch_table() -> None:
    assert organism._pulse_for_window("morning") is pulse.post_morning_pulse
    assert organism._pulse_for_window("noon") is pulse.post_noon_pulse
    assert organism._pulse_for_window("evening") is pulse.post_evening_pulse
    with pytest.raises(ValueError):
        organism._pulse_for_window("midnight")
