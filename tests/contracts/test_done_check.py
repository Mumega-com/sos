"""Contract tests for DoneCheck — closure-v1 Tier 1 T1.3 primitive.

Pins the structured completion-gate entry that replaces free-text notes on
Objective and (follow-up) SquadTask. See ``sos/contracts/done_check.py``.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from sos.contracts.done_check import DoneCheck, all_done


def test_minimum_required_fields():
    c = DoneCheck(id="c1", text="ship it")
    assert c.id == "c1"
    assert c.text == "ship it"
    assert c.done is False
    assert c.acked_by is None
    assert c.acked_at is None


def test_rejects_empty_id():
    with pytest.raises(ValidationError):
        DoneCheck(id="", text="nope")


def test_rejects_empty_text():
    with pytest.raises(ValidationError):
        DoneCheck(id="c1", text="")


def test_rejects_extra_fields():
    with pytest.raises(ValidationError):
        DoneCheck(id="c1", text="x", surprise="forbidden")


def test_all_done_vacuously_true_on_empty():
    assert all_done([]) is True


def test_all_done_true_when_every_check_done():
    checks = [DoneCheck(id="a", text="x", done=True),
              DoneCheck(id="b", text="y", done=True)]
    assert all_done(checks) is True


def test_all_done_false_when_any_check_pending():
    checks = [DoneCheck(id="a", text="x", done=True),
              DoneCheck(id="b", text="y", done=False)]
    assert all_done(checks) is False


def test_all_done_accepts_raw_dicts():
    checks = [{"id": "a", "text": "x", "done": True},
              {"id": "b", "text": "y", "done": True}]
    assert all_done(checks) is True


def test_all_done_mixed_dict_and_instance():
    checks = [DoneCheck(id="a", text="x", done=True),
              {"id": "b", "text": "y", "done": False}]
    assert all_done(checks) is False


def test_all_done_missing_done_key_is_false():
    # A dict without "done" must be treated as not-done (truthy-safe default)
    checks = [{"id": "a", "text": "x"}]
    assert all_done(checks) is False
