"""v0.4.6 Step 6 — P1-06 close.

Before: sos.cli.onboard imported sos.services.saas.registry.TenantRegistry
in-process (R2 violation).

After: cli/onboard.py drives tenant CRUD via sos.clients.saas.SaasClient,
matching the pattern already used by the billing service.
"""
from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock, patch

ONBOARD_FILE = Path(__file__).resolve().parents[2] / "sos" / "cli" / "onboard.py"


def _imported_modules(file_path: Path) -> set[str]:
    tree = ast.parse(file_path.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
            for n in node.names:
                mods.add(f"{node.module}.{n.name}")
    return mods


def test_onboard_does_not_import_saas_service():
    mods = _imported_modules(ONBOARD_FILE)
    leaks = [m for m in mods if m.startswith("sos.services.saas")]
    assert leaks == [], f"cli/onboard.py still reaches into saas: {leaks}"


def test_onboard_uses_saas_client():
    src = ONBOARD_FILE.read_text(encoding="utf-8")
    assert "from sos.clients.saas import SaasClient" in src


def test_get_saas_client_returns_saas_client_instance():
    from sos.cli.onboard import _get_saas_client
    from sos.clients.saas import SaasClient

    # Reset the module-level singleton so repeated test runs get a fresh pick.
    import sos.cli.onboard as mod
    mod._saas_client = None

    client = _get_saas_client()
    assert isinstance(client, SaasClient)
    # Second call returns the same instance (singleton cache)
    assert _get_saas_client() is client


def test_onboard_saas_registration_calls_create_and_activate():
    """Smoke — the lazy client is invoked with create_tenant + activate_tenant."""
    import sos.cli.onboard as mod

    mod._saas_client = None  # reset singleton
    fake = MagicMock()
    fake.create_tenant.return_value = {"subdomain": "acme.mumega.com"}
    fake.activate_tenant.return_value = {"ok": True}

    with patch("sos.clients.saas.SaasClient", return_value=fake):
        with patch("sos.cli.onboard.create_user"), \
             patch("sos.cli.onboard.create_bus_token", return_value="tok"), \
             patch("sos.cli.onboard.write_settings"), \
             patch("sos.cli.onboard.write_claude_md"), \
             patch("sos.cli.onboard.copy_hooks"), \
             patch("sos.cli.onboard.create_squad"), \
             patch("sos.cli.onboard.fix_ownership"), \
             patch("sos.cli.onboard.setup_tmux"):
            mod.onboard("acme", "acme.com", "spai_fake", model="haiku")

    fake.create_tenant.assert_called_once()
    fake.activate_tenant.assert_called_once_with("acme", squad_id="acme", bus_token="tok")
