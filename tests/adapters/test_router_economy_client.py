"""v0.4.6 Step 2 — P1-02 close.

Before: sos.adapters.router imported sos.services.economy.wallet.SovereignWallet
in-process (R2 violation).

After: ModelRouter takes an EconomyClient (HTTP) and debits through it. The
client resolves its token from SOS_ECONOMY_TOKEN / SOS_SYSTEM_TOKEN at
construction time.
"""
from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

ADAPTERS_DIR = Path(__file__).resolve().parents[2] / "sos" / "adapters"
ROUTER_FILE = ADAPTERS_DIR / "router.py"


def _imported_modules(file_path: Path) -> set[str]:
    tree = ast.parse(file_path.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
            for n in node.names:
                mods.add(f"{node.module}.{n.name}")
    return mods


def test_router_does_not_import_economy_service():
    mods = _imported_modules(ROUTER_FILE)
    leaks = [m for m in mods if m.startswith("sos.services.economy")]
    assert leaks == [], f"adapters/router.py still reaches into service: {leaks}"


def test_router_imports_economy_client():
    src = ROUTER_FILE.read_text(encoding="utf-8")
    assert "from sos.clients.economy import EconomyClient" in src


@pytest.mark.asyncio
async def test_record_cost_debits_via_economy_client():
    from sos.adapters.base import UsageInfo
    from sos.adapters.router import ModelRouter

    fake_wallet = AsyncMock()
    fake_wallet.debit = AsyncMock(return_value={"balance": 42.0})

    router = ModelRouter(wallet=fake_wallet)
    usage = UsageInfo(
        provider="anthropic",
        model="claude-sonnet-4-5",
        input_tokens=100,
        output_tokens=50,
        cost_cents=5,  # 5¢ → 50 RU
    )

    await router._record_cost(user_id="acme", usage=usage, agent_id="shabrang")

    fake_wallet.debit.assert_awaited_once()
    args, kwargs = fake_wallet.debit.call_args
    assert args[0] == "acme"
    assert args[1] == 50.0  # 5 cents × 10 RU/cent
    assert kwargs["reason"] == "llm:anthropic:claude-sonnet-4-5:shabrang"


@pytest.mark.asyncio
async def test_record_cost_skipped_when_zero_cost():
    from sos.adapters.base import UsageInfo
    from sos.adapters.router import ModelRouter

    fake_wallet = AsyncMock()
    router = ModelRouter(wallet=fake_wallet)

    await router._record_cost(
        user_id="acme",
        usage=UsageInfo(provider="google", model="gemini-2.0-flash", cost_cents=0),
        agent_id="shabrang",
    )

    fake_wallet.debit.assert_not_called()


@pytest.mark.asyncio
async def test_record_cost_swallows_debit_failures():
    from sos.adapters.base import UsageInfo
    from sos.adapters.router import ModelRouter

    fake_wallet = AsyncMock()
    fake_wallet.debit = AsyncMock(side_effect=RuntimeError("economy down"))

    router = ModelRouter(wallet=fake_wallet)
    # Must not raise — budget errors are non-fatal.
    await router._record_cost(
        user_id="acme",
        usage=UsageInfo(provider="anthropic", model="claude", cost_cents=3),
        agent_id="shabrang",
    )


def test_default_wallet_is_economy_client():
    from sos.adapters.router import ModelRouter
    from sos.clients.economy import EconomyClient

    router = ModelRouter()
    assert isinstance(router._wallet, EconomyClient)
