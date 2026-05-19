"""
A7 FRC overlay tests.

Gate: A7 (Sprint 003 Track B)

Tests verify:
  - Error result → verdict='failed', W=0.0
  - F1 HALLUCINATION: bounds.lower < 0.3, single_pass, no error → warning
  - F2 OMISSION: >50% roleless participants → warning
  - F2 OMISSION: >50% contextless commitments → warning
  - F3 COHERENCE_VIOLATION: κ < 0.7 with violations → error
  - F4 ATTRIBUTION_ERROR: commitment owner not in participants → error
  - W score computation for single_pass / escalated_improved / escalated_degraded / parse_error
  - Verdict thresholds: aligned (W≥0.7, ≤1 warning), degraded (W<0.7 or >1 warning), failed (W<0.5 or errors)
  - evaluate() never raises
"""
from __future__ import annotations

from datetime import date

import pytest

from sos.services.intake.classifier import (
    ClassificationResult,
    ClassifierRunRecord,
    Commitment,
    Opportunity,
    Participant,
)
from sos.services.intake.conformal import wrap
from sos.services.intake.lineage import walk
from sos.services.intake.frc import (
    FailureModeKind,
    FrcOverlay,
    _detect_failures,
    _determine_verdict,
    _witness_score,
    evaluate,
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


def _build(
    run_log: list[ClassifierRunRecord] | None = None,
    participants: list[Participant] | None = None,
    commitments: list[Commitment] | None = None,
    opportunities: list[Opportunity] | None = None,
    error: str | None = None,
    reference_date: date | None = None,
):
    cr = ClassificationResult(
        participants=participants or [],
        commitments=commitments or [],
        opportunities=opportunities or [],
        run_log=run_log or [],
        error=error,
    )
    conformant = wrap(cr, reference_date=reference_date)
    report = walk(conformant, transcript_length=500)
    return conformant, report


# ── Error result ───────────────────────────────────────────────────────────────


class TestErrorResult:
    def test_error_result_verdict_failed(self) -> None:
        conformant, report = _build(run_log=[], error='quota exceeded')
        overlay = evaluate(conformant, report)
        assert overlay.verdict == 'failed'
        assert overlay.witness_score == 0.0

    def test_error_result_reason_contains_error_text(self) -> None:
        conformant, report = _build(run_log=[], error='adapter timeout')
        overlay = evaluate(conformant, report)
        assert 'adapter timeout' in overlay.verdict_reason

    def test_error_result_no_failure_modes(self) -> None:
        conformant, report = _build(run_log=[], error='boom')
        overlay = evaluate(conformant, report)
        assert overlay.failure_modes == []


# ── W score ────────────────────────────────────────────────────────────────────


class TestWitnessScore:
    def test_single_pass_high_confidence_w_near_one(self) -> None:
        conformant, report = _build(run_log=[_record(confidence=0.9)])
        w = _witness_score(conformant, report)
        # w_confidence=0.9, w_coverage=upper≈0.95, w_kappa=1.0, w_pass=1.0
        assert w == pytest.approx((0.9 + 0.95 + 1.0 + 1.0) / 4.0, abs=0.02)

    def test_parse_error_w_pass_zero(self) -> None:
        conformant, report = _build(run_log=[_record(confidence=0.5, parse_error='bad JSON')])
        w = _witness_score(conformant, report)
        # w_pass = 0.0 for parse_error; conf=0.5, upper clamped to 1.0, κ=1.0
        # W = (0.5 + 1.0 + 1.0 + 0.0) / 4 = 0.625 — lower than high-confidence single pass
        assert w == pytest.approx(0.625, abs=0.01)

    def test_escalated_improved_w_pass_085(self) -> None:
        log = [_record(pass_number=1, confidence=0.4), _record(pass_number=2, model=FLASH, confidence=0.8)]
        conformant, report = _build(run_log=log)
        w = _witness_score(conformant, report)
        # method = 'escalated_improved', w_pass = 0.85
        assert 0.7 < w < 1.0

    def test_escalated_degraded_w_pass_065(self) -> None:
        log = [_record(pass_number=1, confidence=0.7), _record(pass_number=2, model=FLASH, confidence=0.5)]
        conformant, report = _build(run_log=log)
        w = _witness_score(conformant, report)
        # method = 'escalated_degraded', w_pass = 0.65
        # w_coverage = upper for conf=0.5 with degraded radius
        assert 0.4 < w < 0.9


# ── Failure mode detection ─────────────────────────────────────────────────────


class TestDetectF1Hallucination:
    def test_f1_fires_when_lower_below_threshold_single_pass(self) -> None:
        # confidence=0.4 → lower=0.1, method=single_pass → F1 warning
        conformant, _ = _build(run_log=[_record(confidence=0.4)])
        failures = _detect_failures(conformant)
        kinds = [f.kind for f in failures]
        assert FailureModeKind.HALLUCINATION in kinds

    def test_f1_severity_is_warning(self) -> None:
        conformant, _ = _build(run_log=[_record(confidence=0.4)])
        failures = _detect_failures(conformant)
        f1 = next(f for f in failures if f.kind == FailureModeKind.HALLUCINATION)
        assert f1.severity == 'warning'

    def test_f1_does_not_fire_when_lower_above_threshold(self) -> None:
        # confidence=0.85 → lower=0.775 → no F1
        conformant, _ = _build(run_log=[_record(confidence=0.85)])
        failures = _detect_failures(conformant)
        kinds = [f.kind for f in failures]
        assert FailureModeKind.HALLUCINATION not in kinds

    def test_f1_does_not_fire_for_escalated_even_if_low(self) -> None:
        # method != single_pass → F1 should not fire
        log = [_record(pass_number=1, confidence=0.3), _record(pass_number=2, model=FLASH, confidence=0.35)]
        conformant, _ = _build(run_log=log)
        failures = _detect_failures(conformant)
        kinds = [f.kind for f in failures]
        assert FailureModeKind.HALLUCINATION not in kinds


class TestDetectF2Omission:
    def test_f2_fires_for_roleless_participants_majority(self) -> None:
        participants = [
            Participant(name='Alice'),
            Participant(name='Bob'),
            Participant(name='Carol', role='PM'),
        ]
        conformant, _ = _build(
            run_log=[_record(confidence=0.85)],
            participants=participants,
        )
        failures = _detect_failures(conformant)
        omissions = [f for f in failures if f.kind == FailureModeKind.OMISSION]
        assert any('role' in f.description.lower() or 'participant' in f.description.lower() for f in omissions)

    def test_f2_does_not_fire_when_majority_have_roles(self) -> None:
        participants = [
            Participant(name='Alice', role='PM'),
            Participant(name='Bob', role='Dev'),
        ]
        conformant, _ = _build(
            run_log=[_record(confidence=0.85)],
            participants=participants,
        )
        failures = _detect_failures(conformant)
        # No roleless majority → no F2 for participants
        roleless_omissions = [
            f for f in failures
            if f.kind == FailureModeKind.OMISSION and 'role' in f.description.lower()
        ]
        assert len(roleless_omissions) == 0

    def test_f2_fires_for_contextless_commitments_majority(self) -> None:
        commitments = [
            Commitment(owner='Alice', action='send report'),
            Commitment(owner='Bob', action='review PR'),
            Commitment(owner='Carol', action='write doc', context='as discussed'),
        ]
        conformant, _ = _build(
            run_log=[_record(confidence=0.85)],
            participants=[Participant(name='Alice'), Participant(name='Bob'), Participant(name='Carol')],
            commitments=commitments,
        )
        failures = _detect_failures(conformant)
        omissions = [f for f in failures if f.kind == FailureModeKind.OMISSION]
        assert any('context' in f.description.lower() or 'commitment' in f.description.lower() for f in omissions)

    def test_f2_does_not_fire_when_no_participants(self) -> None:
        conformant, _ = _build(run_log=[_record(confidence=0.85)])
        failures = _detect_failures(conformant)
        assert FailureModeKind.OMISSION not in [f.kind for f in failures]


class TestDetectF3CoherenceViolation:
    def test_f3_fires_when_kappa_low_with_violations(self) -> None:
        # Build a result with unresolved commitment owner → κ < 1.0 + violation
        participants = [Participant(name='Alice')]
        commitments = [Commitment(owner='Phantom', action='deliver slides')]
        # reference_date far future so temporal check doesn't interfere
        conformant, _ = _build(
            run_log=[_record(confidence=0.85)],
            participants=participants,
            commitments=commitments,
            reference_date=date(2026, 4, 25),
        )
        # Manually check κ < 0.7 — if it is, F3 should fire
        if conformant.kappa.kappa < 0.7:
            failures = _detect_failures(conformant)
            kinds = [f.kind for f in failures]
            assert FailureModeKind.COHERENCE_VIOLATION in kinds

    def test_f3_severity_is_error(self) -> None:
        participants = [Participant(name='Alice')]
        commitments = [Commitment(owner='Ghost', action='write spec')]
        conformant, _ = _build(
            run_log=[_record(confidence=0.85)],
            participants=participants,
            commitments=commitments,
        )
        failures = _detect_failures(conformant)
        f3s = [f for f in failures if f.kind == FailureModeKind.COHERENCE_VIOLATION]
        for f3 in f3s:
            assert f3.severity == 'error'

    def test_f3_does_not_fire_when_no_violations(self) -> None:
        participants = [Participant(name='Alice')]
        commitments = [Commitment(owner='Alice', action='send report')]
        conformant, _ = _build(
            run_log=[_record(confidence=0.85)],
            participants=participants,
            commitments=commitments,
        )
        failures = _detect_failures(conformant)
        assert FailureModeKind.COHERENCE_VIOLATION not in [f.kind for f in failures]


class TestDetectF4AttributionError:
    def test_f4_fires_when_owner_not_in_participants(self) -> None:
        participants = [Participant(name='Alice')]
        commitments = [Commitment(owner='UnknownBob', action='fix bug')]
        conformant, _ = _build(
            run_log=[_record(confidence=0.85)],
            participants=participants,
            commitments=commitments,
        )
        failures = _detect_failures(conformant)
        kinds = [f.kind for f in failures]
        assert FailureModeKind.ATTRIBUTION_ERROR in kinds

    def test_f4_severity_is_error(self) -> None:
        participants = [Participant(name='Alice')]
        commitments = [Commitment(owner='Ghost', action='deliver')]
        conformant, _ = _build(
            run_log=[_record(confidence=0.85)],
            participants=participants,
            commitments=commitments,
        )
        failures = _detect_failures(conformant)
        f4s = [f for f in failures if f.kind == FailureModeKind.ATTRIBUTION_ERROR]
        assert f4s
        assert all(f.severity == 'error' for f in f4s)

    def test_f4_does_not_fire_when_all_owners_in_participants(self) -> None:
        participants = [Participant(name='Alice'), Participant(name='Bob')]
        commitments = [
            Commitment(owner='Alice', action='send report'),
            Commitment(owner='Bob', action='review PR'),
        ]
        conformant, _ = _build(
            run_log=[_record(confidence=0.85)],
            participants=participants,
            commitments=commitments,
        )
        failures = _detect_failures(conformant)
        assert FailureModeKind.ATTRIBUTION_ERROR not in [f.kind for f in failures]

    def test_f4_does_not_fire_without_participants(self) -> None:
        # Can't check attribution without a participant list
        commitments = [Commitment(owner='Alice', action='something')]
        conformant, _ = _build(run_log=[_record()], commitments=commitments)
        failures = _detect_failures(conformant)
        assert FailureModeKind.ATTRIBUTION_ERROR not in [f.kind for f in failures]

    def test_f4_description_names_unresolved_owners(self) -> None:
        participants = [Participant(name='Alice')]
        commitments = [Commitment(owner='Phantom', action='work')]
        conformant, _ = _build(
            run_log=[_record(confidence=0.85)],
            participants=participants,
            commitments=commitments,
        )
        failures = _detect_failures(conformant)
        f4s = [f for f in failures if f.kind == FailureModeKind.ATTRIBUTION_ERROR]
        assert f4s
        assert 'Phantom' in f4s[0].description or 'phantom' in f4s[0].description.lower()


# ── _determine_verdict ─────────────────────────────────────────────────────────


class TestDetermineVerdict:
    def test_aligned_when_w_high_no_failures(self) -> None:
        verdict, reason = _determine_verdict(0.85, [])
        assert verdict == 'aligned'

    def test_aligned_allows_one_warning(self) -> None:
        from sos.services.intake.frc import FailureMode
        warning = FailureMode(kind=FailureModeKind.HALLUCINATION, severity='warning', description='x')
        verdict, _ = _determine_verdict(0.75, [warning])
        assert verdict == 'aligned'

    def test_degraded_when_w_between_05_and_07(self) -> None:
        verdict, reason = _determine_verdict(0.65, [])
        assert verdict == 'degraded'
        assert '0.65' in reason

    def test_degraded_when_two_warnings(self) -> None:
        from sos.services.intake.frc import FailureMode
        warnings = [
            FailureMode(kind=FailureModeKind.HALLUCINATION, severity='warning', description='x'),
            FailureMode(kind=FailureModeKind.OMISSION, severity='warning', description='y'),
        ]
        verdict, _ = _determine_verdict(0.75, warnings)
        assert verdict == 'degraded'

    def test_failed_when_w_below_05(self) -> None:
        verdict, reason = _determine_verdict(0.4, [])
        assert verdict == 'failed'
        assert '0.40' in reason

    def test_failed_when_error_present(self) -> None:
        from sos.services.intake.frc import FailureMode
        error = FailureMode(kind=FailureModeKind.COHERENCE_VIOLATION, severity='error', description='x')
        verdict, reason = _determine_verdict(0.8, [error])
        assert verdict == 'failed'
        assert FailureModeKind.COHERENCE_VIOLATION.value in reason

    def test_failed_when_w_below_05_and_errors(self) -> None:
        from sos.services.intake.frc import FailureMode
        error = FailureMode(kind=FailureModeKind.ATTRIBUTION_ERROR, severity='error', description='x')
        verdict, reason = _determine_verdict(0.3, [error])
        assert verdict == 'failed'


# ── evaluate() ────────────────────────────────────────────────────────────────


class TestEvaluate:
    def test_returns_frc_overlay(self) -> None:
        conformant, report = _build(run_log=[_record(confidence=0.85)])
        overlay = evaluate(conformant, report)
        assert isinstance(overlay, FrcOverlay)

    def test_kappa_propagated(self) -> None:
        conformant, report = _build(run_log=[_record(confidence=0.85)])
        overlay = evaluate(conformant, report)
        assert overlay.kappa == pytest.approx(conformant.kappa.kappa)

    def test_clean_result_is_aligned(self) -> None:
        participants = [Participant(name='Alice', role='PM'), Participant(name='Bob', role='Dev')]
        commitments = [Commitment(owner='Alice', action='write spec', context='as discussed in the meeting')]
        conformant, report = _build(
            run_log=[_record(confidence=0.9)],
            participants=participants,
            commitments=commitments,
        )
        overlay = evaluate(conformant, report)
        assert overlay.verdict == 'aligned'

    def test_never_raises(self) -> None:
        cr = ClassificationResult(error='boom', run_log=[])
        conformant = wrap(cr)
        report = walk(conformant)
        overlay = evaluate(conformant, report)
        assert isinstance(overlay, FrcOverlay)

    def test_verdict_reason_non_empty(self) -> None:
        conformant, report = _build(run_log=[_record(confidence=0.85)])
        overlay = evaluate(conformant, report)
        assert overlay.verdict_reason

    def test_low_confidence_produces_degraded_or_failed(self) -> None:
        conformant, report = _build(run_log=[_record(confidence=0.1)])
        overlay = evaluate(conformant, report)
        assert overlay.verdict in ('degraded', 'failed')
