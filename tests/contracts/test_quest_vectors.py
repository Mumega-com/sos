"""
§16 Quest Vectors tests — Sprint 004 A.4.

Unit tests: dimension schema, prompt building, response parsing, vector operations.
Integration tests (requires DB): upsert_manual, get_vector, extract (mocked Vertex).

Run all:     DATABASE_URL=... pytest tests/contracts/test_quest_vectors.py -v
Run unit:    pytest tests/contracts/test_quest_vectors.py -v -m "not db"
"""
from __future__ import annotations

import json
import os
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sos.contracts.quest_vectors import (
    DIMENSION_NAMES,
    _build_prompt,
    _named_dims_to_vector,
    _parse_response,
    get_vector,
    upsert_manual,
)


# ── helpers ────────────────────────────────────────────────────────────────────


def _has_db() -> bool:
    return bool(os.getenv('MIRROR_DATABASE_URL') or os.getenv('DATABASE_URL'))


def _uid() -> str:
    return f'test-qv-{uuid.uuid4().hex[:8]}'


db = pytest.mark.skipif(not _has_db(), reason='Mirror DB not configured')


# ── Unit: DIMENSION_NAMES schema ──────────────────────────────────────────────


class TestDimensionNames:
    def test_exactly_16(self) -> None:
        assert len(DIMENSION_NAMES) == 16

    def test_all_unique(self) -> None:
        assert len(set(DIMENSION_NAMES)) == 16

    def test_all_strings(self) -> None:
        for name in DIMENSION_NAMES:
            assert isinstance(name, str) and name

    def test_no_spaces(self) -> None:
        """Dimension names use underscores, not spaces (JSON key-safe)."""
        for name in DIMENSION_NAMES:
            assert ' ' not in name

    def test_canonical_names_present(self) -> None:
        """Core expected dimensions exist."""
        for expected in ('technical_depth', 'reliability', 'compliance', 'innovation'):
            assert expected in DIMENSION_NAMES

    def test_order_is_stable(self) -> None:
        """Order must be deterministic — vector index maps to dimension."""
        assert DIMENSION_NAMES[0] == 'technical_depth'
        assert DIMENSION_NAMES[15] == 'innovation'


# ── Unit: _parse_response ─────────────────────────────────────────────────────


class TestParseResponse:
    def _full_json(self, value: float = 0.5) -> str:
        return json.dumps({name: value for name in DIMENSION_NAMES})

    def test_valid_json_parsed(self) -> None:
        result = _parse_response(self._full_json(0.7))
        assert len(result) == 16
        for name in DIMENSION_NAMES:
            assert abs(result[name] - 0.7) < 1e-9

    def test_clamped_above_one(self) -> None:
        data = {name: 1.5 for name in DIMENSION_NAMES}
        result = _parse_response(json.dumps(data))
        for name in DIMENSION_NAMES:
            assert result[name] == 1.0

    def test_clamped_below_zero(self) -> None:
        data = {name: -0.3 for name in DIMENSION_NAMES}
        result = _parse_response(json.dumps(data))
        for name in DIMENSION_NAMES:
            assert result[name] == 0.0

    def test_missing_dimension_defaults_to_half(self) -> None:
        data = {name: 0.6 for name in DIMENSION_NAMES[:-1]}  # omit last
        result = _parse_response(json.dumps(data))
        assert result[DIMENSION_NAMES[-1]] == 0.5  # defaulted

    def test_strips_markdown_fences(self) -> None:
        fenced = '```json\n' + self._full_json(0.4) + '\n```'
        result = _parse_response(fenced)
        assert len(result) == 16

    def test_invalid_json_raises(self) -> None:
        with pytest.raises(ValueError, match='non-JSON'):
            _parse_response('not valid json at all')

    def test_extra_keys_ignored(self) -> None:
        data = {name: 0.5 for name in DIMENSION_NAMES}
        data['extra_key'] = 0.9
        result = _parse_response(json.dumps(data))
        assert 'extra_key' not in result
        assert len(result) == 16


# ── Unit: _named_dims_to_vector ───────────────────────────────────────────────


class TestNamedDimsToVector:
    def test_ordered_correctly(self) -> None:
        named = {name: i * 0.0625 for i, name in enumerate(DIMENSION_NAMES)}
        vector = _named_dims_to_vector(named)
        assert len(vector) == 16
        for i, name in enumerate(DIMENSION_NAMES):
            assert abs(vector[i] - named[name]) < 1e-9

    def test_returns_list(self) -> None:
        named = {name: 0.5 for name in DIMENSION_NAMES}
        vector = _named_dims_to_vector(named)
        assert isinstance(vector, list)
        assert all(isinstance(v, float) for v in vector)


# ── Unit: _build_prompt ───────────────────────────────────────────────────────


class TestBuildPrompt:
    def test_contains_title(self) -> None:
        prompt = _build_prompt('Fix the auth bug', 'Description here')
        assert 'Fix the auth bug' in prompt

    def test_contains_all_dimensions(self) -> None:
        prompt = _build_prompt('Test quest', 'desc')
        for name in DIMENSION_NAMES:
            assert name in prompt

    def test_empty_description_handled(self) -> None:
        prompt = _build_prompt('Quest', '')
        assert 'no description' in prompt

    def test_none_description_handled(self) -> None:
        prompt = _build_prompt('Quest', None)  # type: ignore[arg-type]
        assert 'no description' in prompt


# ── Unit: upsert_manual validation ───────────────────────────────────────────


class TestUpsertManualValidation:
    def test_wrong_length_raises(self) -> None:
        with pytest.raises(ValueError, match='exactly 16'):
            upsert_manual('any-quest-id', [0.5] * 10)

    def test_too_long_raises(self) -> None:
        with pytest.raises(ValueError, match='exactly 16'):
            upsert_manual('any-quest-id', [0.5] * 17)


# ── Integration: DB-backed ────────────────────────────────────────────────────


@db
class TestUpsertManualDB:
    def _insert_quest(self) -> str:
        import psycopg2, psycopg2.extras
        conn = psycopg2.connect(
            os.getenv('MIRROR_DATABASE_URL') or os.getenv('DATABASE_URL'),
            cursor_factory=psycopg2.extras.RealDictCursor,
        )
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO quests (title, description, tier, created_by)
                       VALUES ('Test QV Quest', 'desc', 'T2', 'test')
                       RETURNING id""",
                )
                qid = cur.fetchone()['id']
        conn.close()
        return qid

    def test_upsert_and_retrieve(self) -> None:
        qid = self._insert_quest()
        vector = [float(i) / 16.0 for i in range(16)]

        result = upsert_manual(qid, vector)
        assert result['quest_id'] == qid
        assert result['source'] == 'manual'
        assert len(result['vector']) == 16

        retrieved = get_vector(qid)
        assert retrieved is not None
        assert retrieved['quest_id'] == qid
        assert retrieved['source'] == 'manual'
        assert len(retrieved['vector']) == 16
        for a, b in zip(vector, retrieved['vector']):
            assert abs(a - b) < 1e-6

    def test_values_clamped_on_upsert(self) -> None:
        qid = self._insert_quest()
        vector = [-0.5] * 8 + [1.5] * 8   # out of range
        upsert_manual(qid, vector)
        retrieved = get_vector(qid)
        for v in retrieved['vector'][:8]:
            assert v == 0.0
        for v in retrieved['vector'][8:]:
            assert v == 1.0

    def test_get_vector_missing_returns_none(self) -> None:
        result = get_vector('nonexistent-quest-id-xyz')
        assert result is None

    def test_manual_named_dims_is_null(self) -> None:
        """Manual upsert stores NULL named_dims."""
        qid = self._insert_quest()
        upsert_manual(qid, [0.5] * 16)
        retrieved = get_vector(qid)
        assert retrieved['named_dims'] is None

    def test_idempotent_upsert(self) -> None:
        """Second upsert overwrites first."""
        qid = self._insert_quest()
        upsert_manual(qid, [0.1] * 16)
        upsert_manual(qid, [0.9] * 16)
        retrieved = get_vector(qid)
        for v in retrieved['vector']:
            assert abs(v - 0.9) < 1e-6


@db
class TestExtractMocked:
    """
    Integration test for extract() with mocked Vertex call.
    Verifies the full DB write path without making real LLM calls.
    """

    def _insert_quest(self) -> str:
        import psycopg2, psycopg2.extras
        conn = psycopg2.connect(
            os.getenv('MIRROR_DATABASE_URL') or os.getenv('DATABASE_URL'),
            cursor_factory=psycopg2.extras.RealDictCursor,
        )
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO quests (title, description, tier, created_by)
                       VALUES ('Mocked Extract Quest', 'desc', 'T1', 'test')
                       RETURNING id""",
                )
                qid = cur.fetchone()['id']
        conn.close()
        return qid

    def test_extract_writes_to_db(self) -> None:
        from sos.contracts.quest_vectors import extract
        from sos.adapters.base import ExecutionResult, UsageInfo

        qid = self._insert_quest()

        # Mock LLM response: valid JSON with all 16 dimensions
        mock_scores = {name: 0.6 for name in DIMENSION_NAMES}
        mock_text = json.dumps(mock_scores)

        mock_result = ExecutionResult(
            text=mock_text,
            usage=UsageInfo(model='gemini-2.5-flash-lite', provider='google-vertex'),
            success=True,
        )

        # Patch at the source module since VertexGeminiAdapter is lazily imported inside extract()
        with patch('sos.adapters.vertex_gemini_adapter.VertexGeminiAdapter') as MockAdapter:
            instance = MockAdapter.return_value
            instance.execute = AsyncMock(return_value=mock_result)
            result = extract(qid)

        assert result['quest_id'] == qid
        assert result['source'] == 'auto-extracted'
        assert len(result['vector']) == 16
        assert all(abs(v - 0.6) < 1e-9 for v in result['vector'])
        assert result['named_dims'] == mock_scores

        stored = get_vector(qid)
        assert stored is not None
        assert stored['source'] == 'auto-extracted'
        assert stored['named_dims'] is not None
        assert stored['named_dims']['technical_depth'] == pytest.approx(0.6)

    def test_extract_vertex_failure_raises(self) -> None:
        from sos.contracts.quest_vectors import extract
        from sos.adapters.base import ExecutionResult, UsageInfo

        qid = self._insert_quest()

        mock_result = ExecutionResult(
            text='',
            usage=UsageInfo(model='gemini-2.5-flash-lite', provider='google-vertex'),
            success=False,
            error='quota exceeded',
        )

        with patch('sos.adapters.vertex_gemini_adapter.VertexGeminiAdapter') as MockAdapter:
            instance = MockAdapter.return_value
            instance.execute = AsyncMock(return_value=mock_result)
            with pytest.raises(RuntimeError, match='returned failure'):
                extract(qid)
