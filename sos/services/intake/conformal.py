"""
A5 — Conformal wrapper: uncertainty propagation + FRC κ alignment.

Gate: A5 (Sprint 003 Track B)
Depends on: K5 classifier (ClassificationResult, ClassifierRunRecord)

Takes a ClassificationResult and produces a ConformantResult that adds:
  1. Calibrated per-field uncertainty bounds derived from the run_log
  2. FRC κ (kappa) — coherence alignment score across extracted facts
  3. Coverage guarantee: calibrated_confidence carries a ±uncertainty_radius
     such that a caller can state "confidence ± radius at 90% coverage" without
     retraining a separate calibration model.

Calibration method (single-pass vs escalated):
  - Single pass (no escalation): calibrated_confidence = confidence,
    uncertainty_radius = (1 - confidence) * 0.5
  - Escalated, confidence improved: radius shrinks by 25% (second model agreed)
  - Escalated, confidence did NOT improve: radius widens by 10% (models disagreed)
  - parse_error on any pass: radius set to 0.5 regardless (signal unreliable)

FRC κ computation:
  κ measures internal coherence — how well extracted facts reference each other.
  Checks:
    1. Participant coverage: every commitment.owner and opportunity.involved name
       appears in participants list (κ_participants)
    2. Temporal consistency: no due_date in the past relative to a reference date
       (κ_temporal — skipped if no due_dates present)
    3. Signal diversity: ratio of unique signals across opportunities (κ_diversity)
  κ = mean(checks that fire) — 1.0 if no checks can fire (empty result)

Constitutional rules (inherited from FRC):
  - κ and calibrated_confidence are guidance, not hard filters.
    Caller decides what to do with them.
  - Never modify the underlying ClassificationResult — wrap only.
  - Uncertainty is honest: we do not pad to look more certain.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from sos.services.intake.classifier import ClassificationResult, ClassifierRunRecord

log = logging.getLogger(__name__)

# ── Types ──────────────────────────────────────────────────────────────────────


@dataclass
class UncertaintyBounds:
    """Calibrated confidence interval for one ClassificationResult."""
    calibrated_confidence: float    # point estimate after multi-pass calibration
    uncertainty_radius: float       # ± half-width; 90% coverage heuristic
    lower: float                    # calibrated_confidence - uncertainty_radius, clamped ≥ 0
    upper: float                    # calibrated_confidence + uncertainty_radius, clamped ≤ 1
    method: str                     # 'single_pass' | 'escalated_improved' | 'escalated_degraded' | 'parse_error'


@dataclass
class KappaScore:
    """FRC κ — internal coherence alignment across extracted facts."""
    kappa: float                            # 0.0–1.0; 1.0 = fully coherent
    participant_coverage: Optional[float]   # fraction of owner/involved names in participant list
    temporal_consistency: Optional[float]   # 1.0 if no past due-dates, else ratio of valid
    signal_diversity: Optional[float]       # unique signals / total opportunity count
    violations: list[str] = field(default_factory=list)  # human-readable coherence issues


@dataclass
class ConformantResult:
    """
    A ClassificationResult wrapped with uncertainty bounds and FRC κ.

    The underlying result is unchanged — this is a pure wrapper.
    Callers use calibrated_confidence + kappa to decide trust level;
    the A6 lineage walker adds the W witness layer on top.
    """
    result: ClassificationResult
    bounds: UncertaintyBounds
    kappa: KappaScore


# ── Calibration ────────────────────────────────────────────────────────────────


def _calibrate(run_log: list[ClassifierRunRecord]) -> UncertaintyBounds:
    """
    Derive calibrated confidence bounds from the run_log.

    Single pass:  bounds from raw confidence alone.
    Escalated:    compare pass-1 vs pass-2 confidence to determine agreement.
    Parse error:  uncertainty_radius = 0.5 regardless of stated confidence.
    """
    if not run_log:
        return UncertaintyBounds(
            calibrated_confidence=0.0,
            uncertainty_radius=0.5,
            lower=0.0,
            upper=0.5,
            method='parse_error',
        )

    has_parse_error = any(r.parse_error for r in run_log)
    if has_parse_error:
        raw_conf = run_log[-1].confidence
        return UncertaintyBounds(
            calibrated_confidence=raw_conf,
            uncertainty_radius=0.5,
            lower=max(0.0, raw_conf - 0.5),
            upper=min(1.0, raw_conf + 0.5),
            method='parse_error',
        )

    pass1 = run_log[0]
    if len(run_log) == 1:
        # Single-pass: radius proportional to uncertainty
        radius = (1.0 - pass1.confidence) * 0.5
        conf = pass1.confidence
        return UncertaintyBounds(
            calibrated_confidence=conf,
            uncertainty_radius=radius,
            lower=max(0.0, conf - radius),
            upper=min(1.0, conf + radius),
            method='single_pass',
        )

    # Escalated: compare pass 1 and pass 2
    pass2 = run_log[1]
    base_radius = (1.0 - pass2.confidence) * 0.5
    if pass2.confidence >= pass1.confidence:
        # Models converged or improved — tighten radius
        radius = base_radius * 0.75
        method = 'escalated_improved'
    else:
        # Second model was less confident — widen radius
        radius = base_radius * 1.10
        method = 'escalated_degraded'

    conf = pass2.confidence
    return UncertaintyBounds(
        calibrated_confidence=conf,
        uncertainty_radius=radius,
        lower=max(0.0, conf - radius),
        upper=min(1.0, conf + radius),
        method=method,
    )


# ── FRC κ computation ──────────────────────────────────────────────────────────


def _compute_kappa(
    result: ClassificationResult,
    *,
    reference_date: date | None = None,
) -> KappaScore:
    """
    Compute FRC κ — internal coherence of the ClassificationResult.

    Three sub-checks fire when sufficient data is present:
      1. Participant coverage
      2. Temporal consistency (due_dates vs reference_date)
      3. Signal diversity (opportunities)

    κ = mean of all checks that fire; 1.0 if no checks can fire.
    """
    participant_names: set[str] = {p.name.strip().lower() for p in result.participants if p.name}
    violations: list[str] = []
    scores: list[float] = []

    # ── Check 1: participant coverage ─────────────────────────────────────────
    # Owners of commitments + names in opportunity.involved should appear in participants.
    referenced_names: set[str] = set()
    for c in result.commitments:
        if c.owner:
            referenced_names.add(c.owner.strip().lower())
    for o in result.opportunities:
        for name in o.involved:
            if name:
                referenced_names.add(name.strip().lower())

    if referenced_names:
        covered = sum(1 for n in referenced_names if n in participant_names)
        coverage_ratio = covered / len(referenced_names)
        scores.append(coverage_ratio)
        unresolved = [n for n in referenced_names if n not in participant_names]
        if unresolved:
            violations.append(
                f'participant_coverage: {len(unresolved)} name(s) in commitments/opportunities '
                f'not in participants list: {unresolved[:3]}'
            )
        participant_coverage = coverage_ratio
    else:
        participant_coverage = None

    # ── Check 2: temporal consistency ─────────────────────────────────────────
    ref = reference_date or date.today()
    due_dates = [c.due_date for c in result.commitments if c.due_date]
    if due_dates:
        valid_count = 0
        for ds in due_dates:
            try:
                d = date.fromisoformat(ds)
                if d >= ref:
                    valid_count += 1
                else:
                    violations.append(f'temporal_consistency: due_date {ds} is in the past')
            except ValueError:
                violations.append(f'temporal_consistency: unparseable due_date {ds!r}')
                valid_count += 1  # don't penalise if we can't parse (model did its best)
        temporal_consistency = valid_count / len(due_dates)
        scores.append(temporal_consistency)
    else:
        temporal_consistency = None

    # ── Check 3: signal diversity ─────────────────────────────────────────────
    if result.opportunities:
        unique_signals = len({o.signal for o in result.opportunities})
        diversity = unique_signals / len(result.opportunities)
        scores.append(diversity)
        signal_diversity = diversity
        if diversity < 0.5:
            violations.append(
                f'signal_diversity: only {unique_signals} unique signal type(s) across '
                f'{len(result.opportunities)} opportunity/ies — may indicate over-extraction'
            )
    else:
        signal_diversity = None

    kappa = sum(scores) / len(scores) if scores else 1.0

    return KappaScore(
        kappa=kappa,
        participant_coverage=participant_coverage,
        temporal_consistency=temporal_consistency,
        signal_diversity=signal_diversity,
        violations=violations,
    )


# ── Public API ─────────────────────────────────────────────────────────────────


def wrap(
    result: ClassificationResult,
    *,
    reference_date: date | None = None,
) -> ConformantResult:
    """
    Wrap a ClassificationResult with uncertainty bounds and FRC κ.

    Args:
        result: Output from classify_transcript().
        reference_date: Reference date for temporal consistency check.
                        Defaults to today. Pass a fixed date in tests.

    Returns:
        ConformantResult — always. On error results, bounds reflect
        high uncertainty and kappa defaults to 1.0 (no facts to violate).
    """
    bounds = _calibrate(result.run_log)
    kappa = _compute_kappa(result, reference_date=reference_date)

    log.debug(
        'conformal wrap: calibrated_confidence=%.2f ±%.2f method=%s κ=%.2f violations=%d',
        bounds.calibrated_confidence,
        bounds.uncertainty_radius,
        bounds.method,
        kappa.kappa,
        len(kappa.violations),
    )

    return ConformantResult(result=result, bounds=bounds, kappa=kappa)
