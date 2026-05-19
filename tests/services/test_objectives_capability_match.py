"""TROP-shaped end-to-end assertion on capability matching.

The objectives storage already supports ``query_open(..., capability=...)``
(see test_objectives_storage.py::test_query_open_filters_by_capability).
This test pins the exact scenario S4 depends on: an agent holding
``skills=["post-instagram"]`` should only see the objectives whose
``capabilities_required`` includes that same slug.

If this test starts failing, the plan's assumption that capability matching
already works has broken — tell S4.
"""
from __future__ import annotations

import fakeredis
import pytest

from sos.contracts.objective import Objective
import sos.services.objectives as obj_store


# Use slug-form IDs (the Objective.id regex permits both ULIDs and slugs).
ID_SOCIAL = "trop-social-daily-post"
ID_CONTENT = "trop-content-daily-blog"
ID_OUTREACH = "trop-outreach-inbox-triage"


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> fakeredis.FakeRedis:
    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(obj_store, "_get_redis", lambda: r)
    return r


def _write(obj_id: str, capability: str) -> None:
    now = Objective.now_iso()
    obj_store.write_objective(
        Objective(
            id=obj_id,
            title=f"{capability} work",
            state="open",
            capabilities_required=[capability],
            tags=["trop", "daily-rhythm"],
            created_by="pulse:trop",
            created_at=now,
            updated_at=now,
            project="trop",
            tenant_id="trop",
        )
    )


def test_trop_social_card_only_sees_matching_objectives() -> None:
    """A trop-social agent's capability slug filters the open set correctly."""
    _write(ID_SOCIAL, "post-instagram")
    _write(ID_CONTENT, "blog-draft")
    _write(ID_OUTREACH, "email-draft")

    # The standing trop-social card declares skills=["post-instagram", ...].
    # query_open(capability="post-instagram") must return only the matching node.
    results = obj_store.query_open(project="trop", capability="post-instagram")
    assert len(results) == 1
    assert results[0].id == ID_SOCIAL
    assert "post-instagram" in results[0].capabilities_required

    # Sanity: a capability no objective declares returns nothing.
    assert obj_store.query_open(project="trop", capability="compile-binary") == []
