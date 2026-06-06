"""_ensure_task_in_scope assignee exception (2026-06-06).

Hermetic — constructs MCPAuthContext directly, no tokens, no services.
Covers the BYPASS paths explicitly (kasra-review pattern #3: tests must
exercise how the gate can be defeated, not only the happy allow/deny).
"""
import pytest
from fastapi import HTTPException

from sos.mcp.sos_mcp_sse import MCPAuthContext, _ensure_task_in_scope


def _auth(agent="kasra", tenant="sos", scope="", is_system=False):
    ctx = MCPAuthContext(token="t", tenant_id=tenant, is_system=is_system)
    ctx.agent_name = agent
    ctx.scope = scope
    return ctx


TASK = {"project": "mumega", "assignee": "kasra"}


def test_system_token_passes():
    _ensure_task_in_scope(TASK, _auth(is_system=True, tenant=None))


def test_same_project_passes():
    _ensure_task_in_scope({"project": "sos", "assignee": "x"}, _auth(tenant="sos"))


def test_substrate_assignee_crosses_project():
    # kasra (internal, sos token) closes a brain task in project=mumega
    _ensure_task_in_scope(TASK, _auth(agent="kasra", tenant="sos"))


def test_non_assignee_substrate_agent_blocked():
    with pytest.raises(HTTPException) as e:
        _ensure_task_in_scope(TASK, _auth(agent="loom", tenant="sos"))
    assert e.value.status_code == 403


def test_tenant_bound_agent_with_substrate_token_blocked():
    # adv B1: sol holds an empty-scope sos token but is tenant-bound —
    # not in the coordination-agent narrowing set → blocked even as assignee
    task = {"project": "dentalnearyou", "assignee": "sol"}
    with pytest.raises(HTTPException):
        _ensure_task_in_scope(task, _auth(agent="sol", tenant="sos"))


def test_river_assignee_crosses_project():
    _ensure_task_in_scope({"project": "trop", "assignee": "river"}, _auth(agent="river", tenant="sos"))


def test_tenant_agent_token_cannot_ride_name():
    # s184 class: tenant fork named "kasra" with tenant-agent scope — blocked
    with pytest.raises(HTTPException):
        _ensure_task_in_scope(TASK, _auth(agent="kasra", tenant="viamar", scope="tenant-agent"))


def test_tenant_project_token_blocked_even_if_assignee():
    # token homed on a tenant project (not substrate) — blocked even as assignee
    with pytest.raises(HTTPException):
        _ensure_task_in_scope(TASK, _auth(agent="kasra", tenant="viamar", scope=""))


def test_scoped_internal_token_blocked():
    # any non-empty scope (customer / tenant / tenant-agent) disqualifies
    for s in ("customer", "tenant", "tenant-agent"):
        with pytest.raises(HTTPException):
            _ensure_task_in_scope(TASK, _auth(agent="kasra", tenant="sos", scope=s))


def test_empty_agent_name_blocked():
    with pytest.raises(HTTPException):
        _ensure_task_in_scope({"project": "mumega", "assignee": ""}, _auth(agent="", tenant="sos"))


def test_assignee_mismatch_blocked():
    with pytest.raises(HTTPException):
        _ensure_task_in_scope({"project": "mumega", "assignee": "river"}, _auth(agent="kasra", tenant="sos"))
