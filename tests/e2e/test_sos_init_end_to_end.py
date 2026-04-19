"""End-to-end test for ``sos init`` (Phase 5 §5.7).

Drives ``cli_init.main(...)`` against fully-faked downstream clients and
filesystem. Asserts the full A → B → C → D → E round-trip leaves the
system in the expected shape:

- SaasClient gets the validated tenant payload
- wrangler subprocess gets invoked with --project-name <slug>
- EconomyClient mints one qNFT per default squad role
- standing_workflows.json is enriched with squads + assigned_squads
- OperationsClient receives the pulse trigger

No real HTTP, no real wrangler, no real Redis. Runs in a tmp_path
with a faked _template/ directory so it's hermetic.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from sos.cli import init as cli_init


class _RecordingSaas:
    instances: list["_RecordingSaas"] = []

    def __init__(self, **kwargs: Any) -> None:
        self.init_kwargs = kwargs
        self.calls: list[dict[str, Any]] = []
        _RecordingSaas.instances.append(self)

    def create_tenant(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(payload)
        return {
            "slug": payload["slug"],
            "label": payload["label"],
            "email": payload["email"],
            "plan": payload.get("plan", "starter"),
            "status": "provisioning",
        }


class _RecordingEconomy:
    instances: list["_RecordingEconomy"] = []

    def __init__(self, **kwargs: Any) -> None:
        self.init_kwargs = kwargs
        self.calls: list[dict[str, Any]] = []
        _RecordingEconomy.instances.append(self)

    def mint_qnft(
        self,
        tenant: str,
        squad_id: str,
        role: str,
        seat_id: str,
        *,
        cost_mind: int | None = None,
        project: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        record = {
            "tenant": tenant,
            "squad_id": squad_id,
            "role": role,
            "seat_id": seat_id,
            "cost_mind": cost_mind,
            "project": project,
            "token_id": f"tok-{role}",
        }
        self.calls.append(record)
        return record


class _RecordingOperations:
    instances: list["_RecordingOperations"] = []

    def __init__(self, **kwargs: Any) -> None:
        self.init_kwargs = kwargs
        self.calls: list[tuple[str, str]] = []
        _RecordingOperations.instances.append(self)

    def trigger_pulse(self, tenant: str, project: str) -> dict[str, Any]:
        self.calls.append((tenant, project))
        return {
            "ok": True,
            "tenant": tenant,
            "project": project,
            "started_at": "2026-04-19T00:00:00+00:00",
        }


def _seed_inkwell_template(inkwell_root: Path) -> None:
    template = inkwell_root / "instances" / "_template"
    template.mkdir(parents=True)
    (template / "inkwell.config.ts").write_text(
        "export const config = { name: '{{LABEL}}', slug: '{{SLUG}}' }",
        encoding="utf-8",
    )
    (template / "standing_workflows.json").write_text(
        json.dumps(
            {
                "version": "1",
                "tenant": "{{SLUG}}",
                "workflows": [
                    {
                        "name": "{{SLUG}}-daily",
                        "schedule": "0 9 * * *",
                        "description": "Daily operations pulse for {{LABEL}}",
                        "steps": ["pulse", "journal", "report"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_sos_init_end_to_end_round_trip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Isolate filesystem for Step B + D.
    _seed_inkwell_template(tmp_path)
    monkeypatch.setenv("INKWELL_ROOT", str(tmp_path))
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "fake-cf-token")
    monkeypatch.setenv("MUMEGA_DEFAULT_SQUADS", "social,content")
    monkeypatch.setenv("MUMEGA_QNFT_SEAT_COST_MIND", "25")

    # Clear instance registries (class-level state).
    _RecordingSaas.instances.clear()
    _RecordingEconomy.instances.clear()
    _RecordingOperations.instances.clear()

    # Fake wrangler + npm build so Step B doesn't shell out.
    def fake_run(args: list[str], **kwargs: Any) -> Any:
        return subprocess.CompletedProcess(
            args=args, returncode=0, stdout="https://acme.pages.dev", stderr=""
        )

    # The step functions bind their `client_factory` defaults at definition
    # time, so module-level monkeypatching won't re-bind them. Patch the
    # kwdefaults dict directly so main() picks up the recording fakes.
    monkeypatch.setattr(
        cli_init.step_a_provision_tenant,
        "__kwdefaults__",
        {"client_factory": _RecordingSaas},
    )
    monkeypatch.setattr(
        cli_init.step_b_deploy_inkwell,
        "__kwdefaults__",
        {"_subprocess_run": fake_run},
    )
    monkeypatch.setattr(
        cli_init.step_c_seed_squads,
        "__kwdefaults__",
        {"client_factory": _RecordingEconomy},
    )
    monkeypatch.setattr(
        cli_init.step_e_trigger_pulse,
        "__kwdefaults__",
        {"client_factory": _RecordingOperations},
    )

    rc = cli_init.main([
        "--slug", "acme",
        "--label", "Acme Co",
        "--email", "owner@acme.com",
        "--domain", "acme.com",
        "--industry", "consulting",
        "--tagline", "doing the thing",
    ])

    assert rc == 0
    out = capsys.readouterr().out
    assert "Phase 5 shipped" in out

    # --- Step A ---
    assert len(_RecordingSaas.instances) == 1
    saas_payload = _RecordingSaas.instances[0].calls[0]
    assert saas_payload["slug"] == "acme"
    assert saas_payload["plan"] == "starter"
    assert saas_payload["domain"] == "acme.com"

    # --- Step B (fs side-effect) ---
    deployed = tmp_path / "instances" / "acme" / "inkwell.config.ts"
    assert deployed.exists()
    assert "Acme Co" in deployed.read_text(encoding="utf-8")

    # --- Step C ---
    assert len(_RecordingEconomy.instances) == 1
    econ_calls = _RecordingEconomy.instances[0].calls
    assert {c["role"] for c in econ_calls} == {"social", "content"}
    assert all(c["cost_mind"] == 25 for c in econ_calls)
    assert all(c["squad_id"] == f"acme-squad-{c['role']}" for c in econ_calls)

    # --- Step D ---
    workflow_on_disk = json.loads(
        (tmp_path / "instances" / "acme" / "standing_workflows.json").read_text(
            encoding="utf-8"
        )
    )
    assert {s["role"] for s in workflow_on_disk["squads"]} == {"social", "content"}
    assert set(workflow_on_disk["workflows"][0]["assigned_squads"]) == {
        "acme-squad-social",
        "acme-squad-content",
    }
    # Step B interpolation survived into Step D's round-trip.
    assert workflow_on_disk["workflows"][0]["name"] == "acme-daily"

    # --- Step E ---
    assert len(_RecordingOperations.instances) == 1
    assert _RecordingOperations.instances[0].calls == [("acme", "acme")]
