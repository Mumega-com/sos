from __future__ import annotations

import importlib
import inspect
import json
import re
import sqlite3
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sos.contracts.squad import (
    LoadingLevel,
    SkillDescriptor,
    SkillExecutionResult,
    SkillMatch,
    SkillStatus,
    SquadTask,
    TrustTier,
)
from sos.observability.logging import get_logger
from sos.services.squad.service import DEFAULT_TENANT_ID, SquadBus, SquadDB

try:
    import yaml
except ImportError:
    yaml = None

try:
    import jsonschema
except ImportError:
    jsonschema = None


log = get_logger("squad_skills")


def _loads(value: str | None, fallback: Any) -> Any:
    return json.loads(value) if value else fallback


def _dumps(value: Any) -> str:
    return json.dumps(value)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _is_deprecated(skill: SkillDescriptor) -> bool:
    if skill.status == SkillStatus.DEPRECATED:
        return True
    deprecated_at = _parse_iso(skill.deprecated_at)
    return deprecated_at is not None and deprecated_at <= _now()


def _slugify(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.strip().lower())
    return normalized.strip("-")


def row_to_skill(row: sqlite3.Row) -> SkillDescriptor:
    return SkillDescriptor(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        input_schema=_loads(row["input_schema_json"], {}),
        output_schema=_loads(row["output_schema_json"], {}),
        labels=_loads(row["labels_json"], []),
        keywords=_loads(row["keywords_json"], []),
        entrypoint=row["entrypoint"],
        skill_dir=row["skill_dir"] or "",
        required_inputs=_loads(row["required_inputs_json"], []),
        status=SkillStatus(row["status"]),
        trust_tier=TrustTier(int(row["trust_tier"])),
        loading_level=LoadingLevel(int(row["loading_level"])),
        fuel_grade=row["fuel_grade"],
        version=row["version"],
        deprecated_at=row["deprecated_at"],
    )


def _validate_payload(schema: dict[str, Any], payload: dict[str, Any], phase: str) -> None:
    if not schema:
        return
    if not jsonschema:
        log.warn("jsonschema unavailable; skipping validation", phase=phase)
        return
    jsonschema.validate(instance=payload, schema=schema)


class SquadSkillService:
    """Skill registry on top of ``squad_skills``.

    Schema is owned by Alembic — the full column set (including
    ``input_schema_json`` / ``output_schema_json`` / ``trust_tier`` /
    ``loading_level`` / ``skill_dir`` / ``deprecated_at``) is created
    by the Squad service's baseline revision. Run migrations first.
    """

    def __init__(self, db: SquadDB | None = None, bus: SquadBus | None = None):
        self.db = db or SquadDB()
        self.bus = bus or SquadBus()

    def register(
        self,
        descriptor: SkillDescriptor,
        actor: str = "system",
        tenant_id: str = DEFAULT_TENANT_ID,
    ) -> SkillDescriptor:
        required_inputs = descriptor.required_inputs or list(descriptor.input_schema.get("required", []))
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO squad_skills (
                    id, tenant_id, name, description, input_schema_json, output_schema_json,
                    labels_json, keywords_json, entrypoint, skill_dir,
                    required_inputs_json, status, trust_tier, loading_level,
                    fuel_grade, version, deprecated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    descriptor.id,
                    tenant_id,
                    descriptor.name,
                    descriptor.description,
                    _dumps(descriptor.input_schema),
                    _dumps(descriptor.output_schema),
                    _dumps(descriptor.labels),
                    _dumps(descriptor.keywords),
                    descriptor.entrypoint,
                    descriptor.skill_dir,
                    _dumps(required_inputs),
                    descriptor.status.value,
                    int(descriptor.trust_tier),
                    int(descriptor.loading_level),
                    descriptor.fuel_grade,
                    descriptor.version,
                    descriptor.deprecated_at,
                ),
            )
        stored = SkillDescriptor(
            id=descriptor.id,
            name=descriptor.name,
            description=descriptor.description,
            input_schema=descriptor.input_schema,
            output_schema=descriptor.output_schema,
            labels=descriptor.labels,
            keywords=descriptor.keywords,
            entrypoint=descriptor.entrypoint,
            skill_dir=descriptor.skill_dir,
            required_inputs=required_inputs,
            status=descriptor.status,
            trust_tier=descriptor.trust_tier,
            loading_level=descriptor.loading_level,
            fuel_grade=descriptor.fuel_grade,
            version=descriptor.version,
            deprecated_at=descriptor.deprecated_at,
        )
        self.bus.emit("skill.registered", "skills", actor, asdict(stored))
        return stored

    def list(
        self,
        status: SkillStatus | None = None,
        tenant_id: str | None = DEFAULT_TENANT_ID,
    ) -> list[SkillDescriptor]:
        query = "SELECT * FROM squad_skills WHERE 1=1"
        params: list[Any] = []
        if tenant_id is not None:
            query += " AND tenant_id = ?"
            params.append(tenant_id)
        if status:
            query += " AND status = ?"
            params.append(status.value)
        query += " ORDER BY name ASC"
        with self.db.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [row_to_skill(row) for row in rows]

    def match(
        self,
        task: SquadTask,
        min_trust_tier: int = 1,
        tenant_id: str | None = DEFAULT_TENANT_ID,
    ) -> list[SkillMatch]:
        labels = {label.lower() for label in task.labels}
        text = f"{task.title} {task.description}".lower()
        matches: list[SkillMatch] = []
        for skill in self.list(status=SkillStatus.ACTIVE, tenant_id=tenant_id):
            if int(skill.trust_tier) < min_trust_tier:
                continue
            if _is_deprecated(skill):
                continue
            label_hits = [label for label in skill.labels if label.lower() in labels]
            if label_hits:
                confidence = min(1.0, 0.6 + 0.2 * len(label_hits))
                matches.append(
                    SkillMatch(
                        skill_id=skill.id,
                        skill_name=skill.name,
                        confidence=confidence,
                        match_reason=f"label:{label_hits[0]}",
                    )
                )
                continue
            keyword_hits = [kw for kw in skill.keywords if kw.lower() in text]
            if keyword_hits:
                confidence = min(0.75, 0.25 + 0.1 * len(keyword_hits))
                matches.append(
                    SkillMatch(
                        skill_id=skill.id,
                        skill_name=skill.name,
                        confidence=confidence,
                        match_reason=f"keyword:{keyword_hits[0]}",
                    )
                )
        return sorted(matches, key=lambda item: item.confidence, reverse=True)

    def execute(
        self,
        task: SquadTask,
        context: dict[str, Any],
        actor: str = "system",
        min_trust_tier: int = 1,
        tenant_id: str | None = DEFAULT_TENANT_ID,
    ) -> SkillExecutionResult:
        matches = self.match(task, min_trust_tier=min_trust_tier, tenant_id=tenant_id)
        if not matches:
            return SkillExecutionResult(task_id=task.id, skill_id="", success=False, error="No matching skill")
        skill = next(skill for skill in self.list(tenant_id=tenant_id) if skill.id == matches[0].skill_id)
        module_name, func_name = skill.entrypoint.split(":", 1)
        started = time.time()
        try:
            _validate_payload(skill.input_schema, context, "input")
            fn = getattr(importlib.import_module(module_name), func_name)
            output = fn(task, context)
            if inspect.isawaitable(output):
                raise TypeError("Skill entrypoints must be sync functions: def run(task, context)")
            if isinstance(output, SkillExecutionResult):
                result = output
            else:
                result = SkillExecutionResult(
                    task_id=task.id,
                    skill_id=skill.id,
                    success=True,
                    output=output if isinstance(output, dict) else {"result": output},
                    summary=f"Executed {skill.name}",
                )
            _validate_payload(skill.output_schema, result.output, "output")
        except Exception as exc:
            result = SkillExecutionResult(
                task_id=task.id,
                skill_id=skill.id,
                success=False,
                error=str(exc),
                summary=f"Execution failed for {skill.name}",
            )
        result.attempt = task.attempt
        result.duration_ms = int((time.time() - started) * 1000)
        self.bus.emit("skill.executed", task.squad_id, actor, asdict(result))
        return result

    def load_skill_from_dir(self, skill_dir: str | Path) -> SkillDescriptor:
        directory = Path(skill_dir).expanduser().resolve()
        skill_md = directory / "SKILL.md"
        if not skill_md.exists():
            raise FileNotFoundError(f"SKILL.md not found in {directory}")
        raw = skill_md.read_text()
        if not raw.startswith("---\n"):
            raise ValueError(f"SKILL.md missing YAML frontmatter: {skill_md}")
        if yaml is None:
            raise RuntimeError("PyYAML not installed; cannot parse SKILL.md frontmatter")
        parts = raw.split("---", 2)
        if len(parts) < 3:
            raise ValueError(f"Invalid frontmatter format in {skill_md}")
        frontmatter = yaml.safe_load(parts[1]) or {}
        skill_name = str(frontmatter.get("name") or directory.name)
        return SkillDescriptor(
            id=_slugify(skill_name),
            name=skill_name,
            description=str(frontmatter.get("description") or "").strip(),
            input_schema=frontmatter.get("input_schema") or {},
            output_schema=frontmatter.get("output_schema") or {},
            labels=list(frontmatter.get("labels") or []),
            keywords=list(frontmatter.get("keywords") or []),
            entrypoint=str(frontmatter.get("entrypoint") or "").strip(),
            skill_dir=str(directory),
            required_inputs=list(frontmatter.get("required_inputs") or (frontmatter.get("input_schema") or {}).get("required", [])),
            status=SkillStatus(str(frontmatter.get("status") or SkillStatus.ACTIVE.value)),
            trust_tier=TrustTier(int(frontmatter.get("trust_tier") or TrustTier.VENDOR.value)),
            loading_level=LoadingLevel(int(frontmatter.get("loading_level") or LoadingLevel.INSTRUCTIONS.value)),
            fuel_grade=str(frontmatter.get("fuel_grade") or "diesel"),
            version=str(frontmatter.get("version") or "1.0.0"),
            deprecated_at=frontmatter.get("deprecated_at"),
        )

    def load_decorated_skills(self, actor: str = "system") -> int:
        """Load all skills registered via @skill decorator into the database."""
        try:
            from sos.skills.decorator import REGISTERED_SKILLS
        except ImportError:
            return 0
        count = 0
        for skill_id, descriptor in REGISTERED_SKILLS.items():
            try:
                self.register(descriptor, actor=actor)
                count += 1
            except Exception:
                pass  # skip duplicates
        return count
