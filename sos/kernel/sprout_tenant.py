from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SproutFile:
    path: str
    status: str
    kind: str


@dataclass(frozen=True)
class SproutResult:
    tenant_slug: str
    project_path: str
    pulse_source: str
    tech_stack: list[str]
    core_goals: list[str]
    files: list[SproutFile]
    warnings: list[str]
    summary: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "tenant_slug": self.tenant_slug,
            "project_path": self.project_path,
            "pulse_source": self.pulse_source,
            "tech_stack": self.tech_stack,
            "core_goals": self.core_goals,
            "files": [file.__dict__ for file in self.files],
            "warnings": self.warnings,
            "summary": self.summary,
        }


class SproutTenantEngine:
    """Generate the minimum Living Enterprise files for a project directory."""

    def __init__(
        self,
        *,
        use_gemini: bool = True,
        gemini_timeout_seconds: int = 120,
    ) -> None:
        self.use_gemini = use_gemini
        self.gemini_timeout_seconds = gemini_timeout_seconds

    def sprout(
        self,
        project_path: str,
        *,
        tenant_slug: str | None = None,
        overwrite_existing: bool = False,
    ) -> SproutResult:
        root = self._validate_project_path(project_path)
        slug = self._slugify(tenant_slug or root.name)
        warnings: list[str] = []

        pulse, pulse_source, pulse_warnings = self._pulse_project(root, slug)
        warnings.extend(pulse_warnings)

        tech_stack = self._normalize_list(pulse.get("tech_stack")) or self._detect_stack(root)
        core_goals = self._normalize_list(pulse.get("core_goals")) or self._infer_goals(root, slug)
        summary = str(pulse.get("summary") or self._default_summary(slug, tech_stack, core_goals))
        canvas_blocks = self._canvas_blocks(pulse, slug, tech_stack, core_goals)

        files = [
            self._write_file(
                root / "AGENTS.md",
                self._render_agents_md(slug, root, tech_stack, core_goals),
                kind="agents_instructions",
                overwrite_existing=overwrite_existing,
            ),
            self._write_file(
                root / ".agent.md",
                self._render_agent_personas(slug, tech_stack, core_goals),
                kind="agent_personas",
                overwrite_existing=overwrite_existing,
            ),
            self._write_file(
                root / ".mumega" / "inkwell-canvas.md",
                self._render_canvas(slug, canvas_blocks),
                kind="inkwell_canvas",
                overwrite_existing=overwrite_existing,
            ),
            self._write_file(
                root / ".mumega" / "living-enterprise.json",
                json.dumps(
                    {
                        "tenant_slug": slug,
                        "project_path": str(root),
                        "tech_stack": tech_stack,
                        "core_goals": core_goals,
                        "canvas": canvas_blocks,
                        "generated_by": "sos.sprout_tenant",
                    },
                    indent=2,
                )
                + "\n",
                kind="machine_config",
                overwrite_existing=overwrite_existing,
            ),
        ]

        return SproutResult(
            tenant_slug=slug,
            project_path=str(root),
            pulse_source=pulse_source,
            tech_stack=tech_stack,
            core_goals=core_goals,
            files=files,
            warnings=warnings,
            summary=summary,
        )

    @staticmethod
    def _validate_project_path(project_path: str) -> Path:
        raw = Path(project_path)
        if not raw.is_absolute():
            raise ValueError("project_path must be an absolute path")
        root = raw.resolve()
        if not root.exists():
            raise ValueError(f"project_path does not exist: {root}")
        if not root.is_dir():
            raise ValueError(f"project_path must be a directory: {root}")
        if str(root) == "/":
            raise ValueError("project_path cannot be filesystem root")
        return root

    @staticmethod
    def _slugify(value: str) -> str:
        slug = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
        return slug or "tenant"

    def _pulse_project(self, root: Path, slug: str) -> tuple[dict[str, Any], str, list[str]]:
        if not self.use_gemini:
            return {}, "local", []
        sdk_pulse, sdk_warning = self._pulse_project_with_sdk(root, slug)
        if sdk_pulse:
            return sdk_pulse, "gemini", []
        warnings = [sdk_warning] if sdk_warning else []

        gemini = shutil.which("gemini")
        if not gemini:
            return {}, "local", [*warnings, "gemini CLI not found; used local pulse"]

        prompt = (
            "Pulse this repository for SOS sprout_tenant. Read only. "
            "Return only JSON with keys: summary string, tech_stack string array, "
            "core_goals string array, operating_risks string array, recommended_squads "
            "string array, canvas object with business model blocks. "
            f"Tenant slug candidate: {slug}."
        )
        env = os.environ.copy()
        if env.get("GEMINI_API_KEY") and not env.get("GOOGLE_API_KEY"):
            env["GOOGLE_API_KEY"] = env["GEMINI_API_KEY"]
        try:
            proc = subprocess.run(
                [
                    gemini,
                    "--skip-trust",
                    "--approval-mode",
                    "plan",
                    "--output-format",
                    "text",
                    "-p",
                    prompt,
                ],
                cwd=root,
                text=True,
                capture_output=True,
                env=env,
                timeout=self.gemini_timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return {}, "local", [*warnings, "gemini pulse timed out; used local pulse"]
        except OSError as exc:
            return {}, "local", [*warnings, f"gemini pulse failed: {exc}; used local pulse"]

        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()[:300]
            return {}, "local", [
                *warnings,
                f"gemini pulse exited {proc.returncode}: {err}; used local pulse",
            ]

        parsed = self._parse_json_object(proc.stdout)
        if not parsed:
            return {}, "local", [*warnings, "gemini pulse returned non-JSON; used local pulse"]
        return parsed, "gemini", []

    def _pulse_project_with_sdk(self, root: Path, slug: str) -> tuple[dict[str, Any], str]:
        use_vertex = os.environ.get("SOS_SPROUT_USE_VERTEX") == "1"
        api_keys = [] if use_vertex else self._gemini_api_key_candidates()
        
        if not use_vertex and not api_keys:
            return {}, ""
        try:
            from google import genai
            from google.genai import types
        except Exception as exc:  # noqa: BLE001
            return {}, f"Gemini SDK unavailable: {exc}; used local pulse"

        model = os.environ.get("SOS_SPROUT_GEMINI_MODEL", "gemini-2.0-flash-lite")
        snapshot = self._repo_snapshot(root)
        prompt = f"""Pulse this repository snapshot for SOS sprout_tenant.
Return only a JSON object with these keys:
- summary: string
- tech_stack: string array
- core_goals: string array
- operating_risks: string array
- recommended_squads: string array
- canvas: object whose keys are Business Model Canvas blocks and values are string arrays

Tenant slug candidate: {slug}

Repository snapshot:
{snapshot}
"""
        last_error = ""

        if use_vertex:
            try:
                project = os.environ.get("GOOGLE_CLOUD_PROJECT", "mumega-com")
                location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
                client = genai.Client(vertexai=True, project=project, location=location)
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=0.1,
                        max_output_tokens=2048,
                        response_mime_type="application/json",
                    ),
                )
                parsed = self._parse_json_object(getattr(response, "text", "") or "")
                if parsed:
                    return parsed, ""
                last_error = "Vertex SDK pulse returned non-JSON"
            except Exception as exc:
                last_error = f"Vertex SDK pulse failed: {exc}"
        else:
            for api_key in api_keys:
                try:
                    client = genai.Client(api_key=api_key)
                    response = client.models.generate_content(
                        model=model,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            temperature=0.1,
                            max_output_tokens=2048,
                            response_mime_type="application/json",
                        ),
                    )
                except Exception as exc:  # noqa: BLE001
                    last_error = self._scrub_gemini_error(str(exc))
                    continue

                parsed = self._parse_json_object(getattr(response, "text", "") or "")
                if parsed:
                    return parsed, ""
                last_error = "Gemini SDK pulse returned non-JSON"
        
        source_name = "Vertex" if use_vertex else "Gemini"
        return {}, f"{source_name} SDK pulse failed: {last_error}; used local pulse"


    @staticmethod
    def _gemini_api_key_candidates() -> list[str]:
        keys: list[str] = []
        for name in ["GEMINI_API_KEY", "GOOGLE_API_KEY", *[f"GEMINI_API_KEY_{i}" for i in range(1, 10)]]:
            value = os.environ.get(name)
            if value:
                keys.append(value.strip().strip('"').strip("'"))

        env_files = [
            Path.home() / ".env.secrets",
            Path(__file__).resolve().parents[3] / ".env",
        ]
        for env_file in env_files:
            if not env_file.exists():
                continue
            try:
                lines = env_file.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line in lines:
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue
                name, value = stripped.split("=", 1)
                name = name.strip()
                if name == "GEMINI_API_KEY" or re.fullmatch(r"GEMINI_API_KEY_\d+", name):
                    keys.append(value.strip().strip('"').strip("'"))

        out: list[str] = []
        seen: set[str] = set()
        for key in keys:
            if key and key not in seen:
                seen.add(key)
                out.append(key)
        return out

    @staticmethod
    def _scrub_gemini_error(message: str) -> str:
        return re.sub(r"AIza[0-9A-Za-z_-]{20,}", "<redacted-api-key>", message)[:500]

    def _repo_snapshot(self, root: Path) -> str:
        lines = ["## Files"]
        ignored_dirs = {
            ".git",
            "node_modules",
            ".venv",
            "venv",
            "dist",
            "build",
            ".next",
            ".astro",
            "__pycache__",
        }
        files: list[Path] = []
        for path in root.rglob("*"):
            if any(part in ignored_dirs for part in path.relative_to(root).parts):
                continue
            if path.is_file():
                files.append(path)
            if len(files) >= 160:
                break
        for path in files[:120]:
            lines.append(f"- {path.relative_to(root)}")

        lines.append("\n## Key File Excerpts")
        for name in [
            "README.md",
            "readme.md",
            "package.json",
            "pyproject.toml",
            "astro.config.mjs",
            "astro.config.ts",
            "wrangler.toml",
            "next.config.js",
            "vite.config.ts",
        ]:
            path = root / name
            if not path.exists() or not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")[:4000]
            except OSError:
                continue
            lines.append(f"\n### {name}\n```text\n{text}\n```")
        return "\n".join(lines)[:24000]

    @staticmethod
    def _parse_json_object(text: str) -> dict[str, Any]:
        stripped = text.strip()
        if stripped.startswith("```"):
            stripped = re.sub(r"^```(?:json)?", "", stripped).strip()
            stripped = re.sub(r"```$", "", stripped).strip()
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, flags=re.DOTALL)
            if not match:
                return {}
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _normalize_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        out: list[str] = []
        for item in value:
            text = str(item).strip()
            if text:
                out.append(text)
        return out[:12]

    def _detect_stack(self, root: Path) -> list[str]:
        stack: list[str] = []
        markers = {
            "Astro": ["astro.config.mjs", "astro.config.ts"],
            "Cloudflare": ["wrangler.toml", "wrangler.json", "_worker.js"],
            "Next.js": ["next.config.js", "next.config.mjs", "next.config.ts"],
            "Vite": ["vite.config.js", "vite.config.ts", "vite.config.mjs"],
            "Python": ["pyproject.toml", "requirements.txt"],
            "Node.js": ["package.json"],
            "Deno": ["deno.json", "deno.jsonc"],
            "Rust": ["Cargo.toml"],
            "Go": ["go.mod"],
        }
        names = {path.name for path in root.iterdir() if path.is_file()}
        for label, files in markers.items():
            if any(file in names for file in files):
                stack.append(label)

        package_json = root / "package.json"
        if package_json.exists():
            try:
                pkg = json.loads(package_json.read_text(encoding="utf-8"))
                deps = {
                    **(pkg.get("dependencies") or {}),
                    **(pkg.get("devDependencies") or {}),
                }
                for dep, label in {
                    "astro": "Astro",
                    "@cloudflare/workers-types": "Cloudflare Workers",
                    "react": "React",
                    "svelte": "Svelte",
                    "vue": "Vue",
                    "tailwindcss": "Tailwind CSS",
                }.items():
                    if dep in deps and label not in stack:
                        stack.append(label)
            except (OSError, json.JSONDecodeError):
                pass
        return stack or ["unknown"]

    def _infer_goals(self, root: Path, slug: str) -> list[str]:
        readme = self._read_first_existing(root, ["README.md", "readme.md", "README.txt"])
        goals: list[str] = []
        for line in readme.splitlines():
            clean = line.strip(" #\t")
            if len(clean) >= 20 and not clean.startswith(("http://", "https://")):
                goals.append(clean)
            if len(goals) >= 3:
                break
        if goals:
            return goals
        return [
            f"Operate {slug} as a tenant-scoped Living Enterprise.",
            "Map product, content, operations, and engineering work into routed squad tasks.",
            "Keep human approval for external-impact actions until delegation is explicit.",
        ]

    @staticmethod
    def _read_first_existing(root: Path, names: list[str]) -> str:
        for name in names:
            path = root / name
            if path.exists() and path.is_file():
                try:
                    return path.read_text(encoding="utf-8", errors="replace")[:8000]
                except OSError:
                    return ""
        return ""

    @staticmethod
    def _default_summary(slug: str, tech_stack: list[str], core_goals: list[str]) -> str:
        primary_goal = core_goals[0].rstrip(".") if core_goals else "establish tenant operating truth"
        return (
            f"{slug} uses {', '.join(tech_stack)}. "
            f"Primary goal: {primary_goal}."
        )

    def _canvas_blocks(
        self,
        pulse: dict[str, Any],
        slug: str,
        tech_stack: list[str],
        core_goals: list[str],
    ) -> dict[str, list[str]]:
        raw = pulse.get("canvas")
        if isinstance(raw, dict):
            normalized: dict[str, list[str]] = {}
            for key, value in raw.items():
                items = self._normalize_list(value if isinstance(value, list) else [value])
                if items:
                    normalized[self._title_key(str(key))] = items
            if normalized:
                return normalized

        return {
            "Customer Segments": [
                "Primary users and operators served by this project.",
                "Internal agents and squads responsible for execution.",
            ],
            "Value Propositions": core_goals[:3],
            "Channels": ["Project repository", "Inkwell publication surface", "SOS bus"],
            "Customer Relationships": [
                "Human approval for external sends, billing, publishing, and irreversible changes.",
                "Agent execution through tenant-scoped boot context, memory, and receipts.",
            ],
            "Revenue Streams": ["To be mapped by mkt-outreach or tenant operator."],
            "Key Resources": [
                f"Codebase at project path for tenant {slug}.",
                f"Detected stack: {', '.join(tech_stack)}.",
                "SOS microkernel, Mirror memory, Inkwell canvas, and squad task queues.",
            ],
            "Key Activities": [
                "Maintain operating truth.",
                "Route work into squads.",
                "Record receipts for meaningful actions.",
            ],
            "Key Partners": ["Mumega operator", "Tenant owner", "Authorized agent squads"],
            "Cost Structure": ["LLM/tool compute", "Engineering maintenance", "Human approval time"],
        }

    @staticmethod
    def _title_key(value: str) -> str:
        return re.sub(r"[_-]+", " ", value).strip().title()

    @staticmethod
    def _write_file(
        path: Path,
        content: str,
        *,
        kind: str,
        overwrite_existing: bool,
    ) -> SproutFile:
        path.parent.mkdir(parents=True, exist_ok=True)
        existed = path.exists()
        if existed and not overwrite_existing:
            return SproutFile(path=str(path), status="exists_skipped", kind=kind)
        path.write_text(content, encoding="utf-8")
        return SproutFile(
            path=str(path),
            status="overwritten" if existed else "created",
            kind=kind,
        )

    @staticmethod
    def _render_agents_md(
        slug: str,
        root: Path,
        tech_stack: list[str],
        core_goals: list[str],
    ) -> str:
        goals = "\n".join(f"- {goal}" for goal in core_goals)
        stack = "\n".join(f"- {item}" for item in tech_stack)
        return f"""# AGENTS.md - {slug}

This project is a Mumega Living Enterprise tenant.

## Identity

- Tenant slug: `{slug}`
- Project path: `{root}`
- Canonical local truth: this repository plus `.mumega/inkwell-canvas.md`
- Runtime truth: SOS boot context, Mirror memory, and squad tasks

## First Turn

1. Call `boot_context` from the SOS MCP server.
2. Read this `AGENTS.md`.
3. Read `.agent.md` for available personas and squad responsibilities.
4. Read `.mumega/inkwell-canvas.md` for the current business model.
5. Check `inbox` and `task_list` before exploring files.

## Core Goals

{goals}

## Detected Stack

{stack}

## Operating Rules

- Do not reread the whole repository before checking boot context, canvas, inbox, and tasks.
- Treat local files as a working source, not the only source of truth.
- External sends, billing, publishing, destructive commands, and irreversible customer-visible changes require approval receipts until delegated.
- Record meaningful decisions and completed work through SOS/Mirror so the next node wakes up oriented.
- Prefer routed squad tasks over manual human coordination.

## Standard Flow

```text
boot_context -> canvas -> inbox/task_list -> scoped file reads -> action -> receipt
```
"""

    @staticmethod
    def _render_agent_personas(
        slug: str,
        tech_stack: list[str],
        core_goals: list[str],
    ) -> str:
        first_goal = core_goals[0] if core_goals else f"advance {slug}"
        stack = ", ".join(tech_stack)
        return f"""# .agent.md - {slug} Personas

These are starter personas for the tenant. They are not global identities.
Each agent must still authenticate through SOS and respect tenant scope.

## Loom - Coordinator

- Purpose: hold sprint truth, route work, prevent duplicate exploration.
- First question: what is the next highest-causality task for `{slug}`?
- Main inputs: boot context, inbox, task board, canvas.

## Athena - Gate

- Purpose: review coherence, safety, claims, and approval boundaries.
- First question: what could break trust if this action ships?
- Main inputs: canvas, receipts, tests, external-impact checklist.

## Hermes - Operator

- Purpose: stage outbound, partnerships, support, and process execution.
- First question: which action moves `{slug}` toward its market or customer?
- Main inputs: target lists, CRM notes, approval status, receipt templates.

## Codex - Builder

- Purpose: implement code, tests, scripts, and durable integration paths.
- First question: what is the smallest tested change that makes `{slug}` more alive?
- Main inputs: tech stack ({stack}), issue/task context, repo files.

## Calliope - Content

- Purpose: turn proof into useful public or internal narrative.
- First question: how does this support the tenant's core goal?
- Main inputs: proof artifacts, canvas value propositions, approved claims.

## Tenant Goal

{first_goal}
"""

    @staticmethod
    def _render_canvas(slug: str, blocks: dict[str, list[str]]) -> str:
        lines = [
            f"# Tenant Canvas - {slug}",
            "",
            "Template: Business Model Canvas",
            "Status: draft generated by `sprout_tenant`",
            "",
            "## Canvas Blocks",
            "",
        ]
        for key in [
            "Customer Segments",
            "Value Propositions",
            "Channels",
            "Customer Relationships",
            "Revenue Streams",
            "Key Resources",
            "Key Activities",
            "Key Partners",
            "Cost Structure",
        ]:
            lines.append(f"### {key}")
            for item in blocks.get(key, ["To be mapped."]):
                lines.append(f"- {item}")
            lines.append("")
        lines.extend(
            [
                "## Operating Rule",
                "",
                (
                    "Agents may draft and recommend actions. External sends, publishing, "
                    "billing, destructive operations, and customer-visible changes require "
                    "explicit approval receipts until the tenant delegates them."
                ),
                "",
            ]
        )
        return "\n".join(lines)
