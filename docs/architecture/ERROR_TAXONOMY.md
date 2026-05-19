# SOS Error Taxonomy

**Version:** v0.4.2  
**Module:** `sos.contracts.errors`  
**Handler:** `sos.contracts.error_handlers.register_sos_error_handler`

Clients receive errors in this envelope:

```json
{"error": {"code": "SOS-4001", "message": "...", "details": {...}}}
```

Catch by class (`except BusValidationError`) or by code string (`exc.code == "SOS-4001"`).

---

## SOS-4xxx — Bus / Contract Validation

Raised at the bus publish boundary before a message reaches Redis.

| Code | Class | HTTP | When raised |
|------|-------|------|-------------|
| SOS-4001 | `BusValidationError` | 422 | Message dict fails Pydantic/JSON-Schema parse for its declared v1 type |
| SOS-4002 | `EnvelopeError` | 422 | Envelope has no `type` field at all |
| SOS-4003 | `SourcePatternError` | 422 | `source` field missing or not in `agent:<name>` pattern |
| SOS-4004 | `UnknownTypeError` | 422 | `type` is not in the v1 catalog (`announce`, `send`, `wake`, `ask`, `task_*`, `agent_joined`) |

---

## SOS-5xxx — Auth

Raised by auth middleware and token-validation layers.

| Code | Class | HTTP | When raised |
|------|-------|------|-------------|
| SOS-5001 | `AuthMissing` | 401 | No `Authorization: Bearer` header present |
| SOS-5002 | `AuthInvalid` | 401 | Token present but signature or format is invalid |
| SOS-5003 | `AuthExpired` | 401 | Token is structurally valid but TTL has elapsed |
| SOS-5004 | `AuthForbidden` | 403 | Token is valid but lacks required scope or permission |
| SOS-5005 | `AuthRateLimited` | 429 | Too many auth attempts from this client |

---

## SOS-6xxx — Runtime / Bus Delivery / Squad

Raised during agent coordination, bus delivery, and squad task lifecycle.

| Code | Class | HTTP | When raised |
|------|-------|------|-------------|
| SOS-6001 | `BusDeliveryError` | 503 | Redis XADD or bus publish operation failed |
| SOS-6002 | `AgentNotFound` | 404 | Named agent does not exist in the agent registry |
| SOS-6003 | `AgentOffline` | 503 | Agent exists but heartbeat TTL has expired |
| SOS-6010 | `SquadNotFound` | 404 | Squad identifier not found in the squad registry |
| SOS-6011 | `TaskNotFound` | 404 | Task identifier does not exist |
| SOS-6012 | `TaskAlreadyClaimed` | 409 | Task has already been claimed by another agent |
| SOS-6020 | `SkillNotFound` | 404 | Skill identifier not registered in the skill registry |
| SOS-6021 | `SkillInvocationFailed` | 500 | Skill was found but its execution raised an error |

---

## SOS-7xxx — Economy / Billing / Settlement

Raised by the economy service, ledger, and settlement pipeline.

| Code | Class | HTTP | When raised |
|------|-------|------|-------------|
| SOS-7001 | `InsufficientFunds` | 402 | Wallet balance is below the required amount for the operation |
| SOS-7002 | `WalletNotFound` | 404 | Wallet address or identifier does not exist |
| SOS-7010 | `LedgerWriteFailed` | 500 | Ledger persistence layer rejected or failed the write |
| SOS-7020 | `SettlementRejected` | 409 | Settlement transaction was rejected by the network |
| SOS-7030 | `CurrencyMismatch` | 422 | Source and destination currencies are incompatible |
| SOS-7040 | `UsageLogWriteFailed` | 500 | Usage event could not be persisted to the log store |

---

## Band semantics

| Band | Meaning | Typical response action |
|------|---------|------------------------|
| 4xxx | Client sent a bad message — fix the payload | Return 422, do not retry |
| 5xxx | Auth problem — check token validity/scope | Return 401/403/429 |
| 6xxx | Runtime failure — agent/squad/skill layer | Return 404/409/503 |
| 7xxx | Economy failure — funds/ledger/settlement | Return 402/404/409/422/500 |

---

## FastAPI wiring

Register the handler once per service app:

```python
from fastapi import FastAPI
from sos.contracts.error_handlers import register_sos_error_handler

app = FastAPI()
register_sos_error_handler(app)
```

After registration any route that raises a `SOSError` subclass (or `SOSError`
itself) will return:

```json
HTTP/1.1 <http_status>
Content-Type: application/json

{"error": {"code": "SOS-xxxx", "message": "...", "details": {...}}}
```

---

## Enforcement codes (bus boundary)

`sos.services.bus.enforcement` raises `MessageValidationError` (a `ValueError`
subclass) for backward compatibility with existing call sites. The `code`
attribute on `MessageValidationError` now matches the SOS-4xxx taxonomy,
and the underlying `SOSError` subclass is attached as `__cause__`.

Migration path: catch `BusValidationError | EnvelopeError | SourcePatternError |
UnknownTypeError` at new call sites; legacy sites that catch
`MessageValidationError` continue to work unchanged.
