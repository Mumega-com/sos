"""Tests for sos.contracts.errors — SOS-xxxx typed exception hierarchy.

Covers:
- Each code band has expected http_status
- SOSError.to_dict() shape is stable
- Subclass inheritance (catch by band vs catch by base)
- FastAPI handler returns correct JSON shape + status
- Enforcement tests still pass (SOS-4001/2/3/4 still behave the same)
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sos.contracts.errors import (
    # base
    SOSError,
    # 4xxx
    BusValidationError,
    EnvelopeError,
    SourcePatternError,
    UnknownTypeError,
    # 5xxx
    AuthMissing,
    AuthInvalid,
    AuthExpired,
    AuthForbidden,
    AuthRateLimited,
    # 6xxx
    BusDeliveryError,
    AgentNotFound,
    AgentOffline,
    SquadNotFound,
    TaskNotFound,
    TaskAlreadyClaimed,
    SkillNotFound,
    SkillInvocationFailed,
    # 7xxx
    InsufficientFunds,
    WalletNotFound,
    LedgerWriteFailed,
    SettlementRejected,
    CurrencyMismatch,
    UsageLogWriteFailed,
)
from sos.contracts.error_handlers import register_sos_error_handler


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_app_with_route(exc_factory):
    """Create a minimal FastAPI app that raises exc_factory() on GET /test."""
    app = FastAPI()
    register_sos_error_handler(app)

    @app.get("/test")
    async def _route():
        raise exc_factory()

    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# 1. to_dict() shape is stable
# ---------------------------------------------------------------------------


def test_to_dict_keys():
    exc = BusValidationError("schema mismatch", details={"field": "type"})
    d = exc.to_dict()
    assert set(d.keys()) == {"code", "message", "details"}


def test_to_dict_code_string():
    exc = BusValidationError("oops")
    assert exc.to_dict()["code"] == "SOS-4001"


def test_to_dict_message():
    exc = EnvelopeError("no type field")
    assert exc.to_dict()["message"] == "no type field"


def test_to_dict_details_default_empty():
    exc = UnknownTypeError("bad type")
    assert exc.to_dict()["details"] == {}


def test_to_dict_details_populated():
    exc = SourcePatternError("bad source", details={"source": "foo"})
    assert exc.to_dict()["details"] == {"source": "foo"}


# ---------------------------------------------------------------------------
# 2. HTTP status codes per band
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cls,expected_status", [
    # 4xxx — 422
    (BusValidationError, 422),
    (EnvelopeError, 422),
    (SourcePatternError, 422),
    (UnknownTypeError, 422),
    # 5xxx
    (AuthMissing, 401),
    (AuthInvalid, 401),
    (AuthExpired, 401),
    (AuthForbidden, 403),
    (AuthRateLimited, 429),
    # 6xxx
    (BusDeliveryError, 503),
    (AgentNotFound, 404),
    (AgentOffline, 503),
    (SquadNotFound, 404),
    (TaskNotFound, 404),
    (TaskAlreadyClaimed, 409),
    (SkillNotFound, 404),
    (SkillInvocationFailed, 500),
    # 7xxx
    (InsufficientFunds, 402),
    (WalletNotFound, 404),
    (LedgerWriteFailed, 500),
    (SettlementRejected, 409),
    (CurrencyMismatch, 422),
    (UsageLogWriteFailed, 500),
])
def test_http_status(cls, expected_status):
    exc = cls("test")
    assert exc.http_status == expected_status


# ---------------------------------------------------------------------------
# 3. Code strings per class
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cls,expected_code", [
    (BusValidationError, "SOS-4001"),
    (EnvelopeError, "SOS-4002"),
    (SourcePatternError, "SOS-4003"),
    (UnknownTypeError, "SOS-4004"),
    (AuthMissing, "SOS-5001"),
    (AuthInvalid, "SOS-5002"),
    (AuthExpired, "SOS-5003"),
    (AuthForbidden, "SOS-5004"),
    (AuthRateLimited, "SOS-5005"),
    (BusDeliveryError, "SOS-6001"),
    (AgentNotFound, "SOS-6002"),
    (AgentOffline, "SOS-6003"),
    (SquadNotFound, "SOS-6010"),
    (TaskNotFound, "SOS-6011"),
    (TaskAlreadyClaimed, "SOS-6012"),
    (SkillNotFound, "SOS-6020"),
    (SkillInvocationFailed, "SOS-6021"),
    (InsufficientFunds, "SOS-7001"),
    (WalletNotFound, "SOS-7002"),
    (LedgerWriteFailed, "SOS-7010"),
    (SettlementRejected, "SOS-7020"),
    (CurrencyMismatch, "SOS-7030"),
    (UsageLogWriteFailed, "SOS-7040"),
])
def test_code_string(cls, expected_code):
    exc = cls("test")
    assert exc.code == expected_code


# ---------------------------------------------------------------------------
# 4. Subclass inheritance — catch by band vs catch by base
# ---------------------------------------------------------------------------


def test_catch_by_subclass():
    with pytest.raises(BusValidationError):
        raise BusValidationError("schema error")


def test_catch_by_base_sos_error():
    """Every subclass is catchable as SOSError."""
    with pytest.raises(SOSError):
        raise BusValidationError("schema error")

    with pytest.raises(SOSError):
        raise AuthForbidden("no scope")

    with pytest.raises(SOSError):
        raise InsufficientFunds("broke")


def test_catch_by_band_4xxx():
    """4xxx classes are all subclasses of SOSError, independent of each other."""
    for cls in (BusValidationError, EnvelopeError, SourcePatternError, UnknownTypeError):
        with pytest.raises(SOSError):
            raise cls("test")


def test_catch_by_band_5xxx():
    for cls in (AuthMissing, AuthInvalid, AuthExpired, AuthForbidden, AuthRateLimited):
        with pytest.raises(SOSError):
            raise cls("test")


def test_catch_by_band_6xxx():
    for cls in (
        BusDeliveryError, AgentNotFound, AgentOffline,
        SquadNotFound, TaskNotFound, TaskAlreadyClaimed,
        SkillNotFound, SkillInvocationFailed,
    ):
        with pytest.raises(SOSError):
            raise cls("test")


def test_catch_by_band_7xxx():
    for cls in (
        InsufficientFunds, WalletNotFound, LedgerWriteFailed,
        SettlementRejected, CurrencyMismatch, UsageLogWriteFailed,
    ):
        with pytest.raises(SOSError):
            raise cls("test")


def test_different_bands_not_interchangeable():
    """Catching by a specific subclass does NOT catch a sibling class."""
    with pytest.raises(EnvelopeError):
        raise EnvelopeError("x")

    # BusValidationError should not be caught by except EnvelopeError
    caught_as_envelope = False
    caught_as_sos = False
    try:
        raise BusValidationError("x")
    except EnvelopeError:
        caught_as_envelope = True
    except SOSError:
        caught_as_sos = True
    assert not caught_as_envelope, "BusValidationError should not be caught as EnvelopeError"
    assert caught_as_sos, "BusValidationError should be caught as SOSError"


# ---------------------------------------------------------------------------
# 5. FastAPI handler — JSON shape + HTTP status
# ---------------------------------------------------------------------------


def test_fastapi_handler_status_code():
    client = make_app_with_route(lambda: AgentNotFound("agent:kasra not registered"))
    resp = client.get("/test")
    assert resp.status_code == 404


def test_fastapi_handler_json_shape():
    client = make_app_with_route(lambda: AgentNotFound("agent:kasra not registered"))
    resp = client.get("/test")
    body = resp.json()
    assert "error" in body
    err = body["error"]
    assert set(err.keys()) == {"code", "message", "details"}


def test_fastapi_handler_code_value():
    client = make_app_with_route(lambda: AuthForbidden("scope missing"))
    resp = client.get("/test")
    assert resp.json()["error"]["code"] == "SOS-5004"


def test_fastapi_handler_auth_missing_401():
    client = make_app_with_route(lambda: AuthMissing("no token"))
    resp = client.get("/test")
    assert resp.status_code == 401


def test_fastapi_handler_insufficient_funds_402():
    client = make_app_with_route(lambda: InsufficientFunds("need 100 MIND"))
    resp = client.get("/test")
    assert resp.status_code == 402


def test_fastapi_handler_details_passed_through():
    client = make_app_with_route(
        lambda: TaskAlreadyClaimed("taken", details={"claimed_by": "agent:kasra"})
    )
    resp = client.get("/test")
    assert resp.json()["error"]["details"]["claimed_by"] == "agent:kasra"


# ---------------------------------------------------------------------------
# 6. Enforcement backward-compat — SOS-4001/2/3/4 still work
# ---------------------------------------------------------------------------


def test_enforcement_no_type_raises_sos_4002():
    """enforce() on a dict with no 'type' produces code SOS-4002."""
    from sos.services.bus.enforcement import MessageValidationError, enforce

    with pytest.raises(MessageValidationError) as exc_info:
        enforce({"source": "agent:test", "payload": {}})
    assert exc_info.value.code == "SOS-4002"


def test_enforcement_unknown_type_raises_sos_4004():
    from sos.services.bus.enforcement import MessageValidationError, enforce

    with pytest.raises(MessageValidationError) as exc_info:
        enforce({"type": "legacy_chat", "source": "agent:test"})
    assert exc_info.value.code == "SOS-4004"


def test_enforcement_unknown_type_cause_is_sos_error():
    """The __cause__ of MessageValidationError is now an UnknownTypeError."""
    from sos.services.bus.enforcement import MessageValidationError, enforce

    with pytest.raises(MessageValidationError) as exc_info:
        enforce({"type": "legacy_chat", "source": "agent:test"})
    assert isinstance(exc_info.value.__cause__, UnknownTypeError)


def test_enforcement_envelope_error_cause():
    """Envelope errors carry EnvelopeError as __cause__."""
    from sos.services.bus.enforcement import MessageValidationError, enforce

    with pytest.raises(MessageValidationError) as exc_info:
        enforce({"source": "agent:test"})
    assert isinstance(exc_info.value.__cause__, EnvelopeError)
