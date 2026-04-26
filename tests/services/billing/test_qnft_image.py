"""
Sprint 007 G73 v0.4 — QNFT image generation + R2 upload tests (8 TCs).

TC-G73-a    generate_qnft_image succeeds with AvatarGenerator → returns bytes
TC-G73-b    PIL not available → raises QnftImageGenerationError step=pil_missing
TC-G73-b2   AvatarGenerator.generate raises → QnftImageGenerationError step=generation
TC-G73-c    upload_qnft_to_r2 succeeds → returns URL matching expected shape
TC-G73-d    upload_qnft_to_r2 with R2 failure raises QnftR2UploadError
TC-G73-e    Webhook: image gen failure post-commit → knight minted, URI NULL, emit fires
TC-G73-f    Webhook: R2 upload failure post-commit → DB intact + emit fires + URI NULL
TC-G73-f2   Webhook: full happy path → URI updated to R2 URL after commit
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# TC-G73-a: generate_qnft_image succeeds
# ---------------------------------------------------------------------------


def test_g73_a_generate_image_succeeds() -> None:
    """TC-G73-a: generate_qnft_image with AvatarGenerator returns bytes."""
    from sos.services.billing.qnft_image import generate_qnft_image

    fake_image_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(fake_image_bytes)
        tmp_path = f.name

    mock_result = {
        "success": True,
        "path": tmp_path,
        "filename": "test.png",
        "dna_hash": "abc123",
        "coherence": 0.75,
    }

    with patch("sos.services.billing.qnft_image.AvatarGenerator") as MockGen, \
         patch("sos.services.billing.qnft_image.PIL_AVAILABLE", True):
        mock_gen = MagicMock()
        mock_gen.generate.return_value = mock_result
        MockGen.return_value = mock_gen

        result = generate_qnft_image(
            agent_id="test-knight",
            vector_16d=[0.5, -0.3, 0.8, -0.1, 0.6, -0.7, 0.2, -0.4,
                        0.9, -0.5, 0.3, -0.8, 0.1, -0.6, 0.7, -0.2],
            cause="Serves Test Corp.",
        )

    assert isinstance(result, bytes)
    assert len(result) > 0
    assert result == fake_image_bytes
    Path(tmp_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# TC-G73-b: PIL not available → QnftImageGenerationError step=pil_missing
# ---------------------------------------------------------------------------


def test_g73_b_pil_missing_raises() -> None:
    """TC-G73-b: PIL not available raises QnftImageGenerationError with step=pil_missing."""
    from sos.services.billing.qnft_image import (
        QnftImageGenerationError,
        generate_qnft_image,
    )

    with patch("sos.services.billing.qnft_image.PIL_AVAILABLE", False):
        with pytest.raises(QnftImageGenerationError) as exc_info:
            generate_qnft_image("test", [0.0] * 16, "test cause")

        assert exc_info.value.step == "pil_missing"


# ---------------------------------------------------------------------------
# TC-G73-b2: AvatarGenerator.generate raises → step=generation
# ---------------------------------------------------------------------------


def test_g73_b2_generation_failure_raises() -> None:
    """TC-G73-b2: AvatarGenerator.generate failure raises QnftImageGenerationError."""
    from sos.services.billing.qnft_image import (
        QnftImageGenerationError,
        generate_qnft_image,
    )

    with patch("sos.services.billing.qnft_image.AvatarGenerator") as MockGen, \
         patch("sos.services.billing.qnft_image.PIL_AVAILABLE", True):
        mock_gen = MagicMock()
        mock_gen.generate.side_effect = RuntimeError("Geometry computation failed")
        MockGen.return_value = mock_gen

        with pytest.raises(QnftImageGenerationError) as exc_info:
            generate_qnft_image("test", [0.0] * 16, "test cause")

        assert exc_info.value.step == "generation"


# ---------------------------------------------------------------------------
# TC-G73-c: upload_qnft_to_r2 succeeds
# ---------------------------------------------------------------------------


def test_g73_c_upload_succeeds() -> None:
    """TC-G73-c: upload_qnft_to_r2 succeeds → returns URL matching expected shape."""
    from sos.services.billing.qnft_image import upload_qnft_to_r2

    fake_bytes = b"\x89PNG" + b"\x00" * 50

    with patch("sos.services.billing.qnft_image._build_r2_client") as mock_build:
        mock_s3 = MagicMock()
        mock_build.return_value = mock_s3

        url = upload_qnft_to_r2("p0000000-0000-0000-0000-000000000001", fake_bytes)

    assert "qnft/p0000000-0000-0000-0000-000000000001/v1.png" in url
    mock_s3.put_object.assert_called_once()
    call_kwargs = mock_s3.put_object.call_args
    assert call_kwargs[1]["ContentType"] == "image/png"
    assert call_kwargs[1]["Body"] == fake_bytes


# ---------------------------------------------------------------------------
# TC-G73-d: upload_qnft_to_r2 failure raises QnftR2UploadError
# ---------------------------------------------------------------------------


def test_g73_d_upload_failure_raises() -> None:
    """TC-G73-d: R2 upload failure raises QnftR2UploadError."""
    from sos.services.billing.qnft_image import QnftR2UploadError, upload_qnft_to_r2

    with patch("sos.services.billing.qnft_image._build_r2_client") as mock_build:
        mock_s3 = MagicMock()
        mock_s3.put_object.side_effect = RuntimeError("R2 connection refused")
        mock_build.return_value = mock_s3

        with pytest.raises(QnftR2UploadError):
            upload_qnft_to_r2("principal-001", b"\x89PNG")


# ---------------------------------------------------------------------------
# Helpers for webhook integration tests
# ---------------------------------------------------------------------------


def _make_payment_intent(
    payment_intent_id: str = "pi_test_abc123",
    customer: str = "cus_test_xyz",
    project: str = "mumega",
    tenant_slug: str = "acme",
    knight_name: str = "acme-knight",
    receipt_email: str = "test@acme.com",
) -> dict[str, Any]:
    return {
        "id": payment_intent_id,
        "customer": customer,
        "receipt_email": receipt_email,
        "metadata": {
            "project": project,
            "tenant_slug": tenant_slug,
            "knight_name": knight_name,
            "customer_name": "Acme Corp",
            "cause": "Serves Acme Corp as their dedicated agent.",
        },
    }


def _make_contract_row(
    stripe_customer_id: str = "cus_test_xyz",
    tenant_slug: str = "acme",
    project: str = "mumega",
) -> dict:
    return {
        "id": "c0000000-0000-0000-0000-000000000001",
        "principal_id": "p0000000-0000-0000-0000-000000000001",
        "tenant_slug": tenant_slug,
        "stripe_customer_id": stripe_customer_id,
        "cause_statement": "Serves Acme Corp.",
        "status": "sent",
        "project": project,
        "knight_id": None,
    }


def _make_mock_conn(*, fetchrow_return=None):
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(return_value=None)
    mock_conn.fetchrow = AsyncMock(return_value=fetchrow_return)
    mock_conn.close = AsyncMock()
    mock_tx = AsyncMock()
    mock_tx.__aenter__ = AsyncMock(return_value=None)
    mock_tx.__aexit__ = AsyncMock(return_value=False)
    mock_conn.transaction = MagicMock(return_value=mock_tx)
    return mock_conn


# ---------------------------------------------------------------------------
# TC-G73-e: image gen failure post-commit → knight minted, URI NULL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_g73_e_image_gen_failure_knight_still_minted() -> None:
    """TC-G73-e: image gen failure post-commit → knight minted, URI NULL, emit fires."""
    from sos.services.billing.webhook import handle_payment_intent_succeeded
    from sos.services.billing.qnft_image import QnftImageGenerationError

    pi = _make_payment_intent()
    contract = _make_contract_row()
    mock_conn = _make_mock_conn(fetchrow_return=contract)

    emitted_r2_fail: list[dict] = []

    with patch("sos.services.billing.webhook.asyncpg") as mock_asyncpg, \
         patch("sos.services.billing.webhook.mint_knight_programmatic",
               return_value={"ok": True, "knight_id": "agent:acme-knight",
                             "knight_slug": "acme-knight", "qnft_uri": "qnft:acme-knight:abc",
                             "error": None, "skipped": False, "vector_16d": [0.0] * 16}), \
         patch("sos.services.billing.webhook.emit_knight_minted"), \
         patch("sos.services.billing.webhook.emit_stripe_webhook"), \
         patch("sos.services.billing.qnft_image.generate_qnft_image",
               side_effect=QnftImageGenerationError("PIL missing", step="pil_missing")), \
         patch("sos.observability.sprint_telemetry.emit_qnft_r2_upload_failed",
               side_effect=lambda **kw: emitted_r2_fail.append(kw)), \
         patch.dict("os.environ", {"DATABASE_URL": "postgresql://test/test", "SOS_ENV": "test"}):

        mock_asyncpg.connect = AsyncMock(return_value=mock_conn)
        mock_asyncpg.UniqueViolationError = Exception

        result = await handle_payment_intent_succeeded(pi)

    assert result["ok"] is True
    assert result["knight_id"] == "agent:acme-knight"
    assert result["qnft_uri"] is None
    assert len(emitted_r2_fail) == 1


# ---------------------------------------------------------------------------
# TC-G73-f: R2 upload failure post-commit → DB intact + emit + URI NULL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_g73_f_r2_upload_failure_post_commit() -> None:
    """TC-G73-f: R2 upload fails post-commit → knight minted, URI NULL, emit fires."""
    from sos.services.billing.webhook import handle_payment_intent_succeeded
    from sos.services.billing.qnft_image import QnftR2UploadError

    pi = _make_payment_intent()
    contract = _make_contract_row()
    mock_conn = _make_mock_conn(fetchrow_return=contract)

    execute_sqls: list[str] = []

    async def _track_execute(sql, *args):
        execute_sqls.append(sql)

    mock_conn.execute = AsyncMock(side_effect=_track_execute)

    emitted_r2_fail: list[dict] = []

    with patch("sos.services.billing.webhook.asyncpg") as mock_asyncpg, \
         patch("sos.services.billing.webhook.mint_knight_programmatic",
               return_value={"ok": True, "knight_id": "agent:acme-knight",
                             "knight_slug": "acme-knight", "qnft_uri": "qnft:acme-knight:abc",
                             "error": None, "skipped": False, "vector_16d": [0.0] * 16}), \
         patch("sos.services.billing.webhook.emit_knight_minted"), \
         patch("sos.services.billing.webhook.emit_stripe_webhook"), \
         patch("sos.services.billing.qnft_image.generate_qnft_image",
               return_value=b"\x89PNG\r\n\x00" * 10), \
         patch("sos.services.billing.qnft_image.upload_qnft_to_r2",
               side_effect=QnftR2UploadError("R2 connection refused")), \
         patch("sos.observability.sprint_telemetry.emit_qnft_r2_upload_failed",
               side_effect=lambda **kw: emitted_r2_fail.append(kw)), \
         patch.dict("os.environ", {"DATABASE_URL": "postgresql://test/test", "SOS_ENV": "test"}):

        mock_asyncpg.connect = AsyncMock(return_value=mock_conn)
        mock_asyncpg.UniqueViolationError = Exception

        result = await handle_payment_intent_succeeded(pi)

    assert result["ok"] is True
    assert result["knight_id"] == "agent:acme-knight"
    assert result["qnft_uri"] is None
    assert any("status='processed'" in sql for sql in execute_sqls)
    assert not any("resulting_knight_qnft_uri" in sql for sql in execute_sqls)
    assert len(emitted_r2_fail) == 1


# ---------------------------------------------------------------------------
# TC-G73-f2: full happy path → URI updated to R2 URL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_g73_f2_happy_path_uri_updated() -> None:
    """TC-G73-f2: full happy path → image gen → R2 upload → URI UPDATE → URL in result."""
    from sos.services.billing.webhook import handle_payment_intent_succeeded

    pi = _make_payment_intent()
    contract = _make_contract_row()
    mock_conn = _make_mock_conn(fetchrow_return=contract)

    execute_sqls: list[str] = []

    async def _track_execute(sql, *args):
        execute_sqls.append(sql)

    mock_conn.execute = AsyncMock(side_effect=_track_execute)

    r2_url = "https://pub-f1c03761534049138c800f993e83265c.r2.dev/qnft/acme-knight/v1.png"

    with patch("sos.services.billing.webhook.asyncpg") as mock_asyncpg, \
         patch("sos.services.billing.webhook.mint_knight_programmatic",
               return_value={"ok": True, "knight_id": "agent:acme-knight",
                             "knight_slug": "acme-knight", "qnft_uri": "qnft:acme-knight:abc",
                             "error": None, "skipped": False, "vector_16d": [0.0] * 16}), \
         patch("sos.services.billing.webhook.emit_knight_minted"), \
         patch("sos.services.billing.webhook.emit_stripe_webhook"), \
         patch("sos.services.billing.qnft_image.generate_qnft_image",
               return_value=b"\x89PNG\r\n\x00" * 10), \
         patch("sos.services.billing.qnft_image.upload_qnft_to_r2",
               return_value=r2_url), \
         patch.dict("os.environ", {"DATABASE_URL": "postgresql://test/test", "SOS_ENV": "test"}):

        mock_asyncpg.connect = AsyncMock(return_value=mock_conn)
        mock_asyncpg.UniqueViolationError = Exception

        result = await handle_payment_intent_succeeded(pi)

    assert result["ok"] is True
    assert result["knight_id"] == "agent:acme-knight"
    assert result["qnft_uri"] == r2_url
    assert any("resulting_knight_qnft_uri" in sql for sql in execute_sqls)
