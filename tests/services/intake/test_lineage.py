"""
A6 lineage walker tests.

Gate: A6 (Sprint 003 Track B)

Tests verify:
  - Single-pass result: one PassTrace with role='primary', outcome='accepted'
  - Escalated result: two PassTraces, primary outcome='fallback', escalation='accepted'
  - Escalation parse-failed: fallback outcome on escalation pass
  - Empty run_log: empty passes, witness explains no model passes
  - WitnessStatement four questions all populated
  - walk() never raises
  - walk_engram() returns None for missing engram_id (DB mocked)
  - walk_engram() reconstructs run_log from JSONB and returns LineageReport
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from sos.services.intake.classifier import (
    ClassificationResult,
    ClassifierRunRecord,
    Commitment,
    Opportunity,
    Participant,
)
from sos.services.intake.conformal import wrap
from sos.services.intake.lineage import (
    LineageReport,
    PassTrace,
    WitnessStatement,
    walk,
    walk_engram,
)

FLASH_LITE = 'gemini-2.5-flash-lite'
FLASH = 'gemini-2.5-flash'


def _record(
    pass_number: int = 1,
    model: str = FLASH_LITE,
    confidence: float = 0.85,
    escalated: bool = False,
    parse_error: str | None = None,
) -> ClassifierRunRecord:
    return ClassifierRunRecord(
        pass_number=pass_number,
        model=model,
        billing_path='vertex-adc',
        confidence=confidence,
        escalated=escalated,
        latency_ms=200,
        input_tokens=100,
        output_tokens=50,
        cost_cents=1,
        parse_error=parse_error,
    )


def _cr(
    run_log: list[ClassifierRunRecord] | None = None,
    participants: list[Participant] | None = None,
    commitments: list[Commitment] | None = None,
    opportunities: list[Opportunity] | None = None,
    error: str | None = None,
) -> ClassificationResult:
    return ClassificationResult(
        participants=participants or [],
        commitments=commitments or [],
        opportunities=opportunities or [],
        run_log=run_log or [],
        error=error,
    )


# ── PassTrace construction ─────────────────────────────────────────────────────


class TestPassTraces:
    def test_single_pass_accepted(self) -> None:
        cr = wrap(_cr(run_log=[_record(confidence=0.85)]))
        report = walk(cr)
        assert len(report.passes) == 1
        p = report.passes[0]
        assert p.role == 'primary'
        assert p.outcome == 'accepted'
        assert p.model == FLASH_LITE

    def test_escalated_two_passes(self) -> None:
        log = [
            _record(pass_number=1, confidence=0.45),
            _record(pass_number=2, model=FLASH, confidence=0.82, escalated=True),
        ]
        cr = wrap(_cr(run_log=log))
        report = walk(cr)
        assert len(report.passes) == 2
        assert report.passes[0].role == 'primary'
        assert report.passes[0].outcome == 'fallback'
        assert report.passes[1].role == 'escalation'
        assert report.passes[1].outcome == 'accepted'

    def test_escalation_parse_failed(self) -> None:
        log = [
            _record(pass_number=1, confidence=0.3),
            _record(pass_number=2, model=FLASH, confidence=0.3, parse_error='bad JSON'),
        ]
        cr = wrap(_cr(run_log=log))
        report = walk(cr)
        assert report.passes[1].outcome == 'parse_failed'

    def test_empty_run_log_produces_empty_passes(self) -> None:
        cr = wrap(_cr(run_log=[]))
        report = walk(cr)
        assert report.passes == []


# ── WitnessStatement ───────────────────────────────────────────────────────────


class TestWitnessStatement:
    def test_four_fields_populated(self) -> None:
        cr = wrap(_cr(run_log=[_record()]))
        report = walk(cr, transcript_length=500)
        w = report.witness
        assert w.what_was_seen
        assert w.what_path_was_taken
        assert w.what_was_extracted
        assert w.confidence_assessment

    def test_transcript_length_in_what_was_seen(self) -> None:
        cr = wrap(_cr(run_log=[_record()]))
        report = walk(cr, transcript_length=1234)
        assert '1234' in report.witness.what_was_seen

    def test_single_pass_path_description(self) -> None:
        cr = wrap(_cr(run_log=[_record(model=FLASH_LITE)]))
        report = walk(cr)
        assert FLASH_LITE in report.witness.what_path_was_taken
        assert 'Single-pass' in report.witness.what_path_was_taken

    def test_escalated_path_mentions_both_models(self) -> None:
        log = [_record(pass_number=1, confidence=0.4), _record(pass_number=2, model=FLASH, confidence=0.8)]
        cr = wrap(_cr(run_log=log))
        report = walk(cr)
        assert FLASH_LITE in report.witness.what_path_was_taken
        assert FLASH in report.witness.what_path_was_taken

    def test_extracted_facts_summary(self) -> None:
        participants = [Participant(name='Alice'), Participant(name='Bob')]
        commitments = [Commitment(owner='Alice', action='send report')]
        cr = wrap(_cr(
            run_log=[_record()],
            participants=participants,
            commitments=commitments,
        ))
        report = walk(cr)
        assert '2' in report.witness.what_was_extracted   # 2 participants
        assert '1' in report.witness.what_was_extracted   # 1 commitment

    def test_error_result_describes_failure(self) -> None:
        cr = wrap(_cr(run_log=[], error='quota exceeded'))
        report = walk(cr)
        assert 'failed' in report.witness.what_was_extracted.lower()

    def test_confidence_assessment_contains_kappa(self) -> None:
        cr = wrap(_cr(run_log=[_record(confidence=0.9)]))
        report = walk(cr)
        assert 'κ' in report.witness.confidence_assessment

    def test_violations_appear_in_confidence_assessment(self) -> None:
        participants = [Participant(name='Alice')]
        commitments = [Commitment(owner='UnknownPerson', action='do thing')]
        cr = wrap(_cr(run_log=[_record()], participants=participants, commitments=commitments))
        report = walk(cr)
        assert 'violation' in report.witness.confidence_assessment

    def test_no_violations_stated_when_clean(self) -> None:
        participants = [Participant(name='Alice')]
        commitments = [Commitment(owner='Alice', action='do thing')]
        cr = wrap(_cr(run_log=[_record()], participants=participants, commitments=commitments))
        report = walk(cr)
        assert 'No coherence violations' in report.witness.confidence_assessment


# ── walk() misc ────────────────────────────────────────────────────────────────


class TestWalk:
    def test_engram_id_stored(self) -> None:
        cr = wrap(_cr(run_log=[_record()]))
        report = walk(cr, engram_id='eng-abc123')
        assert report.engram_id == 'eng-abc123'

    def test_never_raises(self) -> None:
        cr = wrap(ClassificationResult(error='boom', run_log=[]))
        report = walk(cr)
        assert isinstance(report, LineageReport)


# ── walk_engram() ──────────────────────────────────────────────────────────────


class TestWalkEngram:
    def test_returns_none_for_missing_engram(self) -> None:
        conn = MagicMock()
        conn.cursor.return_value.__enter__ = lambda s: s
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        conn.cursor.return_value.fetchone.return_value = None

        result = walk_engram('nonexistent', conn)
        assert result is None

    def test_returns_none_when_no_run_log(self) -> None:
        conn = MagicMock()
        conn.cursor.return_value.__enter__ = lambda s: s
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        conn.cursor.return_value.fetchone.return_value = {'classifier_run_log': None}

        result = walk_engram('eng-123', conn)
        assert result is None

    def test_reconstructs_from_jsonb(self) -> None:
        run_log_data = [
            {
                'pass_number': 1,
                'model': FLASH_LITE,
                'billing_path': 'vertex-adc',
                'confidence': 0.88,
                'escalated': False,
                'latency_ms': 300,
                'input_tokens': 200,
                'output_tokens': 80,
                'cost_cents': 2,
                'parse_error': None,
            }
        ]
        conn = MagicMock()
        conn.cursor.return_value.__enter__ = lambda s: s
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        conn.cursor.return_value.fetchone.return_value = {'classifier_run_log': run_log_data}

        report = walk_engram('eng-456', conn)
        assert report is not None
        assert report.engram_id == 'eng-456'
        assert len(report.passes) == 1
        assert report.passes[0].model == FLASH_LITE
        assert report.passes[0].confidence == pytest.approx(0.88)
        assert report.witness.what_path_was_taken  # non-empty
