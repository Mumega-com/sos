"""
Tests for scope-based authorization module.
"""

import pytest
from unittest.mock import MagicMock, AsyncMock

from sos.kernel.scopes import (
    Scope,
    ScopeContext,
    ScopeDeniedError,
    SCOPE_SETS,
    METHOD_SCOPES,
    check_scopes,
    parse_scopes,
    get_method_scopes,
    require_scope,
    require_method_scopes,
    expand_scope_set,
    can_read_agent,
    can_write_agent,
    can_access_memory,
    can_transact,
    can_execute_tools,
    is_admin,
)


class TestScopeEnum:
    """Tests for Scope enum."""

    def test_scope_values_are_strings(self):
        """Scope values should be strings."""
        assert Scope.AGENT_READ.value == "agent.read"
        assert Scope.MEMORY_WRITE.value == "memory.write"
        assert Scope.SYSTEM_ADMIN.value == "system.admin"

    def test_scope_is_string_enum(self):
        """Scope should be usable as string."""
        assert str(Scope.AGENT_READ) == "Scope.AGENT_READ"
        assert Scope.AGENT_READ == "agent.read"


class TestCheckScopes:
    """Tests for check_scopes function."""

    def test_empty_required_always_passes(self):
        """Empty required set should pass."""
        assert check_scopes(set(), set()) is True
        assert check_scopes({Scope.AGENT_READ}, set()) is True

    def test_exact_match_passes(self):
        """Exact scope match should pass."""
        required = {Scope.AGENT_READ}
        provided = {Scope.AGENT_READ}
        assert check_scopes(provided, required) is True

    def test_superset_passes(self):
        """Having more scopes than required should pass."""
        required = {Scope.AGENT_READ}
        provided = {Scope.AGENT_READ, Scope.AGENT_WRITE, Scope.MEMORY_READ}
        assert check_scopes(provided, required) is True

    def test_missing_scope_fails(self):
        """Missing required scope should fail."""
        required = {Scope.AGENT_READ, Scope.AGENT_WRITE}
        provided = {Scope.AGENT_READ}
        assert check_scopes(provided, required) is False

    def test_empty_provided_fails_when_required(self):
        """Empty provided with required scopes should fail."""
        required = {Scope.AGENT_READ}
        assert check_scopes(set(), required) is False


class TestParseScopes:
    """Tests for parse_scopes function."""

    def test_parse_valid_scopes(self):
        """Valid scope strings should be parsed."""
        result = parse_scopes(["agent.read", "memory.write"])
        assert result == {Scope.AGENT_READ, Scope.MEMORY_WRITE}

    def test_parse_invalid_scopes_skipped(self):
        """Invalid scope strings should be skipped."""
        result = parse_scopes(["agent.read", "invalid.scope", "memory.write"])
        assert result == {Scope.AGENT_READ, Scope.MEMORY_WRITE}

    def test_parse_empty_list(self):
        """Empty list should return empty set."""
        result = parse_scopes([])
        assert result == set()

    def test_parse_all_invalid(self):
        """All invalid scopes should return empty set."""
        result = parse_scopes(["foo", "bar", "baz"])
        assert result == set()


class TestGetMethodScopes:
    """Tests for get_method_scopes function."""

    def test_known_methods(self):
        """Known methods should return their scopes."""
        assert get_method_scopes("chat") == {Scope.AGENT_READ, Scope.AGENT_WRITE}
        assert get_method_scopes("memory_store") == {Scope.MEMORY_WRITE}
        assert get_method_scopes("tools_execute") == {Scope.TOOLS_EXECUTE}

    def test_unknown_method(self):
        """Unknown method should return empty set."""
        assert get_method_scopes("unknown_method") == set()


class TestRequireScope:
    """Tests for require_scope decorator."""

    @pytest.mark.asyncio
    async def test_passes_with_required_scopes(self):
        """Should pass when required scopes are present."""
        @require_scope(Scope.AGENT_READ)
        async def handler(request):
            return "success"

        # Mock request with scope context
        request = MagicMock()
        request.state.scope_context = ScopeContext(
            scopes={Scope.AGENT_READ, Scope.AGENT_WRITE}
        )

        result = await handler(request)
        assert result == "success"

    @pytest.mark.asyncio
    async def test_fails_without_required_scopes(self):
        """Should raise ScopeDeniedError when missing scopes."""
        @require_scope(Scope.AGENT_WRITE, Scope.MEMORY_WRITE)
        async def handler(request):
            return "success"

        request = MagicMock()
        request.state.scope_context = ScopeContext(
            scopes={Scope.AGENT_READ}  # Missing AGENT_WRITE and MEMORY_WRITE
        )

        with pytest.raises(ScopeDeniedError) as exc_info:
            await handler(request)

        assert Scope.AGENT_WRITE in exc_info.value.missing
        assert Scope.MEMORY_WRITE in exc_info.value.missing

    @pytest.mark.asyncio
    async def test_fails_with_no_scope_context(self):
        """Should fail when no scope context is present."""
        @require_scope(Scope.AGENT_READ)
        async def handler(request):
            return "success"

        request = MagicMock(spec=[])  # No state attribute

        with pytest.raises(ScopeDeniedError):
            await handler(request)


class TestRequireMethodScopes:
    """Tests for require_method_scopes decorator."""

    @pytest.mark.asyncio
    async def test_uses_method_scope_mapping(self):
        """Should use METHOD_SCOPES for the method."""
        @require_method_scopes("memory_store")
        async def store(request):
            return "stored"

        request = MagicMock()
        request.state.scope_context = ScopeContext(
            scopes={Scope.MEMORY_WRITE}
        )

        result = await store(request)
        assert result == "stored"

    @pytest.mark.asyncio
    async def test_unknown_method_allows_all(self):
        """Unknown method should allow all (passthrough)."""
        @require_method_scopes("unknown_method")
        async def handler(request):
            return "allowed"

        request = MagicMock()
        request.state.scope_context = ScopeContext(scopes=set())

        result = await handler(request)
        assert result == "allowed"


class TestScopeSets:
    """Tests for predefined scope sets."""

    def test_readonly_has_read_scopes(self):
        """Readonly set should have read-only scopes."""
        readonly = SCOPE_SETS["readonly"]
        assert Scope.AGENT_READ in readonly
        assert Scope.MEMORY_READ in readonly
        assert Scope.AGENT_WRITE not in readonly
        assert Scope.MEMORY_WRITE not in readonly

    def test_user_has_basic_scopes(self):
        """User set should have basic user scopes."""
        user = SCOPE_SETS["user"]
        assert Scope.AGENT_READ in user
        assert Scope.AGENT_WRITE in user
        assert Scope.MEMORY_READ in user
        assert Scope.MEMORY_WRITE in user
        assert Scope.AGENT_ADMIN not in user

    def test_admin_has_all_scopes(self):
        """Admin set should have all scopes."""
        admin = SCOPE_SETS["admin"]
        assert admin == set(Scope)

    def test_expand_scope_set(self):
        """expand_scope_set should return correct sets."""
        assert expand_scope_set("readonly") == SCOPE_SETS["readonly"]
        assert expand_scope_set("admin") == set(Scope)
        assert expand_scope_set("unknown") == set()


class TestScopeDeniedError:
    """Tests for ScopeDeniedError exception."""

    def test_error_contains_missing_scopes(self):
        """Error should list missing scopes."""
        required = {Scope.AGENT_READ, Scope.AGENT_WRITE}
        provided = {Scope.AGENT_READ}

        error = ScopeDeniedError(required, provided)

        assert error.required == required
        assert error.provided == provided
        assert error.missing == {Scope.AGENT_WRITE}
        assert "agent.write" in str(error)


class TestScopeContext:
    """Tests for ScopeContext dataclass."""

    def test_default_empty_scopes(self):
        """Default context should have empty scopes."""
        ctx = ScopeContext()
        assert ctx.scopes == set()
        assert ctx.subject is None
        assert ctx.issuer is None

    def test_context_with_values(self):
        """Context should store provided values."""
        ctx = ScopeContext(
            scopes={Scope.AGENT_READ},
            subject="agent:River",
            issuer="sos:identity"
        )
        assert ctx.scopes == {Scope.AGENT_READ}
        assert ctx.subject == "agent:River"
        assert ctx.issuer == "sos:identity"


class TestConvenienceFunctions:
    """Tests for convenience scope checking functions."""

    def test_can_read_agent(self):
        """can_read_agent should check AGENT_READ."""
        assert can_read_agent({Scope.AGENT_READ}) is True
        assert can_read_agent({Scope.AGENT_WRITE}) is False
        assert can_read_agent(set()) is False

    def test_can_write_agent(self):
        """can_write_agent should check AGENT_WRITE."""
        assert can_write_agent({Scope.AGENT_WRITE}) is True
        assert can_write_agent({Scope.AGENT_READ}) is False

    def test_can_access_memory(self):
        """can_access_memory should check MEMORY_READ or MEMORY_WRITE."""
        assert can_access_memory({Scope.MEMORY_READ}) is True
        assert can_access_memory({Scope.MEMORY_WRITE}) is True
        assert can_access_memory({Scope.AGENT_READ}) is False

    def test_can_transact(self):
        """can_transact should check ECONOMY_TRANSACT."""
        assert can_transact({Scope.ECONOMY_TRANSACT}) is True
        assert can_transact({Scope.ECONOMY_READ}) is False

    def test_can_execute_tools(self):
        """can_execute_tools should check TOOLS_EXECUTE."""
        assert can_execute_tools({Scope.TOOLS_EXECUTE}) is True
        assert can_execute_tools({Scope.TOOLS_LIST}) is False

    def test_is_admin(self):
        """is_admin should check SYSTEM_ADMIN."""
        assert is_admin({Scope.SYSTEM_ADMIN}) is True
        assert is_admin({Scope.SYSTEM_CONFIG}) is False
        assert is_admin(set()) is False
