"""
SOS Protocol Error Codes

Standardized error codes and exceptions for all SOS services.

Error Code Ranges (legacy IntEnum — kept for backward compatibility):
- 1xxx: General errors
- 2xxx: Authentication/Authorization
- 3xxx: Resource errors
- 4xxx: Rate limiting
- 5xxx: Model/LLM errors
- 6xxx: Memory errors
- 7xxx: Economy errors
- 8xxx: Tools errors

SOS-xxxx string codes (v0.4.2+ taxonomy — use these for new code):
- SOS-4xxx: Bus / contract validation
- SOS-5xxx: Auth
- SOS-6xxx: Runtime / bus delivery / squad
- SOS-7xxx: Economy / billing / settlement
"""

from __future__ import annotations

from enum import IntEnum
from typing import Any, Optional
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Legacy IntEnum codes — kept for backward compatibility
# ---------------------------------------------------------------------------

class ErrorCode(IntEnum):
    """SOS protocol error codes."""

    # General (1xxx)
    UNKNOWN = 1000
    INVALID_REQUEST = 1001
    INTERNAL_ERROR = 1002
    SERVICE_UNAVAILABLE = 1003
    TIMEOUT = 1004
    VALIDATION_ERROR = 1005

    # Auth (2xxx)
    UNAUTHORIZED = 2001
    FORBIDDEN = 2002
    TOKEN_EXPIRED = 2003
    TOKEN_INVALID = 2004
    SCOPE_DENIED = 2005
    CAPABILITY_MISSING = 2006

    # Resource (3xxx)
    NOT_FOUND = 3001
    ALREADY_EXISTS = 3002
    CONFLICT = 3003
    GONE = 3004

    # Rate Limiting (4xxx)
    RATE_LIMITED = 4001
    QUOTA_EXCEEDED = 4002
    CIRCUIT_OPEN = 4003

    # Model (5xxx)
    MODEL_UNAVAILABLE = 5001
    MODEL_OVERLOADED = 5002
    CONTEXT_TOO_LONG = 5003
    GENERATION_FAILED = 5004
    NO_MODELS_AVAILABLE = 5005

    # Memory (6xxx)
    MEMORY_FULL = 6001
    VECTOR_ERROR = 6002
    EMBEDDING_FAILED = 6003
    RETRIEVAL_FAILED = 6004

    # Economy (7xxx)
    INSUFFICIENT_FUNDS = 7001
    INVALID_TRANSACTION = 7002
    LEDGER_ERROR = 7003

    # Tools (8xxx)
    TOOL_NOT_FOUND = 8001
    TOOL_EXECUTION_FAILED = 8002
    TOOL_TIMEOUT = 8003
    TOOL_PERMISSION_DENIED = 8004
    SANDBOX_ERROR = 8005


@dataclass
class SOSErrorData:
    """Structured error response (legacy dataclass — kept for backward compat)."""

    code: ErrorCode
    message: str
    detail: Optional[str] = None
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON response."""
        result: dict[str, Any] = {
            "code": int(self.code),
            "message": self.message,
        }
        if self.detail:
            result["detail"] = self.detail
        if self.context:
            result["context"] = self.context
        return result


class SOSException(Exception):
    """Base exception for SOS services (legacy — kept for backward compat)."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        detail: str | None = None,
        **context: Any,
    ) -> None:
        self.error = SOSErrorData(
            code=code,
            message=message,
            detail=detail,
            context=context or {},
        )
        super().__init__(message)

    @property
    def code(self) -> ErrorCode:
        return self.error.code

    def to_dict(self) -> dict[str, Any]:
        return {"ok": False, "error": self.error.to_dict()}


# Convenience exception classes (legacy)
class AuthError(SOSException):
    """Authentication/authorization error."""

    def __init__(self, message: str = "Unauthorized", **context: Any) -> None:
        super().__init__(ErrorCode.UNAUTHORIZED, message, **context)


class ForbiddenError(SOSException):
    """Access forbidden error."""

    def __init__(self, message: str = "Access denied", **context: Any) -> None:
        super().__init__(ErrorCode.FORBIDDEN, message, **context)


class ScopeDeniedError(SOSException):
    """Missing required scope."""

    def __init__(self, required: list[str], provided: list[str] | None = None) -> None:
        super().__init__(
            ErrorCode.SCOPE_DENIED,
            "Missing required scope",
            detail=f"Required: {required}",
            required=required,
            provided=provided or [],
        )


class NotFoundError(SOSException):
    """Resource not found."""

    def __init__(self, resource: str, identifier: str | None = None) -> None:
        msg = f"{resource} not found"
        if identifier:
            msg = f"{resource} '{identifier}' not found"
        super().__init__(ErrorCode.NOT_FOUND, msg, resource=resource, id=identifier)


class RateLimitError(SOSException):
    """Rate limit exceeded."""

    def __init__(self, retry_after: int | None = None) -> None:
        super().__init__(
            ErrorCode.RATE_LIMITED,
            "Rate limit exceeded",
            detail=f"Retry after {retry_after}s" if retry_after else None,
            retry_after=retry_after,
        )


class ModelError(SOSException):
    """Model/LLM error."""

    def __init__(self, code: ErrorCode, message: str, model: str | None = None, **context: Any) -> None:
        super().__init__(code, message, model=model, **context)


class MemoryError(SOSException):
    """Memory service error."""

    def __init__(self, code: ErrorCode, message: str, **context: Any) -> None:
        super().__init__(code, message, **context)


class ToolError(SOSException):
    """Tool execution error."""

    def __init__(self, code: ErrorCode, message: str, tool: str | None = None, **context: Any) -> None:
        super().__init__(code, message, tool=tool, **context)


class ValidationError(SOSException):
    """Request validation error."""

    def __init__(self, message: str, field: str | None = None, **context: Any) -> None:
        super().__init__(
            ErrorCode.VALIDATION_ERROR,
            message,
            field=field,
            **context,
        )


# Response helpers (legacy)
def error_response(error: SOSException) -> dict[str, Any]:
    """Create standardized error response."""
    return error.to_dict()


def success_response(result: Any = None, **extra: Any) -> dict[str, Any]:
    """Create standardized success response."""
    response: dict[str, Any] = {"ok": True}
    if result is not None:
        response["result"] = result
    response.update(extra)
    return response


# ---------------------------------------------------------------------------
# v0.4.2 typed exception hierarchy — SOS-xxxx string codes
# ---------------------------------------------------------------------------


class SOSError(Exception):
    """Base class for all SOS typed exceptions (v0.4.2+).

    Attributes:
        code        SOS-xxxx string identifier, e.g. "SOS-4001"
        http_status HTTP status code to return to clients
        message     Human-readable description
        details     Structured context dict (optional)
    """

    code: str = "SOS-0000"
    http_status: int = 500

    def __init__(
        self,
        message: str | None = None,
        http_status: int | None = None,
        details: dict[str, Any] | None = None,
        *,
        # allow callers to override code at construction time
        code: str | None = None,
    ) -> None:
        self.message = message or self.__class__.__doc__ or "An error occurred"
        if http_status is not None:
            self.http_status = http_status
        if code is not None:
            self.code = code
        self.details: dict[str, Any] = details or {}
        super().__init__(f"[{self.code}] {self.message}")

    def to_dict(self) -> dict[str, Any]:
        """Stable JSON-serialisable shape consumed by the FastAPI handler."""
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


# --- 4xxx — bus / contract validation ----------------------------------------


class BusValidationError(SOSError):
    """SOS-4001: Message failed schema validation."""

    code = "SOS-4001"
    http_status = 422


class MessageValidationError(ValueError):
    """Raised when a v1-typed bus message fails schema validation.

    Kept as a standalone ValueError (not a SOSError subclass) for backward
    compatibility with call sites that catch `ValueError` / this exact class.
    New code should catch the typed SOSError subclasses directly
    (BusValidationError, EnvelopeError, SourcePatternError, UnknownTypeError).
    """

    def __init__(
        self,
        code: str,
        message: str,
        original_type: str | None = None,
    ) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.original_type = original_type


class EnvelopeError(SOSError):
    """SOS-4002: Message envelope is malformed (missing 'type')."""

    code = "SOS-4002"
    http_status = 422


class SourcePatternError(SOSError):
    """SOS-4003: Source field missing or not in agent:<name> shape."""

    code = "SOS-4003"
    http_status = 422


class UnknownTypeError(SOSError):
    """SOS-4004: Message type is not in the v1 catalog."""

    code = "SOS-4004"
    http_status = 422


# --- 5xxx — auth --------------------------------------------------------------


class AuthMissing(SOSError):
    """SOS-5001: No bearer token present in request."""

    code = "SOS-5001"
    http_status = 401


class AuthInvalid(SOSError):
    """SOS-5002: Bearer token is malformed or signature invalid."""

    code = "SOS-5002"
    http_status = 401


class AuthExpired(SOSError):
    """SOS-5003: Token exists but is no longer active (TTL elapsed)."""

    code = "SOS-5003"
    http_status = 401


class AuthForbidden(SOSError):
    """SOS-5004: Token is valid but scope or permission is insufficient."""

    code = "SOS-5004"
    http_status = 403


class AuthRateLimited(SOSError):
    """SOS-5005: Too many auth attempts from this client."""

    code = "SOS-5005"
    http_status = 429


# --- 6xxx — runtime / bus delivery / squad ------------------------------------


class BusDeliveryError(SOSError):
    """SOS-6001: Redis XADD or bus publish failed."""

    code = "SOS-6001"
    http_status = 503


class AgentNotFound(SOSError):
    """SOS-6002: Named agent does not exist in the registry."""

    code = "SOS-6002"
    http_status = 404


class AgentOffline(SOSError):
    """SOS-6003: Agent exists but its heartbeat TTL has expired."""

    code = "SOS-6003"
    http_status = 503


class SquadNotFound(SOSError):
    """SOS-6010: Squad identifier not found."""

    code = "SOS-6010"
    http_status = 404


class TaskNotFound(SOSError):
    """SOS-6011: Task identifier not found."""

    code = "SOS-6011"
    http_status = 404


class TaskAlreadyClaimed(SOSError):
    """SOS-6012: Task has already been claimed by another agent."""

    code = "SOS-6012"
    http_status = 409


class SkillNotFound(SOSError):
    """SOS-6020: Skill identifier not registered."""

    code = "SOS-6020"
    http_status = 404


class SkillInvocationFailed(SOSError):
    """SOS-6021: Skill was found but its execution raised an error."""

    code = "SOS-6021"
    http_status = 500


# --- 7xxx — economy / billing / settlement ------------------------------------


class InsufficientFunds(SOSError):
    """SOS-7001: Wallet balance is below the required amount."""

    code = "SOS-7001"
    http_status = 402


class WalletNotFound(SOSError):
    """SOS-7002: Wallet address or identifier not found."""

    code = "SOS-7002"
    http_status = 404


class LedgerWriteFailed(SOSError):
    """SOS-7010: Ledger persistence layer rejected the write."""

    code = "SOS-7010"
    http_status = 500


class SettlementRejected(SOSError):
    """SOS-7020: Settlement transaction was rejected by the network."""

    code = "SOS-7020"
    http_status = 409


class CurrencyMismatch(SOSError):
    """SOS-7030: Source and destination currencies are incompatible."""

    code = "SOS-7030"
    http_status = 422


class UsageLogWriteFailed(SOSError):
    """SOS-7040: Usage event could not be persisted."""

    code = "SOS-7040"
    http_status = 500
