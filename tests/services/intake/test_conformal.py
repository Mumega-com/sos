"""
A5 conformal wrapper tests.

Gate: A5 (Sprint 003 Track B)

Tests verify:
  - Single-pass calibration: radius = (1 - conf) * 0.5
  - Escalated-improved: radius shrinks 25%
  - Escalated-degraded: radius widens 10%
  - Parse error: radius = 0.5 regardless of confidence
  - Empty run_log: treated as parse_error
  - Bounds clamped to [0, 1]
  - κ participant coverage: owner/involved names checked against participants
  - κ temporal consistency: past due_dates penalised
  - κ signal diversity: low diversity flagged
  - κ = 1.0 when no checks can fire (empty result)
  - wrap() never raises; error results get high-uncertainty bounds
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
from sos.services.intake.conformal import (
    ConformantResult,
    KappaScore,
    UncertaintyBounds,
    _calibrate,
    _compute_kappa,
    wrap,
)

# ── Fixtures ───────────────────────────────────────────────────────────────────

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


def _result(
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


# ── _calibrate ─────────────────────────────────────────────────────────────────


class TestCalibrate:
    def test_single_pass_high_confidence(self) -> None:
        bounds = _calibrate([_record(confidence=0.9)])
        assert bounds.method == 'single_pass'
        assert bounds.calibrated_confidence == pytest.approx(0.9)
        assert bounds.uncertainty_radius == pytest.approx(0.05)  # (1-0.9)*0.5
        assert bounds.lower == pytest.approx(0.85)
        assert bounds.upper == pytest.approx(0.95)

    def test_single_pass_low_confidence(self) -> None:
        bounds = _calibrate([_record(confidence=0.4)])
        assert bounds.method == 'single_pass'
        assert bounds.uncertainty_radius == pytest.approx(0.3)  # (1-0.4)*0.5
        assert bounds.lower == pytest.approx(0.1)
        assert bounds.upper == pytest.approx(0.7)

    def test_escalated_improved(self) -> None:
        """Second pass confidence >= first → radius shrinks 25%."""
        log = [_record(pass_number=1, confidence=0.45), _record(pass_number=2, model=FLASH, confidence=0.80)]
        bounds = _calibrate(log)
        assert bounds.method == 'escalated_improved'
        base_radius = (1 - 0.80) * 0.5   # 0.10
        assert bounds.uncertainty_radius == pytest.approx(base_radius * 0.75)
        assert bounds.calibrated_confidence == pytest.approx(0.80)

    def test_escalated_degraded(self) -> None:
        """Second pass confidence < first → radius widens 10%."""
        log = [_record(pass_number=1, confidence=0.55), _record(pass_number=2, model=FLASH, confidence=0.40)]
        bounds = _calibrate(log)
        assert bounds.method == 'escalated_degraded'
        base_radius = (1 - 0.40) * 0.5   # 0.30
        assert bounds.uncertainty_radius == pytest.approx(base_radius * 1.10)

    def test_parse_error_forces_half_radius(self) -> None:
        log = [_record(confidence=0.85, parse_error='JSON parse error: ...')]
        bounds = _calibrate(log)
        assert bounds.method == 'parse_error'
        assert bounds.uncertainty_radius == pytest.approx(0.5)

    def test_empty_run_log(self) -> None:
        bounds = _calibrate([])
        assert bounds.method == 'parse_error'
        assert bounds.calibrated_confidence == pytest.approx(0.0)
        assert bounds.uncertainty_radius == pytest.approx(0.5)

    def test_bounds_clamped_to_unit_interval(self) -> None:
        bounds = _calibrate([_record(confidence=0.05)])
        assert bounds.lower >= 0.0
        assert bounds.upper <= 1.0

    def test_upper_clamped_at_one(self) -> None:
        bounds = _calibrate([_record(confidence=0.99)])
        assert bounds.upper <= 1.0


# ── _compute_kappa ─────────────────────────────────────────────────────────────


class TestComputeKappa:
    def test_empty_result_kappa_is_one(self) -> None:
        k = _compute_kappa(_result())
        assert k.kappa == pytest.approx(1.0)
        assert k.violations == []

    def test_participant_coverage_all_present(self) -> None:
        participants = [Participant(name='Alice'), Participant(name='Bob')]
        commitments = [Commitment(owner='Alice', action='send report')]
        opps = [Opportunity(signal='lead', description='big deal', involved=['Bob'])]
        k = _compute_kappa(_result(participants=participants, commitments=commitments, opportunities=opps))
        assert k.participant_coverage == pytest.approx(1.0)
        assert k.kappa == pytest.approx(1.0)

    def test_participant_coverage_missing_name(self) -> None:
        participants = [Participant(name='Alice')]
        commitments = [Commitment(owner='Charlie', action='send proposal')]
        k = _compute_kappa(_result(participants=participants, commitments=commitments))
        assert k.participant_coverage is not None
        assert k.participant_coverage < 1.0
        assert k.kappa < 1.0
        assert any('participant_coverage' in v for v in k.violations)

    def test_temporal_consistency_future_date(self) -> None:
        commitments = [Commitment(owner='A', action='do thing', due_date='2099-12-31')]
        k = _compute_kappa(_result(commitments=commitments), reference_date=date(2026, 4, 25))
        assert k.temporal_consistency == pytest.approx(1.0)

    def test_temporal_consistency_past_date(self) -> None:
        commitments = [Commitment(owner='A', action='do thing', due_date='2020-01-01')]
        k = _compute_kappa(_result(commitments=commitments), reference_date=date(2026, 4, 25))
        assert k.temporal_consistency == pytest.approx(0.0)
        assert any('temporal_consistency' in v for v in k.violations)

    def test_temporal_consistency_not_checked_without_due_dates(self) -> None:
        commitments = [Commitment(owner='A', action='do thing')]
        k = _compute_kappa(_result(commitments=commitments))
        assert k.temporal_consistency is None

    def test_signal_diversity_varied(self) -> None:
        opps = [
            Opportunity(signal='lead', description='a'),
            Opportunity(signal='referral', description='b'),
        ]
        k = _compute_kappa(_result(opportunities=opps))
        assert k.signal_diversity == pytest.approx(1.0)

    def test_signal_diversity_uniform(self) -> None:
        opps = [
            Opportunity(signal='lead', description='a'),
            Opportunity(signal='lead', description='b'),
            Opportunity(signal='lead', description='c'),
        ]
        k = _compute_kappa(_result(opportunities=opps))
        assert k.signal_diversity is not None
        assert k.signal_diversity < 0.5
        assert any('signal_diversity' in v for v in k.violations)

    def test_kappa_is_mean_of_firing_checks(self) -> None:
        """All three checks fire; verify kappa = their mean."""
        participants = [Participant(name='Alice')]
        commitments = [Commitment(owner='Alice', action='thing', due_date='2099-12-31')]
        opps = [Opportunity(signal='lead', description='x', involved=['Alice'])]
        k = _compute_kappa(
            _result(participants=participants, commitments=commitments, opportunities=opps),
            reference_date=date(2026, 4, 25),
        )
        # coverage=1.0, temporal=1.0, diversity=1.0 → kappa=1.0
        assert k.kappa == pytest.approx(1.0)


# ── wrap() ─────────────────────────────────────────────────────────────────────


class TestWrap:
    def test_returns_conformant_result(self) -> None:
        r = _result(run_log=[_record(confidence=0.85)])
        cr = wrap(r)
        assert isinstance(cr, ConformantResult)
        assert cr.result is r

    def test_result_unchanged(self) -> None:
        participants = [Participant(name='Alice')]
        r = _result(run_log=[_record()], participants=participants)
        cr = wrap(r)
        assert cr.result.participants == participants

    def test_error_result_gets_high_uncertainty(self) -> None:
        r = _result(error='adapter failure')
        cr = wrap(r)
        assert cr.bounds.method == 'parse_error'
        assert cr.bounds.uncertainty_radius == pytest.approx(0.5)

    def test_wrap_never_raises(self) -> None:
        r = ClassificationResult(error='boom', run_log=[])
        cr = wrap(r)
        assert cr is not None

    def test_reference_date_passed_to_kappa(self) -> None:
        commitments = [Commitment(owner='X', action='y', due_date='2025-01-01')]
        r = _result(run_log=[_record()], commitments=commitments)
        cr_past = wrap(r, reference_date=date(2026, 4, 25))
        cr_future = wrap(r, reference_date=date(2024, 1, 1))
        # Past reference date sees the commitment as future (valid)
        assert cr_future.kappa.temporal_consistency == pytest.approx(1.0)
        # Future reference date sees it as past (invalid)
        assert cr_past.kappa.temporal_consistency == pytest.approx(0.0)
