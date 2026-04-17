"""Skill registry — filesystem-backed first implementation.

Each skill is a JSON file in this directory conforming to SkillCard v1.
The `Registry` class loads them on demand, validates via Pydantic, and
supports lookup by id + simple update-earnings / update-verification
operations.

This is the MVP storage for skill provenance. A future backend might
promote to SQLite or a Worker-backed API; the SkillCard v1 contract is
the stable surface that makes either migration non-breaking.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

from sos.contracts.skill_card import SkillCard


_REGISTRY_DIR = Path(__file__).resolve().parent


def _card_files() -> Iterator[Path]:
    for p in sorted(_REGISTRY_DIR.glob("*.json")):
        yield p


class Registry:
    """Filesystem-backed SkillCard registry."""

    def __init__(self, directory: Path | None = None) -> None:
        self.dir = directory or _REGISTRY_DIR
        self.dir.mkdir(parents=True, exist_ok=True)

    def list(self) -> list[SkillCard]:
        """Return all valid SkillCards. Invalid JSON or schema-failing files are skipped silently."""
        out: list[SkillCard] = []
        for p in sorted(self.dir.glob("*.json")):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                out.append(SkillCard.model_validate(data))
            except Exception:
                continue
        return out

    def get(self, skill_id: str) -> SkillCard | None:
        for p in sorted(self.dir.glob("*.json")):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if data.get("id") == skill_id:
                    return SkillCard.model_validate(data)
            except Exception:
                continue
        return None

    def save(self, card: SkillCard, filename_hint: str | None = None) -> Path:
        """Write a card to disk. Filename defaults to `<id>.json`."""
        fname = filename_hint or f"{card.id}.json"
        path = self.dir / fname
        path.write_text(
            json.dumps(card.model_dump(exclude_none=True), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    def record_invocation(
        self,
        skill_id: str,
        tenant: str,
        amount_micros: int,
    ) -> SkillCard | None:
        """Bump the skill's earnings record after one invocation.

        Materialized view — the source of truth lives in the UsageLog.
        This method keeps the SkillCard's `earnings` field current so
        dashboard reads don't have to aggregate on every page load.
        """
        card = self.get(skill_id)
        if card is None:
            return None
        earnings = card.earnings.model_copy() if card.earnings else None
        if earnings is None:
            from sos.contracts.skill_card import EarningsInfo
            earnings = EarningsInfo()
        earnings.total_invocations = (earnings.total_invocations or 0) + 1
        earnings.total_earned_micros = (earnings.total_earned_micros or 0) + amount_micros
        earnings.last_invocation_at = SkillCard.now_iso()
        tenants = dict(earnings.invocations_by_tenant or {})
        tenants[tenant] = tenants.get(tenant, 0) + 1
        earnings.invocations_by_tenant = tenants

        # Round-trip through model_validate so model_validators re-run (invariants enforced).
        new_data = card.model_dump(exclude_none=True)
        new_data["earnings"] = earnings.model_dump(exclude_none=True)
        new_data["updated_at"] = SkillCard.now_iso()
        updated = SkillCard.model_validate(new_data)

        # Persist back to the same file we loaded from
        for p in sorted(self.dir.glob("*.json")):
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
                if raw.get("id") == skill_id:
                    p.write_text(
                        json.dumps(updated.model_dump(exclude_none=True), indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )
                    return updated
            except Exception:
                continue
        return updated
