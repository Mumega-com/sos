"""Unit tests for ``sos init`` (Phase 5 — v0.9.4 tenant provisioning).

Covers all five steps without network access:
- A: SaaS ``/tenants`` POST
- B: inkwell template copy + wrangler deploy (subprocess faked)
- C: qNFT seat minting (EconomyClient faked)
- D: standing_workflows.json enrichment with squad IDs
- E: operations pulse trigger (OperationsClient faked)
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from sos.cli import init as cli_init
from sos.contracts.tenant import TenantPlan


class _FakeSaasClient:
    """Records the call payload and returns a canned tenant row."""

    last_instance: "_FakeSaasClient | None" = None

    def __init__(self, **kwargs: Any) -> None:
        self.init_kwargs = kwargs
        self.calls: list[dict[str, Any]] = []
        _FakeSaasClient.last_instance = self

    def create_tenant(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(payload)
        return {
            "slug": payload["slug"],
            "label": payload["label"],
            "email": payload["email"],
            "plan": payload.get("plan", "starter"),
            "status": "provisioning",
        }


@pytest.fixture
def cfg() -> cli_init.InitConfig:
    return cli_init.InitConfig(
        slug="acme",
        label="Acme Co",
        email="owner@acme.com",
        plan=TenantPlan.STARTER,
        domain="acme.com",
        industry="consulting",
        tagline="doing the thing",
    )


def test_build_payload_round_trips_through_tenant_create(cfg: cli_init.InitConfig) -> None:
    payload = cli_init.build_payload(cfg)
    assert payload["slug"] == "acme"
    assert payload["label"] == "Acme Co"
    assert payload["email"] == "owner@acme.com"
    assert payload["plan"] == "starter"
    assert payload["domain"] == "acme.com"
    assert payload["industry"] == "consulting"
    assert payload["tagline"] == "doing the thing"
    # exclude_none=True: ``services`` / ``primary_color`` not in payload.
    assert "services" not in payload
    assert "primary_color" not in payload


def test_step_a_calls_saas_client_with_tenant_create_payload(
    cfg: cli_init.InitConfig,
) -> None:
    tenant = cli_init.step_a_provision_tenant(cfg, client_factory=_FakeSaasClient)
    assert tenant["slug"] == "acme"
    assert tenant["status"] == "provisioning"

    assert _FakeSaasClient.last_instance is not None
    sent = _FakeSaasClient.last_instance.calls[0]
    assert sent["slug"] == "acme"
    assert sent["plan"] == "starter"
    assert sent["domain"] == "acme.com"


def test_step_a_passes_base_url_and_token_overrides(cfg: cli_init.InitConfig) -> None:
    cfg.saas_base_url = "http://saas.test:9000"
    cfg.saas_token = "sk-test"
    cli_init.step_a_provision_tenant(cfg, client_factory=_FakeSaasClient)

    assert _FakeSaasClient.last_instance is not None
    kwargs = _FakeSaasClient.last_instance.init_kwargs
    assert kwargs["base_url"] == "http://saas.test:9000"
    assert kwargs["token"] == "sk-test"


def test_step_a_dry_run_does_not_hit_client(cfg: cli_init.InitConfig) -> None:
    cfg.dry_run = True
    _FakeSaasClient.last_instance = None
    tenant = cli_init.step_a_provision_tenant(cfg, client_factory=_FakeSaasClient)
    assert tenant["_dry_run"] is True
    assert tenant["slug"] == "acme"
    # Factory must not have been invoked at all.
    assert _FakeSaasClient.last_instance is None


# ---------------------------------------------------------------------------
# Step D — standing_workflows.json enrichment
# ---------------------------------------------------------------------------


def _write_template_workflow(tmp_path: Path, slug: str) -> Path:
    """Place the file Step B would leave for Step D to enrich."""
    instance_dir = tmp_path / "instances" / slug
    instance_dir.mkdir(parents=True)
    workflow_path = instance_dir / "standing_workflows.json"
    workflow_path.write_text(
        json.dumps(
            {
                "version": "1",
                "tenant": slug,
                "workflows": [
                    {
                        "name": f"{slug}-daily",
                        "schedule": "0 9 * * *",
                        "description": "Daily ops pulse",
                        "steps": ["pulse", "journal", "report"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return workflow_path


def test_step_d_injects_squads_and_assigned_squads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cfg: cli_init.InitConfig
) -> None:
    monkeypatch.setenv("INKWELL_ROOT", str(tmp_path))
    workflow_path = _write_template_workflow(tmp_path, "acme")

    seats = [
        {
            "squad_id": "acme-squad-social",
            "role": "social",
            "seat_id": "acme:seat:social",
            "token_id": "tok-1",
        },
        {
            "squad_id": "acme-squad-content",
            "role": "content",
            "seat_id": "acme:seat:content",
            "token_id": "tok-2",
        },
    ]

    result = cli_init.step_d_write_workflows(
        cfg, {"slug": "acme", "status": "provisioning"}, seats
    )

    # Return value reflects what got written.
    assert len(result["squads"]) == 2
    assert {s["role"] for s in result["squads"]} == {"social", "content"}

    # On disk matches.
    on_disk = json.loads(workflow_path.read_text(encoding="utf-8"))
    assert on_disk["squads"] == [
        {
            "squad_id": "acme-squad-social",
            "role": "social",
            "seat_id": "acme:seat:social",
            "token_id": "tok-1",
        },
        {
            "squad_id": "acme-squad-content",
            "role": "content",
            "seat_id": "acme:seat:content",
            "token_id": "tok-2",
        },
    ]

    assert on_disk["workflows"][0]["assigned_squads"] == [
        "acme-squad-social",
        "acme-squad-content",
    ]
    # Existing fields must be preserved.
    assert on_disk["workflows"][0]["name"] == "acme-daily"
    assert on_disk["workflows"][0]["steps"] == ["pulse", "journal", "report"]


def test_step_d_raises_if_step_b_has_not_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cfg: cli_init.InitConfig
) -> None:
    monkeypatch.setenv("INKWELL_ROOT", str(tmp_path))

    with pytest.raises(FileNotFoundError, match="standing_workflows.json"):
        cli_init.step_d_write_workflows(
            cfg, {"slug": "acme", "status": "provisioning"}, []
        )


def test_step_d_handles_empty_seats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cfg: cli_init.InitConfig
) -> None:
    monkeypatch.setenv("INKWELL_ROOT", str(tmp_path))
    workflow_path = _write_template_workflow(tmp_path, "acme")

    cli_init.step_d_write_workflows(
        cfg, {"slug": "acme", "status": "provisioning"}, []
    )

    on_disk = json.loads(workflow_path.read_text(encoding="utf-8"))
    assert on_disk["squads"] == []
    assert on_disk["workflows"][0]["assigned_squads"] == []


# ---------------------------------------------------------------------------
# Step C — qNFT seat minting
# ---------------------------------------------------------------------------


class _FakeEconomyClient:
    """Records mint_qnft calls and returns canned qNFT dicts."""

    last_instance: "_FakeEconomyClient | None" = None

    def __init__(self, **kwargs: Any) -> None:
        self.init_kwargs = kwargs
        self.calls: list[dict[str, Any]] = []
        _FakeEconomyClient.last_instance = self

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
            "token_id": f"fake-{role}",
        }
        self.calls.append(record)
        return record


class _BrokeEconomyClient(_FakeEconomyClient):
    """mint_qnft always raises a 402-like error."""

    def mint_qnft(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise Exception("402 Payment Required: Insufficient funds")


def test_step_c_mints_default_squad_with_cost_env_override(
    cfg: cli_init.InitConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MUMEGA_DEFAULT_SQUADS", "social,content,outreach,analytics")
    monkeypatch.setenv("MUMEGA_QNFT_SEAT_COST_MIND", "50")
    _FakeEconomyClient.last_instance = None

    result = cli_init.step_c_seed_squads(
        cfg,
        {"slug": "acme", "status": "provisioning"},
        client_factory=_FakeEconomyClient,
    )

    assert _FakeEconomyClient.last_instance is not None
    calls = _FakeEconomyClient.last_instance.calls
    assert len(calls) == 4

    roles = [c["role"] for c in calls]
    assert set(roles) == {"social", "content", "outreach", "analytics"}

    for call in calls:
        assert call["cost_mind"] == 50
        assert call["seat_id"] == f"acme:seat:{call['role']}"
        assert call["squad_id"] == f"acme-squad-{call['role']}"
        assert call["project"] == "acme"

    assert len(result) == 4


def test_step_c_raises_on_insufficient_funds(
    cfg: cli_init.InitConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MUMEGA_DEFAULT_SQUADS", "social")
    monkeypatch.setenv("MUMEGA_QNFT_SEAT_COST_MIND", "100")

    with pytest.raises(RuntimeError) as exc_info:
        cli_init.step_c_seed_squads(
            cfg,
            {"slug": "acme", "status": "provisioning"},
            client_factory=_BrokeEconomyClient,
        )
    assert "insufficient" in str(exc_info.value).lower() or "402" in str(exc_info.value)


def _make_fake_completed_process(stdout: str = "https://acme.pages.dev") -> Any:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


def test_step_b_copies_template_and_interpolates_placeholders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cfg: cli_init.InitConfig
) -> None:
    # Create a minimal fake _template/ under tmp_path.
    template = tmp_path / "instances" / "_template"
    template.mkdir(parents=True)
    config_file = template / "inkwell.config.ts"
    config_file.write_text(
        "export const config = { name: '{{LABEL}}', domain: '{{DOMAIN}}', slug: '{{SLUG}}' }",
        encoding="utf-8",
    )
    (template / "standing_workflows.json").write_text(
        '{"tenant": "{{SLUG}}", "workflows": [{"name": "{{SLUG}}-daily"}]}',
        encoding="utf-8",
    )

    monkeypatch.setenv("INKWELL_ROOT", str(tmp_path))
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "fake-cf-token")

    wrangler_calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: Any) -> Any:
        wrangler_calls.append(args)
        return _make_fake_completed_process()

    result = cli_init.step_b_deploy_inkwell(
        cfg, {"slug": "acme", "status": "provisioning"}, _subprocess_run=fake_run
    )

    dest = tmp_path / "instances" / "acme"

    # (a) dest exists and is not the source
    assert dest.exists()
    assert dest != template

    # (b) placeholders were replaced
    deployed_config = (dest / "inkwell.config.ts").read_text()
    assert "Acme Co" in deployed_config
    assert "acme.com" in deployed_config
    assert "{{LABEL}}" not in deployed_config
    assert "{{SLUG}}" not in deployed_config

    # (c) wrangler was called with --project-name acme
    wrangler_invocation = next(
        (call for call in wrangler_calls if "wrangler" in call[0]), None
    )
    assert wrangler_invocation is not None
    assert "--project-name" in wrangler_invocation
    assert "acme" in wrangler_invocation

    # result carries expected keys
    assert result["slug"] == "acme"
    assert result["deploy_path"] == str(dest)


def test_step_b_raises_if_dest_already_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cfg: cli_init.InitConfig
) -> None:
    template = tmp_path / "instances" / "_template"
    template.mkdir(parents=True)
    (template / "inkwell.config.ts").write_text("export const x = '{{SLUG}}'")

    # Pre-create the dest to simulate a collision.
    dest = tmp_path / "instances" / "acme"
    dest.mkdir(parents=True)

    monkeypatch.setenv("INKWELL_ROOT", str(tmp_path))
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "fake-cf-token")

    with pytest.raises(FileExistsError, match="acme"):
        cli_init.step_b_deploy_inkwell(
            cfg,
            {"slug": "acme", "status": "provisioning"},
            _subprocess_run=lambda *a, **kw: _make_fake_completed_process(),
        )


class _FakeOperationsClient:
    """Records trigger_pulse calls and returns a canned response."""

    last_instance: "_FakeOperationsClient | None" = None

    def __init__(self, **kwargs: Any) -> None:
        self.init_kwargs = kwargs
        self.calls: list[tuple[str, str]] = []
        _FakeOperationsClient.last_instance = self

    def trigger_pulse(self, tenant: str, project: str) -> dict[str, Any]:
        self.calls.append((tenant, project))
        return {
            "ok": True,
            "tenant": tenant,
            "project": project,
            "started_at": "2026-04-19T00:00:00+00:00",
        }


def test_step_e_calls_operations_client(cfg: cli_init.InitConfig) -> None:
    _FakeOperationsClient.last_instance = None
    tenant = {"slug": "acme", "status": "provisioning"}
    result = cli_init.step_e_trigger_pulse(
        cfg, tenant, client_factory=_FakeOperationsClient
    )
    assert result["ok"] is True
    assert result["tenant"] == "acme"

    assert _FakeOperationsClient.last_instance is not None
    assert _FakeOperationsClient.last_instance.calls == [("acme", "acme")]


def test_step_e_forwards_token_to_client(cfg: cli_init.InitConfig) -> None:
    cfg.saas_token = "sk-test"
    tenant = {"slug": "acme", "status": "provisioning"}
    cli_init.step_e_trigger_pulse(cfg, tenant, client_factory=_FakeOperationsClient)
    assert _FakeOperationsClient.last_instance is not None
    assert _FakeOperationsClient.last_instance.init_kwargs.get("token") == "sk-test"


# ---------------------------------------------------------------------------
# Step G — Mumega Playbook dogfood shelf seed
# ---------------------------------------------------------------------------


class _FakeShelfEconomyClient:
    """Records add_shelf_product calls and returns a canned product dict."""

    last_instance: "_FakeShelfEconomyClient | None" = None

    def __init__(self, **kwargs: Any) -> None:
        self.init_kwargs = kwargs
        self.calls: list[dict[str, Any]] = []
        _FakeShelfEconomyClient.last_instance = self

    def add_shelf_product(self, tenant: str, **kwargs: Any) -> dict[str, Any]:
        record = {"tenant": tenant, **kwargs}
        self.calls.append(record)
        return {**kwargs, "tenant": tenant, "active": kwargs.get("active", True)}


def test_step_g_skipped_for_non_mumega_tenants(
    cfg: cli_init.InitConfig,
) -> None:
    _FakeShelfEconomyClient.last_instance = None
    result = cli_init.step_g_seed_shelf(
        cfg,
        {"slug": "acme", "status": "provisioning"},
        client_factory=_FakeShelfEconomyClient,
    )
    assert result is None
    # Factory must never be invoked for non-mumega tenants.
    assert _FakeShelfEconomyClient.last_instance is None


def test_step_g_seeds_playbook_for_mumega_internal(
    cfg: cli_init.InitConfig,
) -> None:
    cfg.slug = "mumega-internal"
    _FakeShelfEconomyClient.last_instance = None

    result = cli_init.step_g_seed_shelf(
        cfg,
        {"slug": "mumega-internal", "status": "provisioning"},
        client_factory=_FakeShelfEconomyClient,
    )

    assert result is not None
    assert result["id"] == "mumega-playbook"
    assert result["price_cents"] == 2900
    assert result["grant_id"] == "mumega-playbook"

    assert _FakeShelfEconomyClient.last_instance is not None
    call = _FakeShelfEconomyClient.last_instance.calls[0]
    assert call["tenant"] == "mumega-internal"
    assert call["id"] == "mumega-playbook"
    assert call["title"] == "Mumega Playbook"
    assert call["mind_multiplier"] == 0.5


def test_step_g_forwards_token_to_client(cfg: cli_init.InitConfig) -> None:
    cfg.slug = "mumega-internal"
    cfg.saas_token = "sk-test"
    _FakeShelfEconomyClient.last_instance = None

    cli_init.step_g_seed_shelf(
        cfg,
        {"slug": "mumega-internal", "status": "provisioning"},
        client_factory=_FakeShelfEconomyClient,
    )
    assert _FakeShelfEconomyClient.last_instance is not None
    assert _FakeShelfEconomyClient.last_instance.init_kwargs.get("token") == "sk-test"


def test_parse_args_requires_slug_label_email() -> None:
    with pytest.raises(SystemExit):
        cli_init.parse_args([])


def test_parse_args_builds_config_with_defaults() -> None:
    cfg = cli_init.parse_args([
        "--slug", "acme",
        "--label", "Acme Co",
        "--email", "owner@acme.com",
    ])
    assert cfg.slug == "acme"
    assert cfg.plan is TenantPlan.STARTER
    assert cfg.dry_run is False
    assert cfg.saas_base_url is None


def test_main_dry_run_prints_and_returns_zero(
    cfg: cli_init.InitConfig, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = cli_init.main([
        "--slug", "acme",
        "--label", "Acme Co",
        "--email", "owner@acme.com",
        "--dry-run",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "sos init — acme" in out
    assert "Step A" in out
    assert "Step B" in out and "skipped" in out
    assert "Phase 7 shipped" in out
