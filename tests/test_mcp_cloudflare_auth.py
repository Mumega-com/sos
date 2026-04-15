from __future__ import annotations

import json


class _Response:
    def __init__(self, payload: dict[str, object], status_code: int = 200):
        self.text = json.dumps(payload)
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


def test_lookup_cloudflare_token_accepts_agent_only_payload(monkeypatch):
    from sos.mcp import sos_mcp_sse as module

    module._cloudflare_token_cache.clear()
    monkeypatch.setattr(module, "CF_API_TOKEN", "secret")
    monkeypatch.setattr(
        module.requests,
        "get",
        lambda *args, **kwargs: _Response({"active": True, "agent": "kasra"}),
    )

    ctx = module._lookup_cloudflare_token("token-1")

    assert ctx is not None
    assert ctx.agent_name == "kasra"
    assert ctx.tenant_id is None
    assert ctx.is_system is True
    assert ctx.source == "cloudflare_kv"


def test_lookup_cloudflare_token_rejects_inactive_payload(monkeypatch):
    from sos.mcp import sos_mcp_sse as module

    module._cloudflare_token_cache.clear()
    monkeypatch.setattr(module, "CF_API_TOKEN", "secret")
    monkeypatch.setattr(
        module.requests,
        "get",
        lambda *args, **kwargs: _Response({"active": False, "project": "demo", "agent": "kasra"}),
    )

    ctx = module._lookup_cloudflare_token("token-2")

    assert ctx is None

