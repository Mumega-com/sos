"""QNFT image generation + R2 upload — Sprint 007 G73 v0.4.

generate_qnft_image(agent_id, vector_16d, cause) → bytes
upload_qnft_to_r2(principal_id, image_bytes) → str (R2 URL)

Uses existing sos/services/identity/avatar.py:AvatarGenerator (procedural,
PIL-based, 16D vector → sacred geometry PNG). NO external API required.
R2 credentials reuse the audit_anchor boto3 pattern.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from sos.services.identity.avatar import AvatarGenerator, PIL_AVAILABLE  # noqa: E402

log = logging.getLogger("sos.billing.qnft_image")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class QnftImageGenerationError(RuntimeError):
    """QNFT image generation failed."""

    def __init__(self, message: str, *, step: str = "unknown", **context: Any):
        super().__init__(message)
        self.step = step
        self.context = context


class QnftR2UploadError(RuntimeError):
    """R2 upload of QNFT image failed."""


# ---------------------------------------------------------------------------
# generate_qnft_image — AvatarGenerator → bytes
# ---------------------------------------------------------------------------

_UV16D_FIELDS = [
    "p", "e", "mu", "v", "n", "delta", "r", "phi",
    "pt", "et", "mut", "vt", "nt", "deltat", "rt", "phit",
]


def _vector_to_uv16d(vector_16d: list[float]) -> Any:
    """Convert a 16-element float list to UV16D.

    knight_mint.py generates values in [-1, 1]; UV16D expects [0, 1].
    Normalize: (val + 1) / 2.
    """
    from sos.contracts.identity import UV16D

    normalized = [(v + 1.0) / 2.0 for v in vector_16d[:16]]
    # Pad to 16 if shorter
    while len(normalized) < 16:
        normalized.append(0.5)

    field_dict = {field: normalized[i] for i, field in enumerate(_UV16D_FIELDS)}
    return UV16D.from_dict(field_dict)


def generate_qnft_image(
    agent_id: str,
    vector_16d: list[float],
    cause: str,
) -> bytes:
    """Generate QNFT identity image via AvatarGenerator.

    Uses the existing procedural PIL-based generator (sacred geometry from
    16D vector). Returns raw PNG image bytes.

    Raises:
        QnftImageGenerationError: on PIL missing (step="pil_missing") or
            generation failure (step="generation").
    """
    if not PIL_AVAILABLE:
        raise QnftImageGenerationError(
            "PIL/Pillow not installed — cannot generate QNFT image",
            step="pil_missing",
        )

    uv = _vector_to_uv16d(vector_16d)

    try:
        generator = AvatarGenerator()
        result = generator.generate(
            agent_id=agent_id,
            uv=uv,
            alpha_drift=0.0,
            event_type="knight_mint",
        )
    except Exception as exc:
        raise QnftImageGenerationError(
            f"AvatarGenerator.generate failed: {exc}",
            step="generation",
        ) from exc

    if not result.get("success"):
        raise QnftImageGenerationError(
            "AvatarGenerator.generate returned success=False",
            step="generation",
        )

    # If image_available is False (PIL fallback), raise
    if result.get("image_available") is False:
        raise QnftImageGenerationError(
            "PIL not available — metadata-only generation",
            step="pil_missing",
        )

    filepath = result.get("path")
    if not filepath:
        raise QnftImageGenerationError(
            "AvatarGenerator returned no file path",
            step="generation",
        )

    # Read PNG bytes from the generated file
    try:
        image_bytes = Path(filepath).read_bytes()
    except Exception as exc:
        raise QnftImageGenerationError(
            f"Failed to read generated image at {filepath}: {exc}",
            step="generation",
        ) from exc

    if not image_bytes:
        raise QnftImageGenerationError(
            f"Generated image at {filepath} is empty (0 bytes)",
            step="generation",
        )

    log.info("qnft_image: generated %d bytes from AvatarGenerator at %s", len(image_bytes), filepath)
    return image_bytes


# ---------------------------------------------------------------------------
# upload_qnft_to_r2 — boto3 PUT to R2 public bucket
# ---------------------------------------------------------------------------

_QNFT_R2_BUCKET = os.environ.get("QNFT_R2_BUCKET", "mumega-qnft")
_QNFT_R2_PUBLIC_URL = os.environ.get(
    "QNFT_R2_PUBLIC_URL",
    "https://pub-f1c03761534049138c800f993e83265c.r2.dev",
)


def _build_r2_client() -> Any:
    """Return a boto3 S3 client pointed at Cloudflare R2.

    Reuses the same pattern as audit_anchor.py.
    """
    import boto3

    account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID") or os.environ.get("CF_ACCOUNT_ID")
    if not account_id:
        raise QnftR2UploadError("CLOUDFLARE_ACCOUNT_ID / CF_ACCOUNT_ID not set")

    endpoint_url = f"https://{account_id}.r2.cloudflarestorage.com"
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=os.environ.get("R2_ACCESS_KEY_ID", ""),
        aws_secret_access_key=os.environ.get("R2_SECRET_ACCESS_KEY", ""),
        region_name="auto",
    )


def upload_qnft_to_r2(principal_id: str, image_bytes: bytes) -> str:
    """Upload QNFT image to R2 at deterministic key.

    Key: qnft/{principal_id}/v1.png
    Idempotent: re-upload overwrites cleanly (repair pass uses same key).

    Returns: public URL
    Raises: QnftR2UploadError on upload failure.
    """
    key = f"qnft/{principal_id}/v1.png"
    bucket = os.environ.get("QNFT_R2_BUCKET", _QNFT_R2_BUCKET)
    public_url = os.environ.get("QNFT_R2_PUBLIC_URL", _QNFT_R2_PUBLIC_URL)

    log.info("qnft_image: uploading %d bytes to R2 key=%s bucket=%s", len(image_bytes), key, bucket)

    try:
        s3_client = _build_r2_client()
        s3_client.put_object(
            Bucket=bucket,
            Key=key,
            Body=image_bytes,
            ContentType="image/png",
        )
    except QnftR2UploadError:
        raise
    except Exception as exc:
        raise QnftR2UploadError(
            f"R2 upload failed for key={key}: {exc}"
        ) from exc

    url = f"{public_url.rstrip('/')}/{key}"
    log.info("qnft_image: uploaded to %s", url)
    return url
