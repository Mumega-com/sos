"""
A7 — FRC overlay: κ alignment + W witness score + four-failure-mode taxonomy.

Gate: A7 (Sprint 003 Track B)
Depends on: A5 (ConformantResult), A6 (LineageReport)

Applies the full FRC 531 framework to a classified transcript as the final
evaluation layer before an engram is committed to Mirror.

FRC 531 Framework applied here:
  κ (kappa)   — coherence alignment: already computed in A5 (conformal.KappaScore)
  W (witness) — meta-awareness score: calibrated from A6 LineageReport quality
  Four failure modes — mechanically detectable defects in ClassificationResult

Four failure modes (F1–F4):
  F1 HALLUCINATION   — high-confidence extraction of facts with no textual anchor
                       Detected by: confidence below calibration floor despite no escalation
                       (proxy — cannot detect without ground truth, but high bounds.lower < 0.3
                       with single-pass and no violations is a warning signal)
  F2 OMISSION        — structural gaps in extraction (e.g. participants without roles,
                       commitments without context when transcript is long enough to have it)
                       Detected by: role=None on >50% of participants, or context=None
                       on >50% of commitments
  F3 COHERENCE_VIOLATION — extracted facts contradict each other
                       Detected by: κ < 0.7 with specific violations
  F4 ATTRIBUTION_ERROR   — facts assigned to unresolved / unknown participants
                       Detected by: commitment owners or opportunity.involved names
                       not in participants list (subset of κ participant_coverage check)

FRC verdict:
  'aligned'   — W ≥ 0.7 and no F3/F4 failures, ≤1 F1/F2 warning
  'degraded'  — W ≥ 0.5 or isolated failures
  'failed'    — W < 0.5 or F3/F4 failures present

W score computation:
  W = mean of four sub-scores:
    w_confidence: bounds.calibrated_confidence (reward for high confidence)
    w_coverage:   bounds.upper (reward for tight upper bound)
    w_kappa:      kappa.kappa (coherence)
    w_pass_count: 1.0 for single-pass, 0.85 for escalated-improved, 0.65 for escalated-degraded,
                  0.0 for parse_error
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

import psycopg2
import psycopg2.extras

from sos.services.intake.conformal import ConformantResult
from sos.services.intake.lineage import LineageReport

log = logging.getLogger(__name__)

# ── Failure mode taxonomy ──────────────────────────────────────────────────────


class FailureModeKind(str, Enum):
    HALLUCINATION = 'F1_HALLUCINATION'
    OMISSION = 'F2_OMISSION'
    COHERENCE_VIOLATION = 'F3_COHERENCE_VIOLATION'
    ATTRIBUTION_ERROR = 'F4_ATTRIBUTION_ERROR'


@dataclass
class FailureMode:
    kind: FailureModeKind
    severity: str           # 'warning' | 'error'
    description: str


# ── FRC overlay result ─────────────────────────────────────────────────────────


@dataclass
class FrcOverlay:
    """
    Full FRC 531 evaluation of a ConformantResult.

    kappa:          from A5 conformal wrapper (coherence alignment)
    witness_score:  W — meta-awareness score computed from pass quality + confidence
    failure_modes:  detected F1–F4 defects
    verdict:        'aligned' | 'degraded' | 'failed'
    verdict_reason: human-readable explanation of the verdict
    """
    kappa: float
    witness_score: float
    failure_modes: list[FailureMode] = field(default_factory=list)
    verdict: str = 'aligned'
    verdict_reason: str = ''


# ── W score ────────────────────────────────────────────────────────────────────


def _witness_score(conformant: ConformantResult, report: LineageReport) -> float:
    """Compute W from calibrated confidence, bound tightness, κ, and pass quality."""
    bounds = conformant.bounds
    kappa = conformant.kappa

    w_confidence = bounds.calibrated_confidence
    w_coverage = bounds.upper                  # tighter upper = more trustworthy

    w_kappa = kappa.kappa

    method = bounds.method
    if method == 'single_pass':
        w_pass = 1.0
    elif method == 'escalated_improved':
        w_pass = 0.85
    elif method == 'escalated_degraded':
        w_pass = 0.65
    else:   # parse_error
        w_pass = 0.0

    return (w_confidence + w_coverage + w_kappa + w_pass) / 4.0


# ── Failure mode detection ─────────────────────────────────────────────────────


def _detect_failures(conformant: ConformantResult) -> list[FailureMode]:
    result = conformant.result
    bounds = conformant.bounds
    kappa = conformant.kappa
    failures: list[FailureMode] = []

    # ── F1: HALLUCINATION proxy ───────────────────────────────────────────────
    # High stated confidence that doesn't survive calibration (wide bounds, low floor)
    if bounds.lower < 0.3 and bounds.method == 'single_pass' and not result.error:
        failures.append(FailureMode(
            kind=FailureModeKind.HALLUCINATION,
            severity='warning',
            description=(
                f'Calibrated lower bound {bounds.lower:.2f} < 0.30 with no escalation. '
                'Extracted facts may lack textual anchor. Human review recommended.'
            ),
        ))

    # ── F2: OMISSION ─────────────────────────────────────────────────────────
    # Participants without roles (>50%)
    if result.participants:
        roleless = sum(1 for p in result.participants if not p.role)
        if roleless / len(result.participants) > 0.5:
            failures.append(FailureMode(
                kind=FailureModeKind.OMISSION,
                severity='warning',
                description=(
                    f'{roleless}/{len(result.participants)} participant(s) have no role assigned. '
                    'Transcript may not contain role signals or model under-extracted.'
                ),
            ))
    # Commitments without context (>50%) when there are commitments
    if result.commitments:
        contextless = sum(1 for c in result.commitments if not c.context)
        if contextless / len(result.commitments) > 0.5:
            failures.append(FailureMode(
                kind=FailureModeKind.OMISSION,
                severity='warning',
                description=(
                    f'{contextless}/{len(result.commitments)} commitment(s) have no context quote. '
                    'Verbatim evidence missing — commitments may be inferred rather than stated.'
                ),
            ))

    # ── F3: COHERENCE_VIOLATION ───────────────────────────────────────────────
    if kappa.kappa < 0.7 and kappa.violations:
        failures.append(FailureMode(
            kind=FailureModeKind.COHERENCE_VIOLATION,
            severity='error',
            description=(
                f'FRC κ = {kappa.kappa:.2f} with {len(kappa.violations)} violation(s): '
                + '; '.join(kappa.violations[:2])
                + (f' (+{len(kappa.violations) - 2} more)' if len(kappa.violations) > 2 else '')
            ),
        ))

    # ── F4: ATTRIBUTION_ERROR ─────────────────────────────────────────────────
    # Specifically when commitment owners are not in participants (stricter than κ check)
    if result.commitments and result.participants:
        participant_names = {p.name.strip().lower() for p in result.participants if p.name}
        unresolved_owners = [
            c.owner for c in result.commitments
            if c.owner and c.owner.strip().lower() not in participant_names
        ]
        if unresolved_owners:
            failures.append(FailureMode(
                kind=FailureModeKind.ATTRIBUTION_ERROR,
                severity='error',
                description=(
                    f'{len(unresolved_owners)} commitment owner(s) not in participants list: '
                    + ', '.join(unresolved_owners[:3])
                    + (f' (+{len(unresolved_owners) - 3} more)' if len(unresolved_owners) > 3 else '')
                    + '. Commitments may be attributed to phantom actors.'
                ),
            ))

    return failures


# ── Verdict determination ──────────────────────────────────────────────────────


def _determine_verdict(
    w: float,
    failures: list[FailureMode],
) -> tuple[str, str]:
    errors = [f for f in failures if f.severity == 'error']
    warnings = [f for f in failures if f.severity == 'warning']

    if w < 0.5 or errors:
        verdict = 'failed'
        reasons = []
        if w < 0.5:
            reasons.append(f'W = {w:.2f} < 0.50')
        for e in errors:
            reasons.append(e.kind.value)
        return verdict, '; '.join(reasons)

    if w < 0.7 or len(warnings) > 1:
        verdict = 'degraded'
        reasons = []
        if w < 0.7:
            reasons.append(f'W = {w:.2f} < 0.70')
        if len(warnings) > 1:
            reasons.append(f'{len(warnings)} warnings')
        return verdict, '; '.join(reasons)

    return 'aligned', f'W = {w:.2f}, κ = {w:.2f}, no blocking failures'


# ── Public API ─────────────────────────────────────────────────────────────────


def evaluate(
    conformant: ConformantResult,
    report: LineageReport,
) -> FrcOverlay:
    """
    Apply the FRC 531 overlay to a ConformantResult + LineageReport.

    Args:
        conformant: Output of conformal.wrap().
        report: Output of lineage.walk().

    Returns:
        FrcOverlay — always. Failed extractions get verdict='failed'.
    """
    if conformant.result.error:
        return FrcOverlay(
            kappa=conformant.kappa.kappa,
            witness_score=0.0,
            failure_modes=[],
            verdict='failed',
            verdict_reason=f'extraction error: {conformant.result.error}',
        )

    w = _witness_score(conformant, report)
    failures = _detect_failures(conformant)
    verdict, reason = _determine_verdict(w, failures)

    log.debug(
        'FRC overlay: W=%.2f κ=%.2f failures=%d verdict=%s',
        w, conformant.kappa.kappa, len(failures), verdict,
    )

    return FrcOverlay(
        kappa=conformant.kappa.kappa,
        witness_score=w,
        failure_modes=failures,
        verdict=verdict,
        verdict_reason=reason,
    )


# ── Mirror-backed verdict history (A.3 matchmaking plumbing) ──────────────────


def _mirror_url() -> str:
    url = os.getenv('MIRROR_DATABASE_URL') or os.getenv('DATABASE_URL')
    if not url:
        raise RuntimeError('MIRROR_DATABASE_URL or DATABASE_URL not set')
    return url


def _confidence_to_verdict(run_log: list[dict]) -> str:
    """
    Derive an FRC-aligned verdict from classifier_run_log entries.

    Uses the last pass (highest pass_number) as the authoritative result.
    Mapping mirrors the FRC W-score thresholds from _determine_verdict():
      parse_error present         → 'failed'
      confidence < 0.50           → 'failed'
      confidence < 0.70           → 'degraded'
      confidence ≥ 0.70, no error → 'aligned'
    """
    if not run_log:
        return 'failed'
    latest = max(run_log, key=lambda r: r.get('pass_number', 1))
    if latest.get('parse_error'):
        return 'failed'
    confidence = float(latest.get('confidence', 0.0))
    if confidence < 0.50:
        return 'failed'
    if confidence < 0.70:
        return 'degraded'
    return 'aligned'


def save_verdict(
    engram_id: str,
    holder_id: str,
    verdict: str,
    issued_by: str = 'classifier',
) -> None:
    """
    Persist an FRC verdict to frc_verdicts via the SECURITY DEFINER frc_emit_verdict().

    This is the only legitimate write path for FRC verdicts (F-01 fix).
    Direct INSERT into frc_verdicts is revoked from all application roles.

    Args:
        engram_id: mirror_engrams.id (UUID as text).
        holder_id: The citizen being assessed (profile_id / agent_id).
        verdict:   'aligned' | 'degraded' | 'failed'.
        issued_by: Classifier run ID, 'system' for backfills.
    """
    try:
        conn = psycopg2.connect(_mirror_url(), cursor_factory=psycopg2.extras.RealDictCursor)
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    'SELECT frc_emit_verdict(%s, %s, %s, %s)',
                    (engram_id, holder_id, verdict, issued_by),
                )
        conn.close()
    except Exception as exc:
        log.warning(
            'save_verdict: DB error engram=%s holder=%s: %s',
            engram_id, holder_id, exc,
        )


def get_recent_verdicts(holder_id: str, lookback_days: int = 30) -> list[dict]:
    """
    Return recent FRC verdicts for a citizen from the frc_verdicts table.

    Reads from frc_verdicts (F-01 fix) instead of mirror_engrams.classifier_run_log.
    The former is write-protected via SECURITY DEFINER sole path; the latter
    allowed citizens to self-issue verdicts by fabricating classifier_run_log.

    Used by §16 matchmaking Stage 2 (FRC veto).

    Returns list of dicts: [{engram_id, verdict, recorded_at}, ...]
    Sorted newest-first. Empty list on DB error or no verdicts (fail-open).
    """
    try:
        conn = psycopg2.connect(_mirror_url(), cursor_factory=psycopg2.extras.RealDictCursor)
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT engram_id, verdict, issued_at
                         FROM frc_verdicts
                        WHERE holder_id = %s
                          AND issued_at >= now() - (%s || ' days')::interval
                        ORDER BY issued_at DESC""",
                    (holder_id, str(lookback_days)),
                )
                rows = cur.fetchall()
        conn.close()
    except Exception as exc:
        log.warning('get_recent_verdicts: DB error for holder=%s: %s', holder_id, exc)
        return []

    return [
        {
            'engram_id': row['engram_id'],
            'verdict': row['verdict'],
            'recorded_at': row['issued_at'],
        }
        for row in rows
    ]
