"""CustomerIntakeService — intake → knight spawn pipeline."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional
from uuid import uuid4

from sos.services.squad.service import DEFAULT_TENANT_ID, SquadDB, now_iso

# Path to mint-knight.py — 4 parents up from this file = /home/mumega
_SCRIPT_DIR = Path(__file__).parents[4] / "scripts"
MINT_KNIGHT_PATH = _SCRIPT_DIR / "mint-knight.py"

VALID_STATUSES = {"pending", "approved", "minted", "rejected", "failed"}
VALID_SOURCES = {"direct", "ghl", "api"}
_ROLE_RE = re.compile(r"^[a-z0-9-]+$")


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def validate_initial_roles(value: str) -> list[str]:
    """Parse and validate initial_roles_json.

    Returns the parsed list. Raises ValueError with a detail message on failure.
    """
    try:
        roles = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"initial_roles_json must be valid JSON: {exc}") from exc
    if not isinstance(roles, list):
        raise ValueError("initial_roles_json must be a JSON array")
    if len(roles) > 10:
        raise ValueError("initial_roles_json may not have more than 10 items")
    for item in roles:
        if not isinstance(item, str):
            raise ValueError(f"initial_roles_json items must be strings, got {type(item).__name__}")
        if len(item) > 40:
            raise ValueError(f"role name '{item}' exceeds 40 characters")
        if not _ROLE_RE.match(item):
            raise ValueError(
                f"role name '{item}' is invalid; must match ^[a-z0-9-]+$"
            )
    return roles


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class IntakeNotFoundError(ValueError):
    pass


class IntakeStatusError(ValueError):
    """Raised when an action is blocked by the current status."""
    pass


class CustomerIntakeService:
    def __init__(self, db: Optional[SquadDB] = None) -> None:
        self.db = db or SquadDB()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create_intake(
        self,
        customer_name: str,
        customer_slug: str,
        *,
        domain: Optional[str] = None,
        repo_url: Optional[str] = None,
        icp: Optional[str] = None,
        okrs_json: str = "[]",
        cause_draft: Optional[str] = None,
        descriptor_draft: Optional[str] = None,
        initial_roles_json: str = '["advisor","intern"]',
        source: str = "direct",
        ghl_contact_id: Optional[str] = None,
    ) -> dict:
        # Validate initial_roles_json at create time
        validate_initial_roles(initial_roles_json)

        intake_id = str(uuid4())
        created_at = now_iso()
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO customer_intakes (
                    id, customer_name, customer_slug, domain, repo_url, icp,
                    okrs_json, cause_draft, descriptor_draft, initial_roles_json,
                    status, source, ghl_contact_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                """,
                (
                    intake_id, customer_name, customer_slug,
                    domain, repo_url, icp,
                    okrs_json, cause_draft, descriptor_draft, initial_roles_json,
                    source, ghl_contact_id, created_at,
                ),
            )
        return self.get_intake(intake_id)

    def get_intake(self, intake_id: str) -> dict:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM customer_intakes WHERE id = ?", (intake_id,)
            ).fetchone()
        if row is None:
            raise IntakeNotFoundError(f"intake {intake_id!r} not found")
        return dict(row)

    def list_intakes(
        self,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        with self.db.connect() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM customer_intakes WHERE status = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (status, limit, offset),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM customer_intakes ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
        return [dict(r) for r in rows]

    def update_intake(self, intake_id: str, updates: dict) -> dict:
        """Update editable fields. Only allowed when status=pending."""
        intake = self.get_intake(intake_id)
        if intake["status"] != "pending":
            raise IntakeStatusError(
                f"intake may only be edited when status=pending, currently {intake['status']!r}"
            )

        allowed_fields = {"cause_draft", "descriptor_draft", "initial_roles_json",
                          "domain", "repo_url", "icp", "okrs_json"}
        filtered = {k: v for k, v in updates.items() if k in allowed_fields and v is not None}

        if "initial_roles_json" in filtered:
            validate_initial_roles(filtered["initial_roles_json"])

        if not filtered:
            return intake

        set_clause = ", ".join(f"{k} = ?" for k in filtered)
        values = list(filtered.values()) + [intake_id]
        with self.db.connect() as conn:
            conn.execute(
                f"UPDATE customer_intakes SET {set_clause} WHERE id = ?", values
            )
        return self.get_intake(intake_id)

    # ------------------------------------------------------------------
    # Status transitions
    # ------------------------------------------------------------------

    def approve(self, intake_id: str, approver_agent_id: str) -> dict:
        intake = self.get_intake(intake_id)
        if intake["status"] != "pending":
            raise IntakeStatusError(
                f"approve requires status=pending, currently {intake['status']!r}"
            )
        approved_at = now_iso()
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE customer_intakes SET status = 'approved', approved_by = ?, approved_at = ? WHERE id = ?",
                (approver_agent_id, approved_at, intake_id),
            )
        return self.get_intake(intake_id)

    def reject(self, intake_id: str) -> dict:
        intake = self.get_intake(intake_id)
        if intake["status"] not in ("pending", "approved"):
            raise IntakeStatusError(
                f"reject requires status=pending or approved, currently {intake['status']!r}"
            )
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE customer_intakes SET status = 'rejected' WHERE id = ?",
                (intake_id,),
            )
        return self.get_intake(intake_id)

    # ------------------------------------------------------------------
    # Mint
    # ------------------------------------------------------------------

    def mint(self, intake_id: str) -> dict:
        """Invoke mint-knight.py as subprocess. Returns {knight_name, workspace, roles_seeded}."""
        intake = self.get_intake(intake_id)

        if intake["status"] == "minted":
            raise IntakeStatusError("already minted — cannot mint again")
        if intake["status"] != "approved":
            raise IntakeStatusError(
                f"mint requires status=approved, currently {intake['status']!r}"
            )
        cause = (intake.get("cause_draft") or "").strip()
        if not cause:
            raise ValueError("cause_draft must be non-empty before minting")

        # knight_name = customer_slug (slug IS the canonical identity handle)
        knight_name = intake["customer_slug"]

        # Write cause to a NamedTemporaryFile — never pass user content as a CLI arg
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, prefix="cause_"
        ) as tmp:
            tmp.write(cause)
            tmp_path = tmp.name

        try:
            cmd = [
                sys.executable,
                str(MINT_KNIGHT_PATH),
                "--knight-name", knight_name,
                "--customer-slug", intake["customer_slug"],
                "--customer-name", intake["customer_name"],
                "--cause-file", tmp_path,
            ]
            if intake.get("domain"):
                cmd += ["--customer-domain", intake["domain"]]
            if intake.get("repo_url"):
                cmd += ["--customer-repo", intake["repo_url"]]

            result = subprocess.run(
                cmd,
                shell=False,
                capture_output=True,
                text=True,
            )
        finally:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except Exception:
                pass

        if result.returncode != 0:
            stderr_excerpt = result.stderr[:2000]
            with self.db.connect() as conn:
                conn.execute(
                    "UPDATE customer_intakes SET status = 'failed', mint_error = ? WHERE id = ?",
                    (stderr_excerpt, intake_id),
                )
            raise MintFailedError(stderr_excerpt)

        minted_at = now_iso()
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE customer_intakes
                SET status = 'minted', knight_name = ?, minted_at = ?, mint_error = NULL
                WHERE id = ?
                """,
                (knight_name, minted_at, intake_id),
            )

        # Best-effort role seeding
        roles_seeded = self._seed_roles_for_intake(intake_id)

        return {
            "knight_name": knight_name,
            "workspace": str(
                Path(__file__).parents[4]
                / "mumega.com"
                / "agents"
                / "loom"
                / "customers"
                / knight_name
            ),
            "roles_seeded": roles_seeded,
        }

    def seed_roles(self, intake_id: str) -> dict:
        """Retry role seeding for a minted intake."""
        intake = self.get_intake(intake_id)
        if intake["status"] != "minted":
            raise IntakeStatusError(
                f"seed_roles requires status=minted, currently {intake['status']!r}"
            )
        roles_seeded = self._seed_roles_for_intake(intake_id)
        return {"roles_seeded": roles_seeded}

    def _seed_roles_for_intake(self, intake_id: str) -> int:
        """Best-effort: insert roles from initial_roles_json into roles table.

        Default permissions:
        - inkwell:read:role — all roles
        - inkwell:write:project — advisor only

        Returns number of roles seeded. Never raises (best-effort).
        """
        try:
            intake = self.get_intake(intake_id)
            roles_raw = intake.get("initial_roles_json", '["advisor","intern"]')
            role_names = json.loads(roles_raw)
            project_id = intake["customer_slug"]
            seeded = 0

            with self.db.connect() as conn:
                for role_name in role_names:
                    role_id = f"{project_id}-{role_name}"
                    created_at = now_iso()
                    try:
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO roles (id, project_id, tenant_id, name, created_at)
                            VALUES (?, ?, ?, ?, ?)
                            """,
                            (role_id, project_id, DEFAULT_TENANT_ID, role_name, created_at),
                        )
                        # Base permission: read
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO role_permissions (role_id, permission)
                            VALUES (?, ?)
                            """,
                            (role_id, "inkwell:read:role"),
                        )
                        # Advisor gets write permission too
                        if role_name == "advisor":
                            conn.execute(
                                """
                                INSERT OR IGNORE INTO role_permissions (role_id, permission)
                                VALUES (?, ?)
                                """,
                                (role_id, "inkwell:write:project"),
                            )
                        seeded += 1
                    except Exception:
                        # Individual role failures are tolerated
                        pass
            return seeded
        except Exception:
            # Table may not exist yet (0012 not applied) — silently return 0
            return 0

    # ------------------------------------------------------------------
    # GHL webhook
    # ------------------------------------------------------------------

    def create_from_ghl(self, payload: dict) -> dict:
        """Map a GHL lead webhook payload to an intake row."""
        customer_name = (
            payload.get("company")
            or payload.get("first_name", "")
            + (" " + payload.get("last_name", "")).rstrip()
            or "unknown"
        ).strip()

        # Derive a slug: lowercase, replace spaces with hyphens, strip non-alnum
        raw_slug = re.sub(r"[^a-z0-9]+", "-", customer_name.lower()).strip("-")
        customer_slug = raw_slug or "intake-" + str(uuid4())[:8]

        # Pull custom fields if present
        custom = payload.get("custom_fields") or {}
        icp = custom.get("icp") or payload.get("icp")
        okrs = custom.get("okrs") or payload.get("okrs")
        okrs_json = json.dumps(okrs) if isinstance(okrs, list) else "[]"

        return self.create_intake(
            customer_name=customer_name,
            customer_slug=customer_slug,
            domain=payload.get("domain"),
            icp=icp,
            okrs_json=okrs_json,
            source="ghl",
            ghl_contact_id=payload.get("contact_id") or payload.get("id"),
        )


class MintFailedError(RuntimeError):
    pass
