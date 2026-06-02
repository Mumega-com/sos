"""s184/#167 regression: substrate memory must gate on substrate-project, not a
forgeable agent-name. A tenant-project token must never reach mumega-internal,
even if its agent name collides with a substrate-squad name."""
import pytest

from sos.mcp.sos_mcp_sse import _memory_scope, MCPAuthContext


def _auth(**kw):
    d = dict(token="t", tenant_id=None)
    d.update(kw)
    return MCPAuthContext(**d)


@pytest.mark.parametrize("label,auth,is_internal", [
    # Legit substrate squad — bound to the substrate project → keep mumega-internal.
    ("codex@sos", _auth(agent_name="codex", tenant_id="sos", scope=""), True),
    ("kasra@sos", _auth(agent_name="kasra", tenant_id="sos", scope=""), True),
    ("explicit system", _auth(is_system=True, agent_name="kasra"), True),
    # Cross-tenant leak vectors (GH #167) — internal NAME but TENANT project → blocked.
    ("codex@torivers", _auth(agent_name="codex", tenant_id="torivers", scope=""), False),
    ("dandan@dnu", _auth(agent_name="dandan", tenant_id="dnu", scope=""), False),
    # No-name token must not collapse to AGENT_SELF/substrate (claude.ai vector).
    ("no-name@dnu", _auth(agent_name="", tenant_id="dnu", scope=""), False),
])
def test_substrate_memory_gated_on_project_not_name(label, auth, is_internal):
    ws = _memory_scope(auth).workspace_id
    if is_internal:
        assert ws == "mumega-internal", f"{label} should keep substrate memory"
    else:
        assert ws != "mumega-internal", f"{label} must NOT reach substrate memory (#167)"
