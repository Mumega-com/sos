"""
K5 Meeting/Event Classifier — §10 Metabolic Loop.

Gate: K5 (Sprint 001 carry → Sprint 003)

Classifies raw meeting transcripts into structured facts using
gemini-2.5-flash-lite (first pass). Escalates to gemini-2.5-flash
when confidence falls below ESCALATION_THRESHOLD.

Billing path:
  Production → VertexGeminiAdapter (ADC, Vertex AI credits, no API key).
  Detected via GOOGLE_CLOUD_PROJECT env var being set.
  Fallback → GeminiAdapter (direct API, GOOGLE_API_KEY required).

Output shape:
  ClassificationResult
    participants    list[Participant]       — named attendees + roles + contact hints
    decisions       list[str]               — explicit decisions made
    commitments     list[Commitment]        — who promised what by when
    opportunities   list[Opportunity]       — business signals (asks, leads, referrals)
    relationship_signals list[RelSignal]    — warm/cooling/friction observations
    source_type     str                     — 'meeting' | 'email' | 'document'
    confidence      float                   — 0.0–1.0; <ESCALATION_THRESHOLD → escalated
    model_used      str                     — model that produced the result
    raw_json        dict                    — raw parsed model output
    run_log         list[ClassifierRunRecord] — per-pass metadata for A6 lineage walker

Constitutional rules:
  1. DEKs and KEKs never appear here — classification is text-only.
  2. No PII written to logs — participants are logged by index, not name.
  3. Unresolved participants are flagged, not silently dropped.
  4. Model call cost is logged (GeminiAdapter/VertexGeminiAdapter tracks it).
  5. Confidence < ESCALATION_THRESHOLD triggers one escalation call; no infinite loop.
  6. Every model pass writes a ClassifierRunRecord (A6 D7 prerequisite).
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from sos.adapters.base import AgentAdapter, ExecutionContext

log = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────

FLASH_LITE_MODEL = 'gemini-2.5-flash-lite'
FLASH_MODEL = 'gemini-2.5-flash'
ESCALATION_THRESHOLD = 0.6  # confidence below this → escalate to Flash

# ── Data models ────────────────────────────────────────────────────────────────


@dataclass
class Participant:
    name: str
    role: Optional[str] = None          # e.g. 'host', 'client', 'partner'
    email_hint: Optional[str] = None    # extracted email if mentioned
    contact_ref: Optional[str] = None   # resolved contacts.id (filled by entity resolver)
    resolved: bool = False              # True once entity resolver links to a contact


@dataclass
class Commitment:
    owner: str          # person who made the commitment
    action: str         # what they committed to do
    due_date: Optional[str] = None   # ISO date if mentioned, else None
    context: Optional[str] = None    # surrounding sentence for disambiguation


@dataclass
class Opportunity:
    signal: str                     # short label: 'referral' | 'lead' | 'ask' | 'upsell' | 'risk'
    description: str
    involved: list[str] = field(default_factory=list)  # participant names
    value_hint: Optional[str] = None   # dollar amount or qualitative value if mentioned


@dataclass
class RelSignal:
    subject: str        # participant name
    sentiment: str      # 'warm' | 'active' | 'cooling' | 'friction' | 'unknown'
    evidence: str       # verbatim quote or paraphrase from transcript


@dataclass
class ClassifierRunRecord:
    """
    Per-pass metadata written for A6 lineage walker (D7 prerequisite).

    One record per model call (lite pass + escalation pass if fired).
    Stored in ClassificationResult.run_log and persisted to
    mirror_engrams.classifier_run_log JSONB column by the caller.
    """
    pass_number: int           # 1 = lite, 2 = escalated
    model: str
    billing_path: str          # 'vertex-adc' | 'gemini-api'
    confidence: float
    escalated: bool
    latency_ms: int
    input_tokens: int
    output_tokens: int
    cost_cents: int
    parse_error: Optional[str]


@dataclass
class ClassificationResult:
    participants: list[Participant] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    commitments: list[Commitment] = field(default_factory=list)
    opportunities: list[Opportunity] = field(default_factory=list)
    relationship_signals: list[RelSignal] = field(default_factory=list)
    source_type: str = 'meeting'
    confidence: float = 0.0
    model_used: str = FLASH_LITE_MODEL
    raw_json: dict = field(default_factory=dict)
    escalated: bool = False
    run_log: list[ClassifierRunRecord] = field(default_factory=list)
    error: Optional[str] = None


# ── Prompts ────────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are a meeting intelligence classifier. Extract structured facts from raw \
meeting transcripts. Return ONLY valid JSON — no markdown fences, no prose. \
If a field cannot be determined, use null or an empty list. Never fabricate \
information not present in the transcript.
"""

_USER_PROMPT_TEMPLATE = """\
Classify this transcript and return a JSON object with exactly these fields:

{{
  "source_type": "<meeting|email|document>",
  "confidence": <float 0.0-1.0 representing your certainty in this extraction>,
  "participants": [
    {{
      "name": "<full name as mentioned>",
      "role": "<host|client|partner|colleague|unknown or null>",
      "email_hint": "<email address if explicitly mentioned, else null>"
    }}
  ],
  "decisions": ["<explicit decision stated in the meeting>"],
  "commitments": [
    {{
      "owner": "<person's name>",
      "action": "<what they committed to>",
      "due_date": "<ISO date YYYY-MM-DD if mentioned, else null>",
      "context": "<short quote or paraphrase that makes this commitment clear>"
    }}
  ],
  "opportunities": [
    {{
      "signal": "<referral|lead|ask|upsell|risk>",
      "description": "<one sentence>",
      "involved": ["<participant name>"],
      "value_hint": "<dollar amount or qualitative value if mentioned, else null>"
    }}
  ],
  "relationship_signals": [
    {{
      "subject": "<participant name>",
      "sentiment": "<warm|active|cooling|friction|unknown>",
      "evidence": "<verbatim quote or close paraphrase>"
    }}
  ]
}}

TRANSCRIPT:
{transcript}
"""


# ── Core classification ────────────────────────────────────────────────────────


def _build_prompt(transcript: str) -> str:
    return _USER_PROMPT_TEMPLATE.format(transcript=transcript)


def _parse_model_output(text: str) -> tuple[dict[str, Any], str | None]:
    """Parse JSON from model output. Returns (parsed_dict, error_message)."""
    stripped = text.strip()
    # Strip markdown code fences if model ignores the instruction
    if stripped.startswith('```'):
        lines = stripped.split('\n')
        stripped = '\n'.join(lines[1:-1] if lines[-1].strip() == '```' else lines[1:])
    try:
        return json.loads(stripped), None
    except json.JSONDecodeError as exc:
        return {}, f'JSON parse error: {exc}'


def _build_result(raw: dict, model_used: str, escalated: bool = False) -> ClassificationResult:
    """Convert raw parsed JSON into a ClassificationResult."""
    participants = [
        Participant(
            name=p.get('name', ''),
            role=p.get('role'),
            email_hint=p.get('email_hint'),
        )
        for p in raw.get('participants', [])
        if p.get('name')
    ]
    decisions = [d for d in raw.get('decisions', []) if d]
    commitments = [
        Commitment(
            owner=c.get('owner', ''),
            action=c.get('action', ''),
            due_date=c.get('due_date'),
            context=c.get('context'),
        )
        for c in raw.get('commitments', [])
        if c.get('owner') and c.get('action')
    ]
    opportunities = [
        Opportunity(
            signal=o.get('signal', 'unknown'),
            description=o.get('description', ''),
            involved=o.get('involved', []),
            value_hint=o.get('value_hint'),
        )
        for o in raw.get('opportunities', [])
        if o.get('description')
    ]
    relationship_signals = [
        RelSignal(
            subject=r.get('subject', ''),
            sentiment=r.get('sentiment', 'unknown'),
            evidence=r.get('evidence', ''),
        )
        for r in raw.get('relationship_signals', [])
        if r.get('subject')
    ]

    return ClassificationResult(
        participants=participants,
        decisions=decisions,
        commitments=commitments,
        opportunities=opportunities,
        relationship_signals=relationship_signals,
        source_type=raw.get('source_type', 'meeting'),
        confidence=float(raw.get('confidence', 0.0)),
        model_used=model_used,
        raw_json=raw,
        escalated=escalated,
    )


def _make_adapter(api_key: str | None) -> tuple[AgentAdapter, str]:
    """
    Select the right adapter based on environment.

    Returns (adapter, billing_path).

    Production (GOOGLE_CLOUD_PROJECT set) → VertexGeminiAdapter (ADC, no key).
    Dev/fallback → GeminiAdapter (direct API, GOOGLE_API_KEY).
    """
    if os.environ.get('GOOGLE_CLOUD_PROJECT'):
        from sos.adapters.vertex_gemini_adapter import VertexGeminiAdapter
        return VertexGeminiAdapter(), 'vertex-adc'
    else:
        from sos.adapters.gemini_adapter import GeminiAdapter
        key = api_key or os.getenv('GOOGLE_API_KEY', '')
        return GeminiAdapter(api_key=key), 'gemini-api'


async def _run_pass(
    adapter: AgentAdapter,
    billing_path: str,
    prompt: str,
    model: str,
    agent_id: str,
    pass_number: int,
) -> tuple[ClassificationResult | None, ClassifierRunRecord]:
    """
    Execute one classifier pass and return (result_or_None, run_record).

    Returns None result if the call failed or JSON was unparseable.
    The run_record is always populated for lineage logging.
    """
    ctx = ExecutionContext(
        agent_id=agent_id,
        prompt=prompt,
        system_prompt=_SYSTEM_PROMPT,
        model=model,
        temperature=0.1,
        max_tokens=2048,
    )

    t0 = time.monotonic()
    exec_result = await adapter.execute(ctx)
    latency_ms = int((time.monotonic() - t0) * 1000)

    if not exec_result.success:
        record = ClassifierRunRecord(
            pass_number=pass_number,
            model=model,
            billing_path=billing_path,
            confidence=0.0,
            escalated=(pass_number > 1),
            latency_ms=latency_ms,
            input_tokens=exec_result.usage.input_tokens,
            output_tokens=exec_result.usage.output_tokens,
            cost_cents=exec_result.usage.cost_cents,
            parse_error=f'adapter_error: {exec_result.error}',
        )
        return None, record

    raw, parse_error = _parse_model_output(exec_result.text)
    confidence = float(raw.get('confidence', 0.0)) if not parse_error else 0.0

    record = ClassifierRunRecord(
        pass_number=pass_number,
        model=model,
        billing_path=billing_path,
        confidence=confidence,
        escalated=(pass_number > 1),
        latency_ms=latency_ms,
        input_tokens=exec_result.usage.input_tokens,
        output_tokens=exec_result.usage.output_tokens,
        cost_cents=exec_result.usage.cost_cents,
        parse_error=parse_error,
    )

    if parse_error:
        return None, record

    classification = _build_result(raw, model_used=model, escalated=(pass_number > 1))
    classification.run_log = []  # caller assembles the full log
    return classification, record


async def classify_transcript(
    transcript: str,
    *,
    api_key: str | None = None,
    agent_id: str = 'intake-classifier',
) -> ClassificationResult:
    """
    Classify a meeting/event transcript into structured facts.

    Billing path:
      - GOOGLE_CLOUD_PROJECT set → Vertex AI ADC (credits billing)
      - else → direct Gemini API (GOOGLE_API_KEY)

    First pass: gemini-2.5-flash-lite (cheap, fast).
    Escalation: gemini-2.5-flash if confidence < ESCALATION_THRESHOLD.

    Every pass appends a ClassifierRunRecord to result.run_log.
    Caller is responsible for persisting run_log to
    mirror_engrams.classifier_run_log JSONB (A6 D7 prerequisite).

    Args:
        transcript: Raw text of the meeting transcript or event log.
        api_key: Google API key (only used in direct-API fallback path).
        agent_id: Caller identity for logging + usage tracking.

    Returns:
        ClassificationResult — always. error field set on failure.
    """
    if not transcript or not transcript.strip():
        return ClassificationResult(error='Empty transcript')

    adapter, billing_path = _make_adapter(api_key)
    prompt = _build_prompt(transcript)
    run_log: list[ClassifierRunRecord] = []

    # Pass 1 — flash-lite
    classification, record1 = await _run_pass(
        adapter, billing_path, prompt, FLASH_LITE_MODEL, agent_id, pass_number=1,
    )
    run_log.append(record1)

    if classification is None:
        # Adapter failure or parse error on first pass
        error_msg = record1.parse_error or 'unknown error'
        log.error('Classifier pass 1 failed (%s): %s', billing_path, error_msg)
        return ClassificationResult(
            model_used=FLASH_LITE_MODEL,
            error=error_msg,
            run_log=run_log,
        )

    log.info(
        'Classifier pass 1 complete (%s): confidence=%.2f participants=%d latency=%dms cost=%d¢',
        billing_path, classification.confidence,
        len(classification.participants), record1.latency_ms, record1.cost_cents,
    )

    # Pass 2 — escalate to Flash when confidence is low
    if classification.confidence < ESCALATION_THRESHOLD:
        log.info(
            'Confidence %.2f < threshold %.2f — escalating to %s',
            classification.confidence, ESCALATION_THRESHOLD, FLASH_MODEL,
        )
        escalated, record2 = await _run_pass(
            adapter, billing_path, prompt, FLASH_MODEL, agent_id, pass_number=2,
        )
        run_log.append(record2)

        if escalated is not None:
            classification = escalated
            log.info(
                'Escalated pass complete (%s): confidence=%.2f latency=%dms cost=%d¢',
                billing_path, classification.confidence, record2.latency_ms, record2.cost_cents,
            )
        else:
            log.warning('Escalated pass failed: %s', record2.parse_error)

    classification.run_log = run_log
    return classification
