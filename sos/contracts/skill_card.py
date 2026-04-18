"""SkillCard v1 — Pydantic v2 binding for the SOS / ToRivers skill registry schema.

Cross-language source of truth (JSON Schema):
  sos/contracts/schemas/skill_card_v1.json

This module is the Python binding; the JSON Schema above is authoritative.

SkillCard is a **provenance + commerce overlay** on the execution contract
defined at sos/contracts/squad.py::SkillDescriptor. The canonical execution
fields (entrypoint, trust_tier, loading_level, fuel_grade, required_inputs,
input_schema, output_schema as source of truth) live on SkillDescriptor.
SkillCard references it by `skill_descriptor_id`.

input_schema / output_schema may be echoed here for display / marketplace
rendering, but the source of truth for execution is the referenced
SkillDescriptor.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SCHEMA_PATH = Path(__file__).parent / "schemas" / "skill_card_v1.json"

_SEMVER_PATTERN = r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
_AGENT_PATTERN = r"^agent:[a-z][a-z0-9-]*$"
# Verifier pattern tightened (Athena review 2026-04-17): must start with a
# letter, matching author_agent / Agent Card agent_id pattern, so identifiers
# are consistent across contracts.
_VERIFIER_PATTERN = r"^(agent|human):[a-z][a-z0-9-]*$"
_CURRENCY_PATTERN = r"^[A-Z0-9]{2,10}$"
# Artifact CID ref or legacy engram slug. CID is the migration target.
_SAMPLE_OUTPUT_REF_PATTERN = r"^(artifact:[0-9a-f]{64}|engram:[a-z0-9][a-z0-9-]{0,62})$"
# Primary artifact CID — bare 64-char hex (no prefix, just the SHA-256).
_PRIMARY_ARTIFACT_CID_PATTERN = r"^[0-9a-f]{64}$"
# id: either UUID v4 or a constrained slug. Blocks ' ', '..', '../../...'.
_ID_PATTERN = (
    r"^[a-z0-9][a-z0-9_-]{2,63}$"
    r"|^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


# ---------------------------------------------------------------------------
# Schema loader
# ---------------------------------------------------------------------------


def load_schema() -> dict[str, Any]:
    """Return the parsed JSON Schema dict from the canonical path."""
    return json.loads(_SCHEMA_PATH.read_text())


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class WitnessEvent(BaseModel):
    """A single witness event carrying CoherencePhysics energy.

    Implements the witness output from sos/kernel/physics.py::CoherencePhysics.compute_collapse_energy.
    Per RC-7 physics: vote ∈ {+1, -1}; omega = exp(-lambda*(t-t_min)); delta_c = vote × omega × agent_coherence × 0.1.
    """

    model_config = ConfigDict(strict=False)

    witness_id: str = Field(description="agent:<slug> or human:<slug> — pattern enforced")
    vote: Literal[-1, 1]  # +1 = verified, -1 = rejected
    latency_ms: float = Field(ge=0.0, description="time to decide (used by RC-7)")
    omega: float = Field(description="certainty, 0..1 — output of calculate_will_magnitude")
    delta_c: float = Field(description="coherence change, bounded by physics")
    agent_coherence_snapshot: float = Field(description="C of the target at time of witness")
    signature: Literal["RC-7_COMPLIANT"] = "RC-7_COMPLIANT"
    occurred_at: str = Field(description="ISO 8601")
    sample_output_ref: Optional[str] = Field(
        default=None,
        description="artifact: or engram: the witness evaluated",
    )

    @field_validator("witness_id")
    @classmethod
    def _check_witness_id_pattern(cls, v: str) -> str:
        if not re.match(r"^(agent|human):[a-z][a-z0-9-]*$", v):
            raise ValueError(
                f"witness_id {v!r} must match ^(agent|human):[a-z][a-z0-9-]*$"
            )
        return v

    @field_validator("occurred_at")
    @classmethod
    def _parse_occurred_at(cls, v: str) -> str:
        datetime.fromisoformat(v.replace("Z", "+00:00"))
        return v

    @field_validator("sample_output_ref")
    @classmethod
    def _check_sample_output_ref(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not re.match(_SAMPLE_OUTPUT_REF_PATTERN, v):
            raise ValueError(
                f"sample_output_ref {v!r} does not match {_SAMPLE_OUTPUT_REF_PATTERN}. "
                "Use 'artifact:<sha256-64>' (canonical) or 'engram:<slug>' (legacy)."
            )
        return v


class LineageEntry(BaseModel):
    """One upstream skill this skill was derived from."""

    model_config = ConfigDict(strict=False)

    parent_skill_id: str = Field(min_length=1, description="id of the upstream skill")
    relation: Literal["forked", "refined", "composed", "inspired_by"]
    notes: Optional[str] = Field(default=None, max_length=500)


class EarningsInfo(BaseModel):
    """Materialized view of lifetime earnings across all invocations."""

    model_config = ConfigDict(strict=False)

    total_invocations: int = Field(default=0, ge=0)
    total_earned_micros: int = Field(
        default=0, ge=0, description="Integer micros (1e-6 currency units) of gross lifetime earnings."
    )
    currency: str = Field(
        default="USD",
        pattern=_CURRENCY_PATTERN,
        description="Upper-case 2-10 alphanumeric. USD | MIND | operator-defined.",
    )
    last_invocation_at: Optional[str] = None
    invocations_by_tenant: Optional[dict[str, int]] = None

    @field_validator("last_invocation_at")
    @classmethod
    def _parse_last_invocation_at(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            datetime.fromisoformat(v.replace("Z", "+00:00"))
        return v


class VerificationInfo(BaseModel):
    """Proof that outputs from this skill have been checked."""

    model_config = ConfigDict(strict=False)

    status: Literal["unverified", "auto_verified", "human_verified", "disputed"] = "unverified"
    sample_output_refs: list[str] = Field(default_factory=list, max_length=50)
    primary_artifact_cid: Optional[str] = Field(
        default=None,
        pattern=_PRIMARY_ARTIFACT_CID_PATTERN,
        description=(
            "Optional first-class reference to the representative verified output. "
            "64-char lowercase hex SHA-256 (bare CID, no prefix). "
            "Resolves via ArtifactRegistry.get(cid)."
        ),
    )
    verified_by: list[str] = Field(
        default_factory=list,
        description="agent: or human: URIs of verifiers.",
    )
    verified_at: Optional[str] = None
    dispute_reason: Optional[str] = Field(default=None, max_length=2000)
    witness_events: list[WitnessEvent] = Field(
        default_factory=list,
        max_length=1000,
        description="Ordered log of WitnessEvents — each carries CoherencePhysics energy.",
    )

    @field_validator("sample_output_refs", mode="before")
    @classmethod
    def _check_sample_output_ref_patterns(cls, v: list[str]) -> list[str]:
        import re
        for item in v:
            if not re.match(_SAMPLE_OUTPUT_REF_PATTERN, item):
                raise ValueError(
                    f"sample_output_refs item {item!r} does not match "
                    f"{_SAMPLE_OUTPUT_REF_PATTERN}. "
                    "Use 'artifact:<sha256-64>' (canonical) or 'engram:<slug>' (legacy)."
                )
        return v

    @field_validator("verified_by", mode="before")
    @classmethod
    def _check_verifier_patterns(cls, v: list[str]) -> list[str]:
        import re
        for item in v:
            if not re.match(_VERIFIER_PATTERN, item):
                raise ValueError(f"verified_by item {item!r} does not match {_VERIFIER_PATTERN}")
        return v

    @field_validator("verified_at")
    @classmethod
    def _parse_verified_at(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            datetime.fromisoformat(v.replace("Z", "+00:00"))
        return v

    def total_delta_c(self) -> float:
        """Sum of delta_c across all witness events — gross coherence accumulated."""
        return sum(w.delta_c for w in self.witness_events)

    def weighted_omega(self) -> float:
        """Time-weighted average omega across witness events (recent witnesses count more).

        Simple equal-weight for now; time-weighting can be a later refinement.
        """
        if not self.witness_events:
            return 0.0
        return sum(w.omega for w in self.witness_events) / len(self.witness_events)

    def human_witnessed_count(self) -> int:
        """Count of witness events from human witnesses."""
        return sum(1 for w in self.witness_events if w.witness_id.startswith("human:"))

    def resolve_artifacts(self) -> "list[Any]":
        """Return ArtifactManifest objects for all artifact: prefixed refs.

        Imports ArtifactRegistry lazily to avoid circular imports.
        Non-artifact refs (e.g. engram:) are silently filtered out.
        Raises FileNotFoundError if an artifact CID is not found in the registry.
        """
        from sos.artifacts.registry import ArtifactRegistry  # lazy import

        registry = ArtifactRegistry()
        manifests = []
        for ref in self.sample_output_refs:
            if ref.startswith("artifact:"):
                cid = ref[len("artifact:"):]
                manifests.append(registry.get(cid))
        return manifests


class RevenueSplit(BaseModel):
    """Fractional revenue split; each key is optional, values 0..1.

    If any field is set, the populated fields must sum to 1.0 ±0.001
    (Athena gate 2026-04-17 — prevents silent under-payment when a card
    is written with only {"author": 0.1} and the rest drops to zero).
    """

    model_config = ConfigDict(strict=False)

    author: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    operator: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    network: Optional[float] = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _check_sum(self) -> "RevenueSplit":
        populated = [v for v in (self.author, self.operator, self.network) if v is not None]
        if not populated:
            return self  # all unset is valid (omit the field entirely)
        total = sum(populated)
        if abs(total - 1.0) > 0.001:
            raise ValueError(
                f"revenue_split fields must sum to 1.0 (tolerance ±0.001); got {total:.4f}"
            )
        return self


class CommerceInfo(BaseModel):
    """How this skill is sold on ToRivers / the marketplace."""

    model_config = ConfigDict(strict=False)

    price_per_call_micros: int = Field(default=0, ge=0)
    currency: str = Field(default="USD", pattern=_CURRENCY_PATTERN)
    revenue_split: Optional[RevenueSplit] = None
    marketplace_listed: bool = Field(default=False)


class RuntimeInfo(BaseModel):
    """Where and how the skill executes."""

    model_config = ConfigDict(strict=False)

    entry_point: Optional[str] = Field(default=None, max_length=500)
    backend: Optional[
        Literal[
            "claude-code",
            "cma",
            "openai-agents-sdk",
            "langgraph",
            "crewai",
            "local-python",
            "custom",
        ]
    ] = None
    timeout_seconds: Optional[int] = Field(default=None, ge=1, le=3600)
    memory_mb: Optional[int] = Field(default=None, ge=64)


# ---------------------------------------------------------------------------
# Top-level model
# ---------------------------------------------------------------------------


class SkillCard(BaseModel):
    """Provenance + commerce overlay for a skill in the SOS / ToRivers registry.

    The execution contract (input/output schemas, entrypoint, trust_tier, etc.)
    lives canonically on sos.contracts.squad.SkillDescriptor, referenced here
    by `skill_descriptor_id`. SkillCard carries who authored the skill, what it
    has earned, how it is sold, and its verification status.

    input_schema / output_schema are optional echo fields for display purposes;
    the source of truth for execution is the referenced SkillDescriptor.
    """

    model_config = ConfigDict(strict=False)

    # --- required ---
    schema_version: Literal["1"] = Field(
        default="1",
        description="SkillCard schema version. v1 cards carry '1' so future v1.x/v2 are distinguishable at read time.",
    )
    id: str = Field(pattern=_ID_PATTERN)
    skill_descriptor_id: str = Field(
        pattern=_ID_PATTERN,
        description=(
            "Reference to the canonical SkillDescriptor id in sos.contracts.squad. "
            "This is the execution contract — input/output schemas, entrypoint, "
            "trust_tier, loading_level, fuel_grade are authoritative there. "
            "Use resolve_descriptor() to fetch the live descriptor."
        ),
    )
    name: str = Field(min_length=1, max_length=200)
    version: str = Field(pattern=_SEMVER_PATTERN)
    author_agent: str = Field(pattern=_AGENT_PATTERN)
    created_at: str

    # --- optional echo fields (denormalized view of SkillDescriptor; display only) ---
    input_schema: Optional[dict[str, Any]] = Field(
        default=None,
        description=(
            "Denormalized view of SkillDescriptor.input_schema for display / marketplace "
            "rendering. Source of truth is the SkillDescriptor referenced by "
            "skill_descriptor_id. May be None when the card is stored without echoing schemas."
        ),
    )
    output_schema: Optional[dict[str, Any]] = Field(
        default=None,
        description=(
            "Denormalized view of SkillDescriptor.output_schema for display. "
            "Source of truth is the referenced SkillDescriptor."
        ),
    )

    # --- optional ---
    description: Optional[str] = Field(default=None, max_length=4000)
    tags: Optional[list[str]] = Field(default=None, max_length=20)
    authored_by_ai: bool = False
    lineage: Optional[list[LineageEntry]] = Field(default=None, max_length=20)
    updated_at: Optional[str] = None
    earnings: Optional[EarningsInfo] = None
    verification: Optional[VerificationInfo] = None
    required_tools: Optional[list[str]] = None
    required_models: Optional[list[str]] = None
    commerce: Optional[CommerceInfo] = None
    runtime: Optional[RuntimeInfo] = None
    metadata: Optional[dict[str, Any]] = None

    @field_validator("created_at", "updated_at")
    @classmethod
    def _parse_datetime(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            datetime.fromisoformat(v.replace("Z", "+00:00"))
        return v

    @field_validator("tags", "required_tools", "required_models", mode="after")
    @classmethod
    def _check_nonempty_items(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        if v is None:
            return v
        for item in v:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("list items must be non-empty strings")
        return v

    @field_validator("input_schema", "output_schema", mode="after")
    @classmethod
    def _check_schema_shape(cls, v: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
        """When echoed, input/output_schema must declare at least `$schema` or `type`.

        Prevents `{"input_schema": {"foo": "bar"}}` from validating — that's
        not a JSON Schema document, it's just a dict, and downstream
        validators will choke on it.

        None is valid — it means the card does not echo the schema; resolve the
        referenced SkillDescriptor via skill_descriptor_id to get the live schema.
        """
        if v is None:
            return v
        if "$schema" not in v and "type" not in v:
            raise ValueError("input_schema / output_schema must carry '$schema' or 'type'")
        return v

    @model_validator(mode="after")
    def _check_marketplace_invariants(self) -> "SkillCard":
        """marketplace-listed skills must have an entry_point.

        A listed skill with no runtime.entry_point is structurally unshippable
        (the marketplace can't invoke it). Enforce at contract boundary.
        """
        if self.commerce and self.commerce.marketplace_listed:
            if not self.runtime or not self.runtime.entry_point:
                raise ValueError(
                    "commerce.marketplace_listed=true requires runtime.entry_point"
                )
        return self

    def resolve_descriptor(self) -> "Any":
        """Return the canonical SkillDescriptor for this SkillCard.

        TODO(island-later): wire to squad service / SkillDescriptor registry.
        This is a stub — the actual lookup requires the squad-service client,
        which is not yet available at SkillCard construction time. A later island
        will inject the resolver via a SkillRegistry.resolve(card) pattern.

        Returns None until the wiring lands.
        """
        # TODO: replace with actual squad-service lookup
        return None

    @staticmethod
    def now_iso() -> str:
        """Return current UTC time as an ISO-8601 string."""
        return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Witness helper
# ---------------------------------------------------------------------------


def record_witness(
    verification: VerificationInfo,
    *,
    witness_id: str,
    vote: int,  # +1 or -1
    latency_ms: float,
    agent_coherence: float,
    sample_output_ref: Optional[str] = None,
) -> VerificationInfo:
    """Append a WitnessEvent to verification, computing physics via kernel.

    This is the canonical path for recording a witness: the physics lives in
    sos.kernel.physics.CoherencePhysics; this function wraps it + appends to
    the SkillCard's verification record. Auto-transitions verification.status:
      - first human positive vote: auto_verified -> human_verified
      - any disputed: disputed
    """
    from sos.kernel.physics import CoherencePhysics

    result = CoherencePhysics.compute_collapse_energy(vote, latency_ms, agent_coherence)
    event = WitnessEvent(
        witness_id=witness_id,
        vote=vote,
        latency_ms=latency_ms,
        omega=result["omega"],
        delta_c=result["delta_c"],
        agent_coherence_snapshot=agent_coherence,
        occurred_at=datetime.now(timezone.utc).isoformat(),
        sample_output_ref=sample_output_ref,
    )
    updated_events = list(verification.witness_events) + [event]
    new_status = verification.status
    if witness_id.startswith("human:") and vote > 0 and new_status in ("unverified", "auto_verified"):
        new_status = "human_verified"
    elif vote < 0:
        new_status = "disputed"
    return verification.model_copy(update={"witness_events": updated_events, "status": new_status})


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def parse_skill_card(data: dict[str, Any]) -> SkillCard:
    """Construct a SkillCard from a raw dict.

    Raises pydantic.ValidationError on malformed input.
    """
    return SkillCard.model_validate(data)
