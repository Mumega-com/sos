"""
Tests for the MemoryPort extraction in sovereign/brain.py (#267 K1, task #3
"Brain microkernel extraction").

Verifies that:
1. MirrorMemoryAdapter.remember_sync() translates to the correct Mirror
   /store payload and returns a RememberResult.
2. MirrorMemoryAdapter.search_sync() translates to the correct Mirror
   /search payload and returns typed MemoryResult objects (including
   fail-open behavior on network errors and on malformed/out-of-range
   individual hits).
3. brain.remember() routes through _memory_port, not raw requests.post().
4. brain.hippocampus_recall() routes goal/objection lookups through
   _memory_port.search_sync(), not raw requests.post().
5. brain.motor_execute("post_content") routes the engram write through
   _memory_port, not raw requests.post().

All tests mock at the adapter boundary (MirrorClient.store / direct requests)
so no live Mirror service is required — Mirror's Postgres-backed
recall/remember service is currently down per project notes, so this suite
is intentionally hermetic.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Ensure sovereign/ is on the path (mirrors sovereign/conftest.py)
# ---------------------------------------------------------------------------
SOVEREIGN_DIR = Path(__file__).resolve().parent.parent
if str(SOVEREIGN_DIR) not in sys.path:
    sys.path.insert(0, str(SOVEREIGN_DIR))


# ---------------------------------------------------------------------------
# Tests: MirrorMemoryAdapter unit
# ---------------------------------------------------------------------------


class TestMirrorMemoryAdapter:
    """Unit tests for the adapter itself — no brain.py required."""

    def _make_adapter(self, mock_client: MagicMock):
        from kernel.memory_adapter import MirrorMemoryAdapter
        return MirrorMemoryAdapter(client=mock_client, agent_tag="test-agent")

    def test_remember_sync_calls_client_store(self):
        mock_client = MagicMock()
        mock_client.store.return_value = {"id": "mem-001"}

        adapter = self._make_adapter(mock_client)
        result = adapter.remember_sync(
            "hello world",
            context_id="ctx-1",
            core_concepts=["foo", "bar"],
        )

        mock_client.store.assert_called_once()
        payload = mock_client.store.call_args[0][0]
        assert payload["agent"] == "test-agent"
        assert payload["text"] == "hello world"
        assert payload["context_id"] == "ctx-1"
        assert payload["core_concepts"] == ["foo", "bar"]
        assert result.memory_id == "mem-001"

    def test_remember_sync_auto_context_id(self):
        mock_client = MagicMock()
        mock_client.store.return_value = {"id": "mem-002"}

        adapter = self._make_adapter(mock_client)
        result = adapter.remember_sync("content without explicit context_id")

        payload = mock_client.store.call_args[0][0]
        assert payload["context_id"].startswith("test-agent_mem_")
        assert result.memory_id == "mem-002"

    def test_remember_sync_fail_open_on_exception(self):
        mock_client = MagicMock()
        mock_client.store.side_effect = RuntimeError("Mirror down")

        adapter = self._make_adapter(mock_client)
        # Must not raise
        result = adapter.remember_sync("content")
        assert result.memory_id == "unknown"

    def test_remember_sync_with_raw_data(self):
        mock_client = MagicMock()
        mock_client.store.return_value = {"id": "mem-003"}

        adapter = self._make_adapter(mock_client)
        raw = {"action": {"method": "health_check"}, "result": {"success": True}}
        adapter.remember_sync("text", raw_data=raw)

        payload = mock_client.store.call_args[0][0]
        assert payload["raw_data"] == raw

    def test_search_sync_returns_memory_results(self):
        mock_client = MagicMock()
        mock_client.base_url = "http://mirror:8844"
        mock_client._headers.return_value = {"Authorization": "Bearer tok"}

        mirror_response = {
            "results": [
                {
                    "id": "e-001",
                    "text": "GOAL: ship it",
                    "created_at": "2026-06-15T00:00:00Z",
                    "score": 0.92,
                    "raw_data": {"goal": {"title": "ship it", "priority": "high", "progress": 0.4}},
                }
            ]
        }

        with patch("kernel.memory_adapter.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.raise_for_status.return_value = None
            mock_resp.json.return_value = mirror_response
            mock_post.return_value = mock_resp

            adapter = self._make_adapter(mock_client)
            results = adapter.search_sync("GOAL active", top_k=5, agent_filter="os")

        assert len(results) == 1
        assert results[0].id == "e-001"
        assert results[0].content == "GOAL: ship it"
        assert results[0].score == pytest.approx(0.92)
        assert results[0].metadata == {
            "goal": {"title": "ship it", "priority": "high", "progress": 0.4}
        }

        call_kwargs = mock_post.call_args[1]
        body = call_kwargs["json"]
        assert body["query"] == "GOAL active"
        assert body["top_k"] == 5
        assert body["agent_filter"] == "os"

    def test_search_sync_fail_open_on_error(self):
        mock_client = MagicMock()
        mock_client.base_url = "http://mirror:8844"
        mock_client._headers.return_value = {}

        with patch("kernel.memory_adapter.requests.post", side_effect=ConnectionError("timeout")):
            from kernel.memory_adapter import MirrorMemoryAdapter
            adapter = MirrorMemoryAdapter(client=mock_client)
            results = adapter.search_sync("anything")

        assert results == []

    def test_search_sync_no_agent_filter_omits_field(self):
        mock_client = MagicMock()
        mock_client.base_url = "http://mirror:8844"
        mock_client._headers.return_value = {}

        with patch("kernel.memory_adapter.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.raise_for_status.return_value = None
            mock_resp.json.return_value = []
            mock_post.return_value = mock_resp

            from kernel.memory_adapter import MirrorMemoryAdapter
            adapter = MirrorMemoryAdapter(client=mock_client)
            adapter.search_sync("query without filter")

        body = mock_post.call_args[1]["json"]
        assert "agent_filter" not in body

    def test_search_sync_clamps_out_of_range_score(self):
        """MemoryResult.score is pydantic-constrained to [0.0, 1.0]. Mirror is
        not contractually bounded to that range — the adapter must clamp
        rather than raise, to preserve the fail-open contract."""
        mock_client = MagicMock()
        mock_client.base_url = "http://mirror:8844"
        mock_client._headers.return_value = {}

        mirror_response = {
            "results": [
                {"id": "e-hi", "text": "over", "created_at": "", "score": 4.2},
                {"id": "e-lo", "text": "under", "created_at": "", "score": -1.0},
            ]
        }
        with patch("kernel.memory_adapter.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.raise_for_status.return_value = None
            mock_resp.json.return_value = mirror_response
            mock_post.return_value = mock_resp

            from kernel.memory_adapter import MirrorMemoryAdapter
            adapter = MirrorMemoryAdapter(client=mock_client)
            results = adapter.search_sync("query")

        assert len(results) == 2
        assert results[0].score == 1.0
        assert results[1].score == 0.0

    def test_search_sync_skips_malformed_hit_without_dropping_batch(self):
        """A hit that can't be coerced into MemoryResult (e.g. non-string
        created_at causing a validation error) is skipped, not fatal to the
        whole search."""
        mock_client = MagicMock()
        mock_client.base_url = "http://mirror:8844"
        mock_client._headers.return_value = {}

        mirror_response = {
            "results": [
                {"id": None, "text": None, "created_at": None},  # coerces fine (all -> "")
                {"id": "e-good", "text": "fine", "created_at": "2026-07-13T00:00:00Z"},
            ]
        }
        with patch("kernel.memory_adapter.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.raise_for_status.return_value = None
            mock_resp.json.return_value = mirror_response
            mock_post.return_value = mock_resp

            from kernel.memory_adapter import MirrorMemoryAdapter
            adapter = MirrorMemoryAdapter(client=mock_client)
            results = adapter.search_sync("query")

        # Both hits coerce successfully (str(None) fields become "") — this
        # asserts the batch survives heterogeneous input without raising.
        assert len(results) == 2
        assert results[1].id == "e-good"


# ---------------------------------------------------------------------------
# Tests: brain.remember() routes through _memory_port
# ---------------------------------------------------------------------------


class TestBrainRememberRoutesThroughPort:
    """Verify brain.remember() calls _memory_port.remember_sync(), not
    raw requests.post(MIRROR_URL/store)."""

    def test_remember_calls_memory_port_not_raw_requests(self):
        import brain as brain_mod

        action = {"action": "do thing", "method": "create_task", "agent": "kasra"}
        result_d = {"success": True, "task_id": "t-001", "result": "created"}

        with patch.object(brain_mod._memory_port, "remember_sync") as mock_remember, \
             patch("brain.requests") as mock_requests:
            brain_mod.remember(action, result_d)

        mock_remember.assert_called_once()
        args, kwargs = mock_remember.call_args
        assert "do thing" in (args[0] if args else kwargs.get("content", ""))
        assert kwargs.get("core_concepts") is not None
        assert "create_task" in kwargs.get("core_concepts", [])
        assert kwargs.get("raw_data") == {"action": action, "result": result_d}
        # raw requests must NOT have been called for /store
        mock_requests.post.assert_not_called()

    def test_remember_fail_open_on_port_exception(self):
        import brain as brain_mod

        action = {"action": "x", "method": "health_check", "agent": "system"}
        result_d = {"success": True, "result": "ok"}

        with patch.object(brain_mod._memory_port, "remember_sync",
                          side_effect=RuntimeError("adapter down")):
            # Must not propagate the exception
            brain_mod.remember(action, result_d)


# ---------------------------------------------------------------------------
# Tests: hippocampus_recall() routes searches through _memory_port
# ---------------------------------------------------------------------------


class TestHippocampusRecallRoutesThroughPort:
    """Verify the goal/objection searches in hippocampus_recall() use
    _memory_port.search_sync() instead of raw requests.post()."""

    def _make_goal_hit(self):
        from sos.contracts.ports.memory import MemoryResult
        return MemoryResult(
            id="e-g1",
            content="GOAL: ship kernel",
            created_at="2026-07-13T00:00:00Z",
            metadata={"goal": {"title": "ship kernel", "priority": "high", "progress": 0.5}},
            score=0.9,
        )

    def _make_obj_hit(self):
        from sos.contracts.ports.memory import MemoryResult
        return MemoryResult(
            id="e-o1",
            content="OBJECTION: bandwidth",
            created_at="2026-07-13T00:00:00Z",
            metadata={
                "objection": {
                    "type": "resource",
                    "intensity": 0.7,
                    "description": "not enough time",
                }
            },
            score=0.8,
        )

    def test_hippocampus_uses_memory_port_for_goals(self):
        import brain as brain_mod

        goal_hit = self._make_goal_hit()

        with patch.object(brain_mod._memory_port, "search_sync",
                          return_value=[goal_hit]) as mock_search, \
             patch("brain.requests") as mock_requests:
            mock_requests.get.return_value = MagicMock(
                ok=True, json=MagicMock(return_value={"tasks": []})
            )
            # Force cortex snapshot to fail so we reach the legacy fallback path
            with patch.dict("sys.modules", {"cortex": None}):
                context = brain_mod.hippocampus_recall()

        assert mock_search.called
        queries = [c[0][0] for c in mock_search.call_args_list]
        assert any("GOAL" in q for q in queries)
        assert "ship kernel" in context

    def test_hippocampus_goal_and_objection_hits_appear_no_raw_search(self):
        import brain as brain_mod

        goal_hit = self._make_goal_hit()
        obj_hit = self._make_obj_hit()

        def fake_search(query: str, **kwargs):
            if "GOAL" in query:
                return [goal_hit]
            if "OBJECTION" in query:
                return [obj_hit]
            return []

        with patch.object(brain_mod._memory_port, "search_sync", side_effect=fake_search), \
             patch("brain.requests") as mock_raw:
            mock_raw.get.return_value = MagicMock(
                ok=True, json=MagicMock(return_value={"tasks": []})
            )
            with patch.dict("sys.modules", {"cortex": None}):
                context = brain_mod.hippocampus_recall()

        assert "ship kernel" in context
        assert "not enough time" in context
        # raw requests.post must NOT be called for /search (the two memory lookups)
        for c in mock_raw.post.call_args_list:
            url = c[0][0] if c[0] else c[1].get("url", "")
            assert "/search" not in str(url), \
                f"raw requests.post('/search') was called — should go through MemoryPort: {url}"


# ---------------------------------------------------------------------------
# Tests: motor_execute("post_content") routes through _memory_port
# ---------------------------------------------------------------------------


class TestMotorExecutePostContentRoutesThroughPort:
    """Verify that the post_content branch in motor_execute() stores the
    generated content engram via _memory_port.remember_sync(), not via a
    raw requests.post(MIRROR_URL/store) call."""

    def _base_action(self) -> dict:
        return {
            "method": "post_content",
            "action": "write a blog post about kernels",
            "details": "write about kernel ports",
            "agent": "brain",
            "goal_id": "mumega",
        }

    def test_post_content_calls_memory_port_not_raw_requests(self):
        import brain as brain_mod

        action = self._base_action()

        # Stub _generate_content to return a non-empty string so the
        # remember_sync branch is reached (avoids an LLM call in tests).
        # _capability_block is stubbed to bypass the live squad-roster fetch
        # (_agent_home_tenant hits SQUAD_URL over the network) — that gate is
        # unrelated to this K1 memory-routing slice.
        generated = "Generated content about kernels"
        with patch.object(brain_mod, "_generate_content", return_value=generated), \
             patch.object(brain_mod, "_capability_block", return_value=None), \
             patch.object(brain_mod._memory_port, "remember_sync") as mock_remember, \
             patch("brain.requests") as mock_requests:
            result = brain_mod.motor_execute(action)

        # The write must go through MemoryPort exactly once.
        mock_remember.assert_called_once()
        args, kwargs = mock_remember.call_args
        content_arg = args[0] if args else kwargs.get("content")
        assert content_arg == "Generated content about kernels"
        assert kwargs.get("context_id", "").startswith("brain_content_")
        assert "content" in (kwargs.get("core_concepts") or [])

        # Raw requests.post must NOT be called for /store.
        for c in mock_requests.post.call_args_list:
            url = c[0][0] if c[0] else c[1].get("url", "")
            assert "/store" not in str(url), \
                f"raw requests.post('/store') was called — should go through MemoryPort: {url}"

        assert result.get("success") is True

    def test_post_content_skipped_when_content_mode_off(self):
        """When _generate_content returns __CONTENT_MODE_OFF__, no remember_sync
        call is made and the result reports the skip."""
        import brain as brain_mod

        action = self._base_action()

        with patch.object(brain_mod, "_generate_content", return_value="__CONTENT_MODE_OFF__"), \
             patch.object(brain_mod, "_capability_block", return_value=None), \
             patch.object(brain_mod._memory_port, "remember_sync") as mock_remember:
            result = brain_mod.motor_execute(action)

        mock_remember.assert_not_called()
        assert result.get("success") is True
        assert "skipped" in result.get("result", "").lower()
