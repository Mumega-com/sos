"""QNFT image generation + R2 upload — Sprint 007 G73 v0.3.

generate_qnft_image(descriptor, vector_16d, cause) → bytes
upload_qnft_to_r2(principal_id, image_bytes) → str (R2 URL)

GEMINI_API_KEY must be set (checked at call time — fail fast).
R2 credentials reuse the audit_anchor boto3 pattern.

Vendor: Google Imagen 4 via genai SDK (v0.3 swap from DALL-E 3).
"""
from __future__ import annotations

import logging
import os
from typing import Any

from google import genai
from google.genai import types as genai_types

log = logging.getLogger("sos.billing.qnft_image")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class QnftImageGenerationError(RuntimeError):
    """Image generation failed."""

    def __init__(self, message: str, *, step: str = "unknown", **context: Any):
        super().__init__(message)
        self.step = step
        self.context = context


class QnftR2UploadError(RuntimeError):
    """R2 upload of QNFT image failed."""


# ---------------------------------------------------------------------------
# 16D vector → aesthetic adjectives (pure-string mapping, no ML)
# ---------------------------------------------------------------------------

_DIM_LABELS = [
    ("warm", "cool"),            # dim 0: color temperature
    ("organic", "geometric"),     # dim 1: shape language
    ("dense", "sparse"),          # dim 2: visual density
    ("luminous", "muted"),        # dim 3: brightness
    ("flowing", "angular"),       # dim 4: line quality
    ("natural", "synthetic"),     # dim 5: material feel
    ("deep", "shallow"),          # dim 6: depth
    ("complex", "minimal"),       # dim 7: detail level
    ("bold", "subtle"),           # dim 8: contrast
    ("ancient", "futuristic"),    # dim 9: temporal feel
    ("textured", "smooth"),       # dim 10: surface
    ("expansive", "intimate"),    # dim 11: scale
    ("vibrant", "monochrome"),    # dim 12: saturation
    ("grounded", "ethereal"),     # dim 13: weight
    ("structured", "chaotic"),    # dim 14: order
    ("radiant", "shadowed"),      # dim 15: light direction
]


def _vector_to_adjectives(vector_16d: list[float], top_n: int = 6) -> list[str]:
    """Map 16D vector to aesthetic adjectives.

    For each dimension, pick the adjective based on sign (positive → first,
    negative → second). Rank by absolute value, return top_n strongest.
    """
    pairs: list[tuple[float, str]] = []
    for i, val in enumerate(vector_16d[:16]):
        if i < len(_DIM_LABELS):
            pos_label, neg_label = _DIM_LABELS[i]
            label = pos_label if val >= 0 else neg_label
            pairs.append((abs(val), label))
    pairs.sort(reverse=True)
    return [label for _, label in pairs[:top_n]]


# ---------------------------------------------------------------------------
# generate_qnft_image — Imagen 4 → bytes
# ---------------------------------------------------------------------------


def _compose_prompt(descriptor: str, vector_16d: list[float], cause: str) -> str:
    """Build image generation prompt from descriptor + 16D vector + cause.

    The prompt produces an abstract identity portrait — not a face, but a
    visual signature that represents the agent's role and character.
    """
    adjectives = _vector_to_adjectives(vector_16d)
    adj_str = ", ".join(adjectives)

    return (
        f"Create an abstract digital identity portrait for an AI agent. "
        f"This agent is described as: {descriptor}. "
        f"Their purpose: {cause} "
        f"Visual style: {adj_str}. "
        f"The image should be a sophisticated abstract composition — NOT a face or avatar. "
        f"Think generative art: geometric patterns, data flows, neural networks, or cosmic "
        f"structures that evoke the agent's role. Use a dark background with accent lighting. "
        f"No text, no logos, no words. Square format, high contrast, production quality."
    )


def generate_qnft_image(
    descriptor: str,
    vector_16d: list[float],
    cause: str,
) -> bytes:
    """Generate QNFT identity image via Google Imagen 4.

    Returns raw PNG image bytes directly (Imagen returns bytes, no URL fetch).

    Raises:
        QnftImageGenerationError: on API failure (step="api_call") or
            image extraction failure (step="download" — defensive, covers
            SDK response shape changes).
    """
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise QnftImageGenerationError(
            "GEMINI_API_KEY not set — cannot generate QNFT image",
            step="api_call",
        )

    prompt = _compose_prompt(descriptor, vector_16d, cause)
    log.info("qnft_image: generating Imagen 4 image (prompt length=%d)", len(prompt))

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_images(
            model="imagen-4.0-generate-001",
            prompt=prompt,
            config=genai_types.GenerateImagesConfig(
                number_of_images=1,
                output_mime_type="image/png",
            ),
        )
    except Exception as exc:
        raise QnftImageGenerationError(
            f"Imagen 4 API call failed: {exc}",
            step="api_call",
            error=str(exc),
        ) from exc

    # Extract image bytes from response
    try:
        if not response.generated_images or len(response.generated_images) == 0:
            raise QnftImageGenerationError(
                "Imagen 4 returned no images",
                step="download",
            )
        image_data = response.generated_images[0].image
        if image_data is None:
            raise QnftImageGenerationError(
                "Imagen 4 returned None image data",
                step="download",
            )
        image_bytes = image_data.image_bytes
        if not image_bytes:
            raise QnftImageGenerationError(
                "Imagen 4 returned empty image bytes",
                step="download",
            )
    except QnftImageGenerationError:
        raise
    except Exception as exc:
        raise QnftImageGenerationError(
            f"Image extraction failed: {exc}",
            step="download",
            error=str(exc),
        ) from exc

    log.info("qnft_image: generated %d bytes", len(image_bytes))
    return image_bytes


# ---------------------------------------------------------------------------
# upload_qnft_to_r2 — boto3 PUT to R2 public bucket
# ---------------------------------------------------------------------------

_QNFT_R2_BUCKET = os.environ.get("QNFT_R2_BUCKET", "mumega-qnft")
_QNFT_R2_PUBLIC_URL = os.environ.get(
    "QNFT_R2_PUBLIC_URL",
    "https://qnft.mumega.com",
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

    Returns: public URL (e.g., https://qnft.mumega.com/qnft/{principal_id}/v1.png)
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
