"""
Tests for SOS kernel EmbeddingAdapter.

Run:
    cd /home/mumega/SOS && python -m pytest sos/kernel/tests/test_embedding_adapter.py -v
"""

from __future__ import annotations

import os
import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# Ensure SOS is importable
_SOS_PATH = os.path.expanduser("~/SOS")
if _SOS_PATH not in sys.path:
    sys.path.insert(0, _SOS_PATH)

from sos.kernel.embedding_adapter import (
    EmbeddingError,
    _resize,
    embed,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_vertex_mock(dim: int = 768) -> Any:
    """Return a mock that behaves like TextEmbeddingModel."""
    values = [0.1] * dim
    mock_emb = MagicMock()
    mock_emb.values = values
    mock_model = MagicMock()
    mock_model.get_embeddings.return_value = [mock_emb]
    return mock_model


def _make_gemini_mock(dim: int = 1536) -> Any:
    values = [0.2] * dim
    mock_emb = MagicMock()
    mock_emb.values = values
    mock_result = MagicMock()
    mock_result.embeddings = [mock_emb]
    mock_client = MagicMock()
    mock_client.models.embed_content.return_value = mock_result
    return mock_client


# ── Unit tests ────────────────────────────────────────────────────────────────


class TestResize:
    def test_pads_short_vector(self) -> None:
        short = [1.0] * 768
        result = _resize(short, 1536)
        assert len(result) == 1536
        assert result[:768] == short
        assert result[768:] == [0.0] * 768

    def test_truncates_long_vector(self) -> None:
        long = [1.0] * 2048
        result = _resize(long, 1536)
        assert len(result) == 1536

    def test_exact_size_unchanged(self) -> None:
        exact = [0.5] * 1536
        result = _resize(exact, 1536)
        assert result == exact


class TestEmbedOutput:
    """embed() must always return exactly 1536 floats (default dims)."""

    def _patch_vertex(self) -> Any:
        import sos.kernel.embedding_adapter as mod
        # Reset cached model so mock is picked up
        mod._vertex_model = None
        mock_model = _make_vertex_mock(768)
        return mock_model

    @patch.dict(os.environ, {"EMBEDDING_BACKEND": "vertex"})
    def test_vertex_returns_1536_floats(self) -> None:
        import sos.kernel.embedding_adapter as mod
        mod._vertex_model = None

        mock_model = _make_vertex_mock(768)
        with (
            patch("vertexai.init"),
            patch(
                "vertexai.language_models.TextEmbeddingModel.from_pretrained",
                return_value=mock_model,
            ),
        ):
            result = embed("hello world")

        assert isinstance(result, list)
        assert len(result) == 1536
        assert all(isinstance(v, float) for v in result)

    @patch.dict(os.environ, {"EMBEDDING_BACKEND": "gemini"})
    def test_gemini_returns_1536_floats(self) -> None:
        mock_client = _make_gemini_mock(1536)
        with patch("google.genai.Client", return_value=mock_client):
            result = embed("hello world")

        assert len(result) == 1536

    @patch.dict(os.environ, {"EMBEDDING_BACKEND": "vertex"})
    def test_vertex_zero_padding_preserved(self) -> None:
        """768-dim vertex output must be zero-padded to 1536."""
        import sos.kernel.embedding_adapter as mod
        mod._vertex_model = None

        mock_model = _make_vertex_mock(768)
        with (
            patch("vertexai.init"),
            patch(
                "vertexai.language_models.TextEmbeddingModel.from_pretrained",
                return_value=mock_model,
            ),
        ):
            result = embed("test text")

        # First 768 values should be the mock value (0.1); rest should be 0.0
        assert result[:768] == [0.1] * 768
        assert result[768:] == [0.0] * 768


class TestVertexNonZero:
    """Vertex backend must produce non-zero vectors."""

    @patch.dict(os.environ, {"EMBEDDING_BACKEND": "vertex"})
    def test_non_zero_vector(self) -> None:
        import sos.kernel.embedding_adapter as mod
        mod._vertex_model = None

        mock_model = _make_vertex_mock(768)
        with (
            patch("vertexai.init"),
            patch(
                "vertexai.language_models.TextEmbeddingModel.from_pretrained",
                return_value=mock_model,
            ),
        ):
            result = embed("The sovereign city runs on coherence")

        assert any(v != 0.0 for v in result), "Vector must not be all zeros"


class TestAutoMode:
    """auto mode should select vertex first when ADC is available."""

    @patch.dict(os.environ, {"EMBEDDING_BACKEND": "auto"})
    def test_auto_uses_vertex_first(self) -> None:
        import sos.kernel.embedding_adapter as mod
        mod._vertex_model = None

        mock_model = _make_vertex_mock(768)
        with (
            patch("vertexai.init"),
            patch(
                "vertexai.language_models.TextEmbeddingModel.from_pretrained",
                return_value=mock_model,
            ),
        ):
            result = embed("auto mode test")

        assert len(result) == 1536

    @patch.dict(os.environ, {"EMBEDDING_BACKEND": "auto"})
    def test_auto_falls_back_to_gemini_when_vertex_fails(self) -> None:
        import sos.kernel.embedding_adapter as mod
        mod._vertex_model = None

        mock_client = _make_gemini_mock(1536)
        with (
            patch("vertexai.init", side_effect=Exception("ADC not configured")),
            patch(
                "vertexai.language_models.TextEmbeddingModel.from_pretrained",
                side_effect=Exception("ADC not configured"),
            ),
            patch("google.genai.Client", return_value=mock_client),
        ):
            result = embed("fallback test")

        assert len(result) == 1536

    @patch.dict(os.environ, {"EMBEDDING_BACKEND": "auto"})
    def test_auto_raises_when_all_fail(self) -> None:
        import sos.kernel.embedding_adapter as mod
        mod._vertex_model = None

        with (
            patch("vertexai.init", side_effect=Exception("fail")),
            patch(
                "vertexai.language_models.TextEmbeddingModel.from_pretrained",
                side_effect=Exception("fail"),
            ),
            patch("google.genai.Client", side_effect=Exception("fail")),
            patch(
                "fastembed.TextEmbedding",
                side_effect=Exception("fail"),
            ),
        ):
            with pytest.raises(EmbeddingError):
                embed("should fail")


class TestUnknownBackend:
    @patch.dict(os.environ, {"EMBEDDING_BACKEND": "unknown_backend"})
    def test_raises_embedding_error(self) -> None:
        with pytest.raises(EmbeddingError, match="Unknown EMBEDDING_BACKEND"):
            embed("test")
