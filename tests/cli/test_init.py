"""Unit tests for ``sos init`` (Phase 5 — v0.9.4 tenant provisioning).

Covers Step A (SaaS ``/tenants`` POST) and verifies B–E raise
``NotImplementedError`` with pointers to the Phase 5 plan. Step A is
exercised against a fake SaasClient so no network is required.
"""
from __future__ import annotations

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


@pytest.mark.parametrize(
    "fn",
    [
        cli_init.step_b_deploy_inkwell,
        cli_init.step_c_seed_squads,
        cli_init.step_d_write_workflows,
        cli_init.step_e_trigger_pulse,
    ],
)
def test_steps_b_through_e_are_documented_stubs(
    fn: Any, cfg: cli_init.InitConfig
) -> None:
    with pytest.raises(NotImplementedError) as exc_info:
        fn(cfg, {"slug": "acme", "status": "provisioning"})
    assert "docs/plans/2026-04-19-mumega-mothership.md" in str(exc_info.value)


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
    assert "Phase 5 Step A shipped." in out
