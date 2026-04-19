"""``sos init`` — first-boot tenant provisioning (Phase 5, v0.9.4).

Onboards a new Mumega tenant end-to-end. Old dev-wizard lives at
``sos.cli.setup`` now; this module is the Phase 5 flow described in
``docs/plans/2026-04-19-mumega-mothership.md`` §5.

Five steps, sequenced:
    A. POST /tenants on the SaaS service                (unblocked, shipped)
    B. Copy inkwell/instances/_template → <slug>/, wrangler deploy   (blocked)
    C. Create default squads + mint qNFTs in the economy             (blocked)
    D. Write standing_workflows.json from template                   (blocked)
    E. Trigger first pulse run in the operations service             (blocked)

v0.9.4-alpha.1 ships Step A only. B–E raise ``NotImplementedError`` with a
pointer to the infrastructure piece that's missing. Each stub lists the
exact prerequisite so follow-up tasks are unambiguous.

Run::

    python -m sos.cli.init --slug acme --label "Acme Co" --email owner@acme.com \
        --plan starter --domain acme.com --industry consulting

Use ``--dry-run`` to print the payload without hitting the saas service.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import Any

from sos.clients.saas import SaasClient
from sos.contracts.tenant import TenantCreate, TenantPlan


def green(text: str) -> str:
    return f"\033[1;32m{text}\033[0m"


def bold(text: str) -> str:
    return f"\033[1m{text}\033[0m"


def warn(text: str) -> str:
    return f"\033[1;33m{text}\033[0m"


def red(text: str) -> str:
    return f"\033[1;31m{text}\033[0m"


@dataclass
class InitConfig:
    slug: str
    label: str
    email: str
    plan: TenantPlan
    domain: str | None = None
    industry: str | None = None
    tagline: str | None = None
    saas_base_url: str | None = None
    saas_token: str | None = None
    dry_run: bool = False


def build_payload(cfg: InitConfig) -> dict[str, Any]:
    """Validate via TenantCreate and return the wire JSON body."""
    model = TenantCreate(
        slug=cfg.slug,
        label=cfg.label,
        email=cfg.email,
        plan=cfg.plan,
        domain=cfg.domain,
        industry=cfg.industry,
        tagline=cfg.tagline,
    )
    return model.model_dump(mode="json", exclude_none=True)


def step_a_provision_tenant(
    cfg: InitConfig, *, client_factory: Any = SaasClient
) -> dict[str, Any]:
    """Step A — POST /tenants on the SaaS service.

    Returns the freshly created Tenant row (status=provisioning). Raises
    whatever the SaasClient raises on HTTP error — the caller logs and
    bails out before attempting B–E.
    """
    payload = build_payload(cfg)
    if cfg.dry_run:
        print(f"  {warn('[dry-run]')} would POST /tenants with:")
        for k, v in payload.items():
            print(f"    {k}: {v}")
        return {"slug": cfg.slug, "status": "provisioning", "_dry_run": True}

    kwargs: dict[str, Any] = {}
    if cfg.saas_base_url:
        kwargs["base_url"] = cfg.saas_base_url
    if cfg.saas_token:
        kwargs["token"] = cfg.saas_token
    client = client_factory(**kwargs)
    return client.create_tenant(payload)


def step_b_deploy_inkwell(cfg: InitConfig, tenant: dict[str, Any]) -> None:
    """Step B — copy ``inkwell/instances/_template/`` and ``wrangler deploy``.

    Blocked: the ``_template/`` directory does not exist in the Inkwell
    repo yet. Follow-up is tracked in the Phase 5 plan §5.3 — the
    template must export ``inkwell.config.ts`` with ``{{SLUG}}`` /
    ``{{LABEL}}`` / ``{{DOMAIN}}`` placeholders and a stub
    ``standing_workflows.json``.
    """
    raise NotImplementedError(
        "Step B blocked on inkwell/instances/_template/ scaffold + "
        "Cloudflare Pages credentials wired into CI. "
        "See docs/plans/2026-04-19-mumega-mothership.md §5.3."
    )


def step_c_seed_squads(cfg: InitConfig, tenant: dict[str, Any]) -> None:
    """Step C — POST default squads to /agents/cards and mint qNFTs.

    Blocked: the economy service has no qNFT mint endpoint yet. The
    ``/agents/cards`` POST exists (registry service), but the "hire"
    transaction that debits the tenant wallet and issues a qNFT per
    seat does not. Follow-up is Phase 5 §5.4 + the qNFT contract work
    noted in the plan.
    """
    raise NotImplementedError(
        "Step C blocked on economy qNFT mint endpoint + qNFT contract. "
        "See docs/plans/2026-04-19-mumega-mothership.md §5.4."
    )


def step_d_write_workflows(cfg: InitConfig, tenant: dict[str, Any]) -> None:
    """Step D — write ``inkwell/instances/<slug>/standing_workflows.json``.

    Blocked on Step B (the instance directory doesn't exist until the
    Inkwell template is copied). A real Step D is a file write; it's
    only stubbed here because running it in isolation would leave an
    orphan file outside any instance.
    """
    raise NotImplementedError(
        "Step D blocked on Step B (instance directory must exist first). "
        "See docs/plans/2026-04-19-mumega-mothership.md §5.5."
    )


def step_e_trigger_pulse(cfg: InitConfig, tenant: dict[str, Any]) -> None:
    """Step E — kick the first pulse run for this tenant/project.

    Blocked: ``sos.services.operations.pulse`` exposes the runner but
    not a "start for tenant" entry point reachable via the saas client.
    The operations client needs a ``trigger_pulse(tenant)`` method that
    maps to the right HTTP surface.
    """
    raise NotImplementedError(
        "Step E blocked on operations HTTP client helper. "
        "See docs/plans/2026-04-19-mumega-mothership.md §5.6."
    )


def parse_args(argv: list[str] | None = None) -> InitConfig:
    parser = argparse.ArgumentParser(
        prog="sos init",
        description="Provision a new Mumega tenant end-to-end.",
    )
    parser.add_argument("--slug", required=True, help="Tenant slug (a-z0-9-)")
    parser.add_argument("--label", required=True, help="Human-readable label")
    parser.add_argument("--email", required=True, help="Owner email")
    parser.add_argument(
        "--plan",
        default="starter",
        choices=[p.value for p in TenantPlan],
        help="Billing plan tier",
    )
    parser.add_argument("--domain", default=None, help="Custom domain (optional)")
    parser.add_argument("--industry", default=None, help="Industry hint")
    parser.add_argument("--tagline", default=None, help="One-line tagline")
    parser.add_argument(
        "--saas-base-url",
        default=None,
        help="Override SaaS service URL (default: http://localhost:8075)",
    )
    parser.add_argument(
        "--saas-token",
        default=None,
        help="Admin token (default: reads SOS_SAAS_ADMIN_KEY / SOS_SYSTEM_TOKEN)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print Step A payload without hitting the SaaS service",
    )
    args = parser.parse_args(argv)
    return InitConfig(
        slug=args.slug,
        label=args.label,
        email=args.email,
        plan=TenantPlan(args.plan),
        domain=args.domain,
        industry=args.industry,
        tagline=args.tagline,
        saas_base_url=args.saas_base_url,
        saas_token=args.saas_token,
        dry_run=args.dry_run,
    )


def main(argv: list[str] | None = None) -> int:
    cfg = parse_args(argv)

    print(f"\n{bold(f'sos init — {cfg.slug}')}")
    print("=" * 48)

    # Step A — real.
    print(f"\n{bold('Step A')} — provision tenant via SaaS /tenants")
    try:
        tenant = step_a_provision_tenant(cfg)
    except Exception as exc:
        print(f"  {red('FAILED')}: {exc}")
        return 1
    print(f"  {green('ok')} — tenant row created (status=provisioning)")

    # Steps B–E — stubs with remediation pointers.
    pending: list[tuple[str, Any]] = [
        ("Step B", step_b_deploy_inkwell),
        ("Step C", step_c_seed_squads),
        ("Step D", step_d_write_workflows),
        ("Step E", step_e_trigger_pulse),
    ]
    for name, fn in pending:
        print(f"\n{bold(name)} — running…")
        try:
            fn(cfg, tenant)
        except NotImplementedError as exc:
            print(f"  {warn('skipped')}: {exc}")

    print("\n" + "=" * 48)
    print(f"{green('Phase 5 Step A shipped.')} B–E blocked (see above).")
    print(f"  slug:   {cfg.slug}")
    print(f"  label:  {cfg.label}")
    print(f"  plan:   {cfg.plan.value}")
    print(f"  status: {tenant.get('status', 'unknown')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
