"""
§16 Quest Vectors — A.4 Vertex Flash Lite auto-extraction (Sprint 004 A.4).

Scores each quest on 16 canonical alignment dimensions using Vertex Flash Lite.
Stores result in `quest_vectors` (FLOAT8[] vector + JSONB named_dims sidecar).

16 canonical dimensions (order is fixed — must match citizen_vectors):
  0  technical_depth       depth and specificity of technical skill required
  1  communication         written/verbal clarity and collaboration bandwidth
  2  reliability           consistent delivery, low failure-rate work
  3  creativity            novel approach generation, open-ended problem solving
  4  analytical_rigor      data-driven, evidence-based reasoning
  5  scope_awareness       knowing limits, accurate self-assessment
  6  execution_speed       throughput-heavy, time-pressured delivery
  7  collaboration         paired/team work vs solo autonomy
  8  documentation         knowledge transfer, write-up, async communication
  9  mentorship            teaching, onboarding, knowledge amplification
 10  strategic_thinking    long-term planning, systems thinking
 11  compliance            protocol adherence, regulatory/audit work
 12  resilience            handles ambiguity, failure recovery
 13  initiative            self-directed vs needs explicit tasking
 14  domain_breadth        generalist breadth vs specialist depth signal
 15  innovation            pushing beyond current patterns, R&D frontier

Scores are in [0.0, 1.0]: 0.0 = not at all required; 1.0 = essential.

Public API:
  DIMENSION_NAMES   — ordered list of 16 dimension names
  extract(quest_id) — auto-extract via Vertex Flash Lite + upsert to quest_vectors
  upsert_manual(quest_id, vector) — manual override (source='manual')
  get_vector(quest_id) — retrieve stored vector + named_dims
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import psycopg2
import psycopg2.extras

log = logging.getLogger(__name__)

# ── Canonical 16D schema ──────────────────────────────────────────────────────

DIMENSION_NAMES: list[str] = [
    'technical_depth',
    'communication',
    'reliability',
    'creativity',
    'analytical_rigor',
    'scope_awareness',
    'execution_speed',
    'collaboration',
    'documentation',
    'mentorship',
    'strategic_thinking',
    'compliance',
    'resilience',
    'initiative',
    'domain_breadth',
    'innovation',
]

assert len(DIMENSION_NAMES) == 16, 'DIMENSION_NAMES must have exactly 16 entries'

# ── Extraction prompt ─────────────────────────────────────────────────────────

_EXTRACTION_SYSTEM = """\
You are a precision quest-alignment scorer for the SOS citizen platform.
Given a quest title and description, score the quest on 16 dimensions.
Each score is a float in [0.0, 1.0]: 0.0 = not required at all, 1.0 = essential.
Be calibrated: most quests score 0.2–0.8 on most dimensions; extremes (0.0 or 1.0) are rare.
Reply with ONLY a JSON object — no preamble, no explanation, no markdown fences.
"""

_EXTRACTION_PROMPT_TEMPLATE = """\
Quest title: {title}
Quest description: {description}

Score this quest on the following 16 dimensions (float 0.0–1.0 each):
{dim_list}

Reply with ONLY a JSON object with these exact keys and float values."""


def _build_prompt(title: str, description: str) -> str:
    dim_list = '\n'.join(f'- {name}' for name in DIMENSION_NAMES)
    return _EXTRACTION_PROMPT_TEMPLATE.format(
        title=title,
        description=description or '(no description provided)',
        dim_list=dim_list,
    )


def _parse_response(text: str) -> dict[str, float]:
    """
    Parse LLM JSON response into {dim_name: score} dict.
    Strips markdown fences if present. Clamps scores to [0.0, 1.0].
    Raises ValueError on parse failure.
    """
    # Strip ```json ... ``` fences if the model adds them despite instruction
    cleaned = re.sub(r'^```(?:json)?\s*|\s*```$', '', text.strip(), flags=re.MULTILINE)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f'LLM returned non-JSON: {exc!r}\nRaw: {text[:200]}') from exc

    result: dict[str, float] = {}
    for name in DIMENSION_NAMES:
        raw = data.get(name)
        if raw is None:
            log.warning('LLM missing dimension %r — defaulting to 0.5', name)
            result[name] = 0.5
        else:
            result[name] = float(max(0.0, min(1.0, float(raw))))
    return result


def _named_dims_to_vector(named: dict[str, float]) -> list[float]:
    """Convert named_dims dict → ordered 16-element list matching DIMENSION_NAMES."""
    return [named[name] for name in DIMENSION_NAMES]


# ── DB helpers ────────────────────────────────────────────────────────────────


def _db_url() -> str:
    url = os.getenv('MIRROR_DATABASE_URL') or os.getenv('DATABASE_URL')
    if not url:
        raise RuntimeError('MIRROR_DATABASE_URL or DATABASE_URL is not set')
    return url


def _connect():
    return psycopg2.connect(_db_url(), cursor_factory=psycopg2.extras.RealDictCursor)


def _fetch_quest(conn, quest_id: str) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            'SELECT id, title, description FROM quests WHERE id = %s',
            (quest_id,),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def _upsert_vector(
    conn,
    quest_id: str,
    vector: list[float],
    named_dims: dict[str, float] | None,
    source: str,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO quest_vectors (quest_id, vector, named_dims, source, extracted_at)
               VALUES (%s, %s, %s, %s, now())
               ON CONFLICT (quest_id) DO UPDATE SET
                   vector       = EXCLUDED.vector,
                   named_dims   = EXCLUDED.named_dims,
                   source       = EXCLUDED.source,
                   extracted_at = now()""",
            (
                quest_id,
                vector,
                json.dumps(named_dims) if named_dims is not None else None,
                source,
            ),
        )
    conn.commit()


# ── Public API ────────────────────────────────────────────────────────────────


def extract(quest_id: str) -> dict[str, Any]:
    """
    Auto-extract 16D alignment vector for a quest via Vertex Flash Lite.

    Reads quest.title + quest.description from DB.
    Calls VertexGeminiAdapter with gemini-2.5-flash-lite (cost-disciplined; no escalation).
    Parses JSON response → named_dims dict → 16-element FLOAT8[] vector.
    Upserts to quest_vectors with source='auto-extracted'.

    Returns {'quest_id', 'vector', 'named_dims', 'source', 'model'}.
    Raises ValueError on quest-not-found or parse failure.
    Raises RuntimeError on Vertex call failure (let caller decide retry policy).
    """
    import asyncio

    from sos.adapters.base import ExecutionContext
    from sos.adapters.vertex_gemini_adapter import VertexGeminiAdapter

    with _connect() as conn:
        quest = _fetch_quest(conn, quest_id)
        if quest is None:
            raise ValueError(f'quest {quest_id!r} not found in quests table')

    title = quest['title']
    description = quest.get('description') or ''

    prompt = _build_prompt(title, description)
    model = 'gemini-2.5-flash-lite'

    ctx = ExecutionContext(
        agent_id='quest-vector-extractor',
        prompt=prompt,
        system_prompt=_EXTRACTION_SYSTEM,
        model=model,
        temperature=0.1,     # low temperature — scoring, not generation
        max_tokens=512,      # 16 floats as JSON fits well under 256 tokens
    )

    adapter = VertexGeminiAdapter()

    # VertexGeminiAdapter.execute() is async; run in a new event loop if called sync
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, adapter.execute(ctx))
                result = future.result(timeout=30)
        else:
            result = loop.run_until_complete(adapter.execute(ctx))
    except Exception as exc:
        raise RuntimeError(f'Vertex Flash Lite call failed for quest {quest_id!r}: {exc}') from exc

    if not result.success or not result.text:
        raise RuntimeError(
            f'Vertex Flash Lite returned failure for quest {quest_id!r}: {result.error}'
        )

    named_dims = _parse_response(result.text)
    vector = _named_dims_to_vector(named_dims)

    with _connect() as conn:
        _upsert_vector(conn, quest_id, vector, named_dims, source='auto-extracted')

    log.info(
        'quest_vectors: extracted 16D for quest=%s model=%s',
        quest_id, model,
    )
    return {
        'quest_id': quest_id,
        'vector': vector,
        'named_dims': named_dims,
        'source': 'auto-extracted',
        'model': model,
    }


def upsert_manual(quest_id: str, vector: list[float]) -> dict[str, Any]:
    """
    Manual override — quest creator supplies the 16D vector directly.

    vector must be exactly 16 floats in [0.0, 1.0].
    Stored with source='manual'; named_dims is NULL (no LLM sidecar for manual vectors).

    Returns {'quest_id', 'vector', 'source'}.
    Raises ValueError on invalid input.
    """
    if len(vector) != 16:
        raise ValueError(f'vector must have exactly 16 elements, got {len(vector)}')
    clamped = [float(max(0.0, min(1.0, v))) for v in vector]

    with _connect() as conn:
        quest = _fetch_quest(conn, quest_id)
        if quest is None:
            raise ValueError(f'quest {quest_id!r} not found')
        _upsert_vector(conn, quest_id, clamped, named_dims=None, source='manual')

    log.info('quest_vectors: manual upsert for quest=%s', quest_id)
    return {'quest_id': quest_id, 'vector': clamped, 'source': 'manual'}


def get_vector(quest_id: str) -> dict[str, Any] | None:
    """
    Retrieve stored vector + named_dims for a quest.

    Returns None if no vector has been extracted yet.
    Returns {'quest_id', 'vector', 'named_dims', 'source', 'extracted_at'}.
    """
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT quest_id, vector, named_dims, source, extracted_at
                     FROM quest_vectors WHERE quest_id = %s""",
                (quest_id,),
            )
            row = cur.fetchone()
    if not row:
        return None
    return {
        'quest_id': row['quest_id'],
        'vector': list(row['vector']),
        'named_dims': row['named_dims'],
        'source': row['source'],
        'extracted_at': row['extracted_at'],
    }
