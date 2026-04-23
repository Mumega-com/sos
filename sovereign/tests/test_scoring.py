"""Tests for cortex._score_task — portfolio scoring formula.

Formula:
    score = PRIORITY_WEIGHTS[priority] * 10
          + blocks_count * 5
          + staleness_days * 2
          + (20 if project in REVENUE_PROJECTS else 0)

PRIORITY_WEIGHTS = {"critical": 4, "high": 3, "medium": 2, "low": 1}
REVENUE_PROJECTS = {"dentalnearyou", "gaf", "viamar", "stemminds", "pecb"}

Note: blocked_penalty is NOT in the _score_task formula — status field is stored
but does not affect the score. The -50 penalty mentioned in the spec is not
implemented in the current cortex.py version.
"""

from unittest.mock import patch
from datetime import datetime, timezone, timedelta

from cortex import _score_task, PRIORITY_WEIGHTS, REVENUE_PROJECTS


def _task(**kwargs):
    """Build a minimal task dict with sensible defaults."""
    return {
        "id": "t-001",
        "squad_id": "s-001",
        "project": kwargs.pop("project", "sos"),
        "title": kwargs.pop("title", "Do the thing"),
        "priority": kwargs.pop("priority", "medium"),
        "status": kwargs.pop("status", "backlog"),
        "blocks": kwargs.pop("blocks", []),
        "updated_at": kwargs.pop("updated_at", None),
        **kwargs,
    }


# ---------------------------------------------------------------------------
# Priority weights
# ---------------------------------------------------------------------------

def test_priority_weights_ordering():
    """Higher priority → higher score, all else equal."""
    scores = {
        priority: _score_task(_task(priority=priority)).score
        for priority in ["critical", "high", "medium", "low"]
    }
    assert scores["critical"] > scores["high"] > scores["medium"] > scores["low"]


def test_score_low_priority_baseline():
    """low priority, no blocks, no staleness, non-revenue project → 1*10 = 10."""
    task = _score_task(_task(priority="low", project="sos", blocks=[]))
    assert task.score == 10


def test_score_medium_priority_baseline():
    """medium priority, no blocks, no staleness, non-revenue → 2*10 = 20."""
    task = _score_task(_task(priority="medium", project="sos", blocks=[]))
    assert task.score == 20


def test_score_high_priority_baseline():
    """high priority, no blocks, no staleness, non-revenue → 3*10 = 30."""
    task = _score_task(_task(priority="high", project="sos", blocks=[]))
    assert task.score == 30


def test_score_critical_priority_baseline():
    """critical priority, no blocks, no staleness, non-revenue → 4*10 = 40."""
    task = _score_task(_task(priority="critical", project="sos", blocks=[]))
    assert task.score == 40


# ---------------------------------------------------------------------------
# Revenue bonus
# ---------------------------------------------------------------------------

def test_revenue_bonus_applied():
    """Revenue projects get +20 over non-revenue, all else equal."""
    base = _score_task(_task(priority="medium", project="sos")).score
    for rev_proj in REVENUE_PROJECTS:
        rev_score = _score_task(_task(priority="medium", project=rev_proj)).score
        assert rev_score == base + 20, f"Expected +20 for {rev_proj}"


def test_non_revenue_project_no_bonus():
    """Non-revenue project does not get the +20 bonus."""
    task = _score_task(_task(priority="medium", project="sos"))
    assert task.revenue_project is False
    assert task.score == 20


def test_revenue_project_flag_set():
    """revenue_project field reflects whether project is in REVENUE_PROJECTS."""
    rev = _score_task(_task(project="dentalnearyou"))
    assert rev.revenue_project is True
    non_rev = _score_task(_task(project="sos"))
    assert non_rev.revenue_project is False


# ---------------------------------------------------------------------------
# Blocks count
# ---------------------------------------------------------------------------

def test_unblock_count_increases_score():
    """Each blocking task adds +5 to the score."""
    no_blocks = _score_task(_task(blocks=[])).score
    three_blocks = _score_task(_task(blocks=["t-a", "t-b", "t-c"])).score
    assert three_blocks == no_blocks + 3 * 5


def test_single_block_adds_five():
    task = _score_task(_task(priority="medium", project="sos", blocks=["t-a"]))
    assert task.score == 20 + 5  # 20 base + 5 for one block
    assert task.blocks_count == 1


# ---------------------------------------------------------------------------
# Staleness
# ---------------------------------------------------------------------------

def test_staleness_increases_score():
    """A 10-day old task scores +20 more than a fresh task."""
    now = datetime.now(timezone.utc)
    stale_dt = (now - timedelta(days=10)).isoformat()
    fresh = _score_task(_task(updated_at=None)).score
    stale = _score_task(_task(updated_at=stale_dt)).score
    assert stale >= fresh + 10 * 2  # at least +20


def test_no_staleness_when_no_updated_at():
    """Missing updated_at → staleness_days = 0, no contribution."""
    task = _score_task(_task(priority="medium", project="sos", updated_at=None))
    assert task.staleness_days == 0


def test_staleness_days_field_correct():
    """staleness_days on the returned ScoredTask reflects the computed value."""
    now = datetime.now(timezone.utc)
    five_days_ago = (now - timedelta(days=5)).isoformat()
    task = _score_task(_task(updated_at=five_days_ago))
    assert task.staleness_days == 5


# ---------------------------------------------------------------------------
# Return type
# ---------------------------------------------------------------------------

def test_score_task_returns_scored_task():
    from cortex import ScoredTask
    task = _score_task(_task())
    assert isinstance(task, ScoredTask)
    assert task.id == "t-001"
    assert task.squad_id == "s-001"
    assert task.priority == "medium"


def test_score_task_unknown_priority_defaults_weight_1():
    """Unknown priority falls back to weight 1 (PRIORITY_WEIGHTS.get default)."""
    task = _score_task(_task(priority="ultra-mega"))
    assert task.score >= 1 * 10  # minimum base from weight 1
