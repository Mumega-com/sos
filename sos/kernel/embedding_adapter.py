"""
EmbeddingAdapter — switchable embedding backend for the SOS microkernel.

Backend selection via EMBEDDING_BACKEND env var:
  vertex    — Vertex AI text-embedding-004 (768d → zero-padded to 1536d). Uses ADC.
  gemini    — Google AI Studio gemini-embedding-2-preview (1536d). Uses GEMINI_API_KEY.
  local     — Local ONNX BAAI/bge-small-en-v1.5 (384d → zero-padded to 1536d). Offline.
  auto      — Try vertex → gemini → local in order (default)
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_DIMS = 1536

# ── Custom exception ──────────────────────────────────────────────────────────


class EmbeddingError(Exception):
    """Raised when all embedding backends fail."""


# ── Cached backend clients ────────────────────────────────────────────────────

_vertex_model: Optional[object] = None
_local_onnx_model: Optional[object] = None


# ── Tier 1: Vertex AI text-embedding-004 (768d → padded to 1536d) ─────────────


def _embed_vertex(text: str) -> list[float]:
    """
    Vertex AI text-embedding-004 via Application Default Credentials (ADC).
    Produces 768-dimensional vectors; zero-padded to _DIMS for pgvector compat.
    """
    global _vertex_model
    import vertexai
    from vertexai.language_models import TextEmbeddingModel

    if _vertex_model is None:
        project = os.environ.get("GOOGLE_CLOUD_PROJECT", "mumega-com")
        location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
        vertexai.init(project=project, location=location)
        _vertex_model = TextEmbeddingModel.from_pretrained("text-embedding-004")
        logger.info(
            "Vertex AI text-embedding-004 client initialised (project=%s, location=%s)",
            project,
            location,
        )

    embeddings = _vertex_model.get_embeddings([text[:8192]])
    emb: list[float] = list(embeddings[0].values)  # 768 floats

    # Zero-pad to _DIMS so pgvector halfvec(1536) columns accept the vector
    if len(emb) < _DIMS:
        emb = emb + [0.0] * (_DIMS - len(emb))

    return emb[:_DIMS]


# ── Tier 2: Google AI Studio gemini-embedding-2-preview (1536d native) ────────

import time as _time

_GEMINI_KEY_POOL: list[str] = [
    k for k in [
        os.environ.get("GEMINI_API_KEY", ""),
        os.environ.get("GEMINI_API_KEY_1", ""),
        os.environ.get("GEMINI_API_KEY_2", ""),
        os.environ.get("GEMINI_API_KEY_3", ""),
        os.environ.get("GEMINI_API_KEY_4", ""),
    ] if k
]
_GEMINI_KEY_EXHAUSTED_UNTIL: dict[str, float] = {}
_GEMINI_QUOTA_COOLDOWN = 3600


def _embed_gemini(text: str) -> list[float]:
    """
    Google AI Studio Gemini Embedding 2 via key rotation pool.
    Tries each key in order; marks exhausted keys for 1 hour on 429.
    """
    from google import genai
    from google.genai import types

    now = _time.time()
    for key in _GEMINI_KEY_POOL:
        if _GEMINI_KEY_EXHAUSTED_UNTIL.get(key, 0) >= now:
            continue
        try:
            client = genai.Client(api_key=key)
            result = client.models.embed_content(
                model="gemini-embedding-2-preview",
                contents=text[:8192],
                config=types.EmbedContentConfig(output_dimensionality=_DIMS),
            )
            return list(result.embeddings[0].values)
        except Exception as e:
            err = str(e)
            if any(kw in err.lower() for kw in ["quota", "429", "resource exhausted", "rate limit"]):
                _GEMINI_KEY_EXHAUSTED_UNTIL[key] = now + _GEMINI_QUOTA_COOLDOWN
                logger.warning("Gemini embedding key exhausted — rotating to next key")
                continue
            raise
    raise EmbeddingError("All Gemini embedding keys exhausted")


# ── Tier 3: Local ONNX via fastembed (384d → zero-padded to 1536d) ────────────


def _embed_local(text: str) -> list[float]:
    """
    Local semantic embedding via fastembed + ONNX runtime.
    Model: BAAI/bge-small-en-v1.5 — 384 dims, ~90 MB, CPU-only, Pi-compatible.
    Zero-padded to _DIMS for index compatibility with halfvec(1536) columns.
    Cosine similarity is unaffected by trailing zeros.
    """
    global _local_onnx_model
    if _local_onnx_model is None:
        from fastembed import TextEmbedding

        _local_onnx_model = TextEmbedding("BAAI/bge-small-en-v1.5")
        logger.info("Local ONNX model loaded (BAAI/bge-small-en-v1.5, 384 dims)")

    embeddings = list(_local_onnx_model.embed([text]))
    emb: list[float] = [float(x) for x in embeddings[0]]

    if len(emb) < _DIMS:
        emb = emb + [0.0] * (_DIMS - len(emb))

    return emb[:_DIMS]


# ── Backend registry ──────────────────────────────────────────────────────────

_BACKENDS: dict[str, tuple[str, object]] = {
    "vertex": ("vertex-text-embedding-004", _embed_vertex),
    "gemini": ("gemini-embedding-2-preview", _embed_gemini),
    "local":  ("local-onnx-bge-small-en",   _embed_local),
}

_AUTO_ORDER = ["vertex", "gemini", "local"]


# ── Public API ────────────────────────────────────────────────────────────────


def embed(text: str, dims: int = _DIMS) -> list[float]:
    """
    Produce a dense embedding vector for *text* of length *dims* (default 1536).

    Backend selection is controlled by the ``EMBEDDING_BACKEND`` environment
    variable (vertex | gemini | local | auto).  When set to ``auto`` (the
    default), the adapter tries vertex → gemini → local in that order and
    returns the first successful result.

    Raises:
        EmbeddingError: if the requested backend fails (explicit mode) or all
                        backends fail (auto mode).
    """
    backend_env = os.environ.get("EMBEDDING_BACKEND", "auto").lower().strip()

    if backend_env == "auto":
        last_exc: Optional[Exception] = None
        for key in _AUTO_ORDER:
            label, fn = _BACKENDS[key]
            try:
                vec = fn(text)
                logger.info("EmbeddingAdapter: used backend=%s dims=%d", label, len(vec))
                return _resize(vec, dims)
            except Exception as exc:
                logger.warning("EmbeddingAdapter: backend=%s failed: %s", label, exc)
                last_exc = exc
        raise EmbeddingError(
            f"All embedding backends failed. Last error: {last_exc}"
        ) from last_exc

    if backend_env not in _BACKENDS:
        raise EmbeddingError(
            f"Unknown EMBEDDING_BACKEND={backend_env!r}. "
            f"Valid values: {list(_BACKENDS)} + 'auto'."
        )

    label, fn = _BACKENDS[backend_env]
    try:
        vec = fn(text)
        logger.info("EmbeddingAdapter: used backend=%s dims=%d", label, len(vec))
        return _resize(vec, dims)
    except Exception as exc:
        raise EmbeddingError(
            f"Embedding backend {backend_env!r} failed: {exc}"
        ) from exc


# ── Helpers ───────────────────────────────────────────────────────────────────


def _resize(vec: list[float], dims: int) -> list[float]:
    """Zero-pad or truncate *vec* to exactly *dims* elements."""
    if len(vec) < dims:
        return vec + [0.0] * (dims - len(vec))
    return vec[:dims]
