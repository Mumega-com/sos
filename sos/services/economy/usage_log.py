"""Usage event log — append-only record of model-call telemetry.

Separate from `work_ledger.py`, which settles squad work units and bounties.
This log captures model-call events (tokens, cost, latency) reported by any
tenant — Python adapters, Cloudflare workers, edge Pages Functions, or any
external client authenticated with a bus token.

Storage: append-only JSONL at `~/.mumega/usage_events.jsonl` by default.
Override with `SOS_USAGE_LOG_PATH` env var.

**Boundary note (SOS vs Mumega):**
This module is SOS (protocol): canonical `UsageEvent` shape, tenant scoping,
append-only log primitive. Currency-agnostic — `cost_micros` can represent
USD, $MIND token units, or any other unit the operator defines.

Mumega's commercial layer reads this log to compute per-tenant invoices,
apply volume tiers, issue Stripe charges, etc. None of that is SOS concern.

**Economy ledger integration (island #4):**
Every append with ``cost_micros > 0`` and ``cost_currency == "MIND"`` triggers
a best-effort ``settle_usage_event()`` call.  Settlement failures never block
the append — the event is written first, and if settlement fails the event's
``metadata.settlement_status`` is tagged ``"deferred"`` and a new line is
re-written.  The JSONL log is always the audit trail; the wallet is the ledger.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

# UsageEvent lives in sos.contracts.economy — imported here for backward
# compatibility with call sites that still reference
# `sos.services.economy.usage_log.UsageEvent`.
from sos.contracts.economy import UsageEvent  # re-export

_log = logging.getLogger("sos.usage_log")


def _default_log_path() -> Path:
    """`~/.mumega/usage_events.jsonl` — overridable via `SOS_USAGE_LOG_PATH`."""
    override = os.environ.get("SOS_USAGE_LOG_PATH")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".mumega" / "usage_events.jsonl"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Internal async settlement helpers
# ---------------------------------------------------------------------------

async def _settle(event: "UsageEvent", wallet: Any) -> Any:
    """Thin async shim that imports settlement lazily to avoid circular deps."""
    from sos.services.economy.settlement import settle_usage_event
    return await settle_usage_event(event, wallet)


def _run_async(coro: Any) -> Any:
    """Run a coroutine in the current event loop or a new one.

    UsageLog.append() may be called from both sync and async contexts.  We
    try the running loop first; if there is none we spin up a temporary one.
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # We are inside an async context — schedule and wait via
            # run_coroutine_threadsafe so we don't deadlock the outer loop.
            import concurrent.futures
            future = asyncio.run_coroutine_threadsafe(coro, loop)
            return future.result(timeout=10)
        else:
            return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


class UsageLog:
    """Append-only JSONL log of `UsageEvent`s.

    Thread-safety: the append path uses `O_APPEND` under the hood (Python's
    default `"a"` mode on POSIX), which guarantees atomic appends for writes
    up to PIPE_BUF (4096 bytes on Linux). Each event's JSON line is well
    below that, so concurrent writers on the same machine do not corrupt the
    file. For multi-machine deployments, front this with a proper queue.

    Economy integration
    -------------------
    Pass a ``SovereignWallet`` instance as ``wallet`` to enable automatic
    settlement.  Every event with ``cost_micros > 0`` and
    ``cost_currency == "MIND"`` will trigger a best-effort
    ``settle_usage_event()`` call immediately after the JSONL write.  If
    settlement fails the event's ``metadata.settlement_status`` is patched to
    ``"deferred"`` and a corrected line is appended so the log stays the
    authoritative audit trail.
    """

    def __init__(self, path: Optional[Path] = None, wallet: Any = None) -> None:
        self.path = path or _default_log_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._wallet = wallet

    def _write_line(self, event: UsageEvent) -> None:
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")

    def append(self, event: UsageEvent) -> UsageEvent:
        """Write one event. Returns the stored event (with server-assigned id / received_at if unset).

        If a wallet is configured and the event carries a MIND cost, settlement
        is attempted synchronously (runs the async coroutine in whatever event
        loop is running, or a fresh one).  Settlement failures are swallowed —
        the JSONL write is never rolled back.
        """
        if not event.id:
            event.id = str(uuid4())
        if not event.received_at:
            event.received_at = _now_iso()

        # Write the event first — log is always the audit trail
        self._write_line(event)

        # Best-effort settlement
        if self._wallet and event.cost_micros > 0 and event.cost_currency.upper() == "MIND":
            try:
                result = _run_async(_settle(event, self._wallet))
                if result.settlement_status == "deferred":
                    # Patch the metadata in-place and append a corrected line
                    event.metadata["settlement_status"] = "deferred"
                    event.metadata["settlement_errors"] = result.errors[:3]  # truncate for JSONL
                    self._write_line(event)
                    _log.warning(
                        "settlement deferred for event %s: %s",
                        event.id,
                        result.errors,
                    )
                elif result.settlement_status == "settled":
                    event.metadata["settlement_status"] = "settled"
                    if result.total_charged:
                        event.metadata["transaction_id"] = (
                            result.outcomes[0].transaction_id if result.outcomes else None
                        )
            except Exception as exc:  # noqa: BLE001
                event.metadata["settlement_status"] = "deferred"
                event.metadata["settlement_errors"] = [str(exc)]
                self._write_line(event)
                _log.warning("settlement exception for event %s: %s", event.id, exc)

        return event

    def read_all(self, tenant: str | None = None, limit: int | None = None) -> list[UsageEvent]:
        """Read events, optionally filtered by tenant. Newest last."""
        if not self.path.exists():
            return []
        out: list[UsageEvent] = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if tenant and data.get("tenant") != tenant:
                    continue
                out.append(UsageEvent(**data))
        if limit is not None and limit > 0:
            return out[-limit:]
        return out

    def count(self, tenant: str | None = None) -> int:
        return len(self.read_all(tenant=tenant))
