"""Round-trip stability test for the QNFT contract."""
from __future__ import annotations

from datetime import datetime, timezone

from sos.contracts.qnft import QNFT, QNFTMintRequest


def _sample_qnft() -> QNFT:
    return QNFT(
        token_id="aaaaaaaa-0000-4000-8000-bbbbbbbbbbbb",
        tenant="acme",
        squad_id="acme-squad-social",
        role="social",
        seat_id="acme:seat:social",
        mint_cost_mind=100,
        minted_at=datetime(2026, 4, 19, 12, 0, 0, tzinfo=timezone.utc),
    )


def test_qnft_round_trips_through_model_dump() -> None:
    original = _sample_qnft()
    data = original.model_dump()
    restored = QNFT(**data)
    assert restored.token_id == original.token_id
    assert restored.tenant == original.tenant
    assert restored.role == original.role
    assert restored.mint_cost_mind == original.mint_cost_mind
    assert restored.claimed_by is None
    assert restored.claimed_at is None


def test_qnft_with_claim_round_trips() -> None:
    token = _sample_qnft()
    token_claimed = token.model_copy(
        update={
            "claimed_by": "agent-xyz",
            "claimed_at": datetime(2026, 4, 20, 0, 0, 0, tzinfo=timezone.utc),
        }
    )
    data = token_claimed.model_dump()
    restored = QNFT(**data)
    assert restored.claimed_by == "agent-xyz"
    assert restored.claimed_at is not None


def test_qnft_mint_request_round_trips() -> None:
    req = QNFTMintRequest(
        tenant="acme",
        squad_id="acme-squad-content",
        role="content",
        seat_id="acme:seat:content",
        cost_mind=200,
        project="acme",
    )
    data = req.model_dump()
    restored = QNFTMintRequest(**data)
    assert restored.cost_mind == 200
    assert restored.project == "acme"


def test_qnft_mint_request_optional_fields_default_none() -> None:
    req = QNFTMintRequest(
        tenant="acme",
        squad_id="acme-squad-content",
        role="content",
        seat_id="acme:seat:content",
    )
    assert req.cost_mind is None
    assert req.project is None
