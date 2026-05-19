"""
A6 — Lineage walker: explainability + FRC W (Witness) overlay.

Gate: A6 (Sprint 003 Track B)
Depends on: A5 (ConformantResult), K5 classifier_run_log on mirror_engrams

Two entry points:
  1. walk(conformant_result) — walk the run_log already in memory (pre-storage)
  2. walk_engram(engram_id, db_conn) — query mirror_engrams.classifier_run_log
     for a stored engram and produce a LineageReport

Each LineageReport contains:
  - A per-pass PassTrace (what happened in each model call)
  - A WitnessStatement — the FRC W overlay: a structured, first-person-objective
    account of how the classification was produced, written to be readable by
    a human partner or auditor

W (Witness) design principle (FRC 531):
  The Witness does not judge — it observes and narrates. It names what happened
  without editorializing. A human reading the W statement should be able to
  reconstruct the agent's decision-making without asking any follow-up questions.

  W covers four questions:
    1. What did the model see? (input summary)
    2. What path did it take? (single-pass or escalated, billing path)
    3. What did it extract? (summary of facts)
    4. How confident should we be? (bounds + κ summary)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from sos.services.intake.classifier import ClassificationResult, ClassifierRunRecord
from sos.services.intake.conformal import ConformantResult, KappaScore, UncertaintyBounds

log = logging.getLogger(__name__)

# ── Types ──────────────────────────────────────────────────────────────────────


@dataclass
class PassTrace:
    """One model call's contribution to the final ClassificationResult."""
    pass_number: int
    model: str
    billing_path: str
    confidence: float
    escalated: bool
    latency_ms: int
    cost_cents: int
    parse_error: Optional[str]
    role: str           # 'primary' | 'escalation'
    outcome: str        # 'accepted' | 'fallback' | 'parse_failed'


@dataclass
class WitnessStatement:
    """
    FRC W — structured first-person-objective account of the classification.

    Four fields map to the four W questions. All fields are plain strings
    suitable for display in a partner dashboard or audit log.
    """
    what_was_seen: str          # Input summary (transcript length, detected source_type)
    what_path_was_taken: str    # Model chain: primary → escalation or primary only
    what_was_extracted: str     # Fact summary: N participants, M commitments, etc.
    confidence_assessment: str  # Calibrated confidence ± radius, κ, any violations


@dataclass
class LineageReport:
    """Full lineage trace for one ClassificationResult."""
    passes: list[PassTrace]
    witness: WitnessStatement
    engram_id: Optional[str] = None     # set when walking a stored engram


# ── PassTrace construction ─────────────────────────────────────────────────────


def _build_pass_traces(run_log: list[ClassifierRunRecord]) -> list[PassTrace]:
    traces = []
    final_pass = len(run_log)
    for record in run_log:
        is_last = record.pass_number == final_pass
        if record.parse_error:
            outcome = 'parse_failed'
        elif record.escalated or (not record.escalated and is_last):
            outcome = 'accepted'
        else:
            # Non-last non-escalated pass that succeeded → it triggered escalation
            outcome = 'fallback'

        traces.append(PassTrace(
            pass_number=record.pass_number,
            model=record.model,
            billing_path=record.billing_path,
            confidence=record.confidence,
            escalated=record.escalated,
            latency_ms=record.latency_ms,
            cost_cents=record.cost_cents,
            parse_error=record.parse_error,
            role='primary' if record.pass_number == 1 else 'escalation',
            outcome=outcome,
        ))
    return traces


# ── Witness statement generation ───────────────────────────────────────────────


def _build_witness(
    result: ClassificationResult,
    bounds: UncertaintyBounds,
    kappa: KappaScore,
    passes: list[PassTrace],
    transcript_length: int,
) -> WitnessStatement:
    # ── Q1: what was seen ─────────────────────────────────────────────────────
    what_was_seen = (
        f"Received a {result.source_type} transcript of {transcript_length} characters."
    )

    # ── Q2: what path was taken ───────────────────────────────────────────────
    if not passes:
        what_path = "No model passes recorded — classification did not complete."
    elif len(passes) == 1:
        p = passes[0]
        what_path = (
            f"Single-pass classification via {p.model} ({p.billing_path}). "
            f"Completed in {p.latency_ms}ms at cost {p.cost_cents}¢."
        )
    else:
        p1, p2 = passes[0], passes[1]
        what_path = (
            f"Primary pass via {p1.model} ({p1.billing_path}) returned confidence "
            f"{p1.confidence:.2f} — below escalation threshold. "
            f"Escalated to {p2.model} ({p2.billing_path}): confidence {p2.confidence:.2f}. "
            f"Total latency {p1.latency_ms + p2.latency_ms}ms, "
            f"cost {p1.cost_cents + p2.cost_cents}¢."
        )
        if p2.outcome == 'parse_failed':
            what_path += f" Escalation parse failed ({p2.parse_error}); primary result used."
        elif p2.outcome == 'accepted' and p2.confidence < p1.confidence:
            what_path += " Note: escalated model returned lower confidence (degraded)."

    # ── Q3: what was extracted ────────────────────────────────────────────────
    if result.error:
        what_was_extracted = f"Extraction failed: {result.error}. No facts produced."
    else:
        parts = []
        if result.participants:
            parts.append(f"{len(result.participants)} participant(s)")
        if result.decisions:
            parts.append(f"{len(result.decisions)} decision(s)")
        if result.commitments:
            parts.append(f"{len(result.commitments)} commitment(s)")
        if result.opportunities:
            signals = ', '.join(sorted({o.signal for o in result.opportunities}))
            parts.append(f"{len(result.opportunities)} opportunity/ies ({signals})")
        if result.relationship_signals:
            parts.append(f"{len(result.relationship_signals)} relationship signal(s)")
        what_was_extracted = (
            f"Extracted: {'; '.join(parts)}." if parts else "No facts extracted."
        )

    # ── Q4: confidence assessment ─────────────────────────────────────────────
    conf_str = (
        f"Calibrated confidence {bounds.calibrated_confidence:.2f} "
        f"± {bounds.uncertainty_radius:.2f} ({bounds.method}). "
        f"FRC κ = {kappa.kappa:.2f}."
    )
    if kappa.violations:
        conf_str += f" {len(kappa.violations)} coherence violation(s): " + "; ".join(kappa.violations[:2])
        if len(kappa.violations) > 2:
            conf_str += f" (+{len(kappa.violations) - 2} more)"
    else:
        conf_str += " No coherence violations detected."

    return WitnessStatement(
        what_was_seen=what_was_seen,
        what_path_was_taken=what_path,
        what_was_extracted=what_was_extracted,
        confidence_assessment=conf_str,
    )


# ── Public API ─────────────────────────────────────────────────────────────────


def walk(
    conformant: ConformantResult,
    *,
    transcript_length: int = 0,
    engram_id: str | None = None,
) -> LineageReport:
    """
    Walk the lineage of an in-memory ConformantResult.

    Args:
        conformant: Output of conformal.wrap().
        transcript_length: Length of the original transcript in characters.
                           Pass 0 if not available (logged as unknown).
        engram_id: If this result has been persisted to Mirror, pass its engram id.

    Returns:
        LineageReport — always.
    """
    passes = _build_pass_traces(conformant.result.run_log)
    witness = _build_witness(
        result=conformant.result,
        bounds=conformant.bounds,
        kappa=conformant.kappa,
        passes=passes,
        transcript_length=transcript_length,
    )
    report = LineageReport(passes=passes, witness=witness, engram_id=engram_id)

    log.debug(
        'lineage walk: passes=%d witness_chars=%d engram_id=%s',
        len(passes),
        len(witness.what_was_seen) + len(witness.what_path_was_taken) +
        len(witness.what_was_extracted) + len(witness.confidence_assessment),
        engram_id,
    )
    return report


def walk_engram(engram_id: str, db_conn) -> LineageReport | None:
    """
    Walk the lineage of a stored engram by reading classifier_run_log from Mirror.

    Args:
        engram_id: The mirror_engrams.id of the engram to trace.
        db_conn: An open psycopg2 connection (RealDictCursor).

    Returns:
        LineageReport, or None if engram not found or has no run_log.
    """
    import json

    with db_conn.cursor() as cur:
        cur.execute(
            'SELECT classifier_run_log FROM mirror_engrams WHERE id = %s',
            (engram_id,),
        )
        row = cur.fetchone()

    if not row or row['classifier_run_log'] is None:
        log.info('walk_engram: engram %s has no classifier_run_log', engram_id)
        return None

    raw_log = row['classifier_run_log']
    if isinstance(raw_log, str):
        raw_log = json.loads(raw_log)

    run_log = [
        ClassifierRunRecord(
            pass_number=entry.get('pass_number', 1),
            model=entry.get('model', ''),
            billing_path=entry.get('billing_path', 'unknown'),
            confidence=float(entry.get('confidence', 0.0)),
            escalated=bool(entry.get('escalated', False)),
            latency_ms=int(entry.get('latency_ms', 0)),
            input_tokens=int(entry.get('input_tokens', 0)),
            output_tokens=int(entry.get('output_tokens', 0)),
            cost_cents=int(entry.get('cost_cents', 0)),
            parse_error=entry.get('parse_error'),
        )
        for entry in raw_log
    ]

    # Reconstruct a minimal ClassificationResult — just enough for the witness
    stub_result = ClassificationResult(run_log=run_log)

    from sos.services.intake.conformal import wrap as conformal_wrap
    conformant = conformal_wrap(stub_result)

    passes = _build_pass_traces(run_log)
    witness = _build_witness(
        result=stub_result,
        bounds=conformant.bounds,
        kappa=conformant.kappa,
        passes=passes,
        transcript_length=0,
    )
    return LineageReport(passes=passes, witness=witness, engram_id=engram_id)
