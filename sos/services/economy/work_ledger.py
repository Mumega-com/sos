"""
Work Ledger
===========
Local-first work unit + proof models with an append-only event log.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

import os; resolve_runtime_path = lambda x, **kw: os.path.expanduser(f"~/.mumega/{x}")
get_env_bool = lambda k, d=False: os.environ.get(k, str(d)).lower() in ("1","true","yes")
from .payment_status import PaymentStatus
from .work_slashing import WorkSlashingLedger, build_slashing_record
from .worker_registry import WorkerRegistry

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.utcnow().isoformat()

def _default_slash_ratio() -> float:
    try:
        return float(os.getenv("MUMEGA_SLASH_RATIO", "0.1"))
    except ValueError:
        return 0.1


def _add_seconds(iso_timestamp: str, seconds: int) -> Optional[str]:
    try:
        base = datetime.fromisoformat(iso_timestamp)
    except Exception:
        return None
    return (base + timedelta(seconds=seconds)).isoformat()


def _dispute_deadline(unit: "WorkUnit") -> Optional[str]:
    if unit.dispute_window_seconds <= 0:
        return None
    base = unit.completed_at or unit.updated_at or unit.created_at
    if not base:
        return None
    return _add_seconds(base, unit.dispute_window_seconds)


def _default_dispute_sla_seconds() -> Optional[int]:
    raw = os.getenv("MUMEGA_DISPUTE_SLA_SECONDS", "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


# TD-023: Use centralized boolean parsing
def _require_assigned_resolver() -> bool:
    return get_env_bool("MUMEGA_DISPUTE_REQUIRE_ASSIGNEE", default=False)


def _require_arbiter_role() -> bool:
    return get_env_bool("MUMEGA_DISPUTE_REQUIRE_ARBITER_ROLE", default=True)


def _strict_work_transitions() -> bool:
    return get_env_bool("MUMEGA_WORK_STRICT_TRANSITIONS", default=True)


def _format_statuses(statuses: List["WorkUnitStatus"]) -> str:
    return ", ".join(sorted(status.value for status in statuses))


class WorkUnitStatus(str, Enum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    IN_PROGRESS = "in_progress"
    SUBMITTED = "submitted"
    VERIFIED = "verified"
    REJECTED = "rejected"
    DISPUTED = "disputed"
    PAID = "paid"


class ProofStatus(str, Enum):
    PENDING = "pending"
    VERIFIED = "verified"
    REJECTED = "rejected"


class WorkEventType(str, Enum):
    CREATED = "created"
    CLAIMED = "claimed"
    STARTED = "started"
    PROOF_SUBMITTED = "proof_submitted"
    PROOF_VERIFIED = "proof_verified"
    PROOF_REJECTED = "proof_rejected"
    WITNESS_ASSIGNED = "witness_assigned"
    DISPUTED = "disputed"
    DISPUTE_RESOLVED = "dispute_resolved"
    DISPUTE_ASSIGNED = "dispute_assigned"
    SLASHING_APPLIED = "slashing_applied"
    PAYOUT_REQUESTED = "payout_requested"
    PAYOUT_SENT = "payout_sent"


class DisputeStatus(str, Enum):
    """Status of a work dispute."""
    OPEN = "open"
    UNDER_REVIEW = "under_review"
    RESOLVED_WORKER_WINS = "resolved_worker_wins"
    RESOLVED_CHALLENGER_WINS = "resolved_challenger_wins"
    ESCALATED = "escalated"


@dataclass
class DisputeRecord:
    """Record of a work dispute for arbitration."""
    id: str
    work_id: str
    challenger_id: str
    reason: str
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    status: DisputeStatus = DisputeStatus.OPEN
    evidence_refs: List[str] = field(default_factory=list)
    resolution_notes: Optional[str] = None
    resolved_at: Optional[str] = None
    resolver_id: Optional[str] = None
    assigned_to: Optional[str] = None
    assigned_at: Optional[str] = None
    due_at: Optional[str] = None
    sla_seconds: Optional[int] = None
    slash_amount: float = 0.0
    slash_target: Optional[str] = None  # worker_id or challenger_id
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        work_id: str,
        challenger_id: str,
        reason: str,
        evidence_refs: Optional[List[str]] = None,
    ) -> "DisputeRecord":
        return cls(
            id=str(uuid4()),
            work_id=work_id,
            challenger_id=challenger_id,
            reason=reason,
            evidence_refs=evidence_refs or [],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "work_id": self.work_id,
            "challenger_id": self.challenger_id,
            "reason": self.reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "status": self.status.value,
            "evidence_refs": self.evidence_refs,
            "resolution_notes": self.resolution_notes,
            "resolved_at": self.resolved_at,
            "resolver_id": self.resolver_id,
            "assigned_to": self.assigned_to,
            "assigned_at": self.assigned_at,
            "due_at": self.due_at,
            "sla_seconds": self.sla_seconds,
            "slash_amount": self.slash_amount,
            "slash_target": self.slash_target,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DisputeRecord":
        status = data.get("status", DisputeStatus.OPEN)
        if not isinstance(status, DisputeStatus):
            try:
                status = DisputeStatus(str(status))
            except ValueError:
                status = DisputeStatus.OPEN
        return cls(
            id=data.get("id", str(uuid4())),
            work_id=data.get("work_id", ""),
            challenger_id=data.get("challenger_id", ""),
            reason=data.get("reason", ""),
            created_at=data.get("created_at", _now_iso()),
            updated_at=data.get("updated_at", data.get("created_at", _now_iso())),
            status=status,
            evidence_refs=data.get("evidence_refs", []),
            resolution_notes=data.get("resolution_notes"),
            resolved_at=data.get("resolved_at"),
            resolver_id=data.get("resolver_id"),
            assigned_to=data.get("assigned_to"),
            assigned_at=data.get("assigned_at"),
            due_at=data.get("due_at"),
            sla_seconds=data.get("sla_seconds"),
            slash_amount=data.get("slash_amount", 0.0),
            slash_target=data.get("slash_target"),
            metadata=data.get("metadata", {}),
        )



@dataclass
class WorkUnit:
    id: str
    title: str
    description: str
    requester_id: str
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    status: WorkUnitStatus = WorkUnitStatus.QUEUED
    input_ref: Optional[str] = None
    input_hash: Optional[str] = None
    expected_output: Optional[str] = None
    verify_method: Optional[str] = None
    dispute_window_seconds: int = 3600
    payout_amount: Optional[float] = None
    payout_currency: Optional[str] = None
    escrow_id: Optional[str] = None
    worker_id: Optional[str] = None
    observer_id: Optional[str] = None
    proof_id: Optional[str] = None
    completed_at: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        title: str,
        description: str,
        requester_id: str,
        **kwargs: Any,
    ) -> "WorkUnit":
        return cls(
            id=str(uuid4()),
            title=title,
            description=description,
            requester_id=requester_id,
            **kwargs,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "requester_id": self.requester_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "status": self.status.value,
            "input_ref": self.input_ref,
            "input_hash": self.input_hash,
            "expected_output": self.expected_output,
            "verify_method": self.verify_method,
            "dispute_window_seconds": self.dispute_window_seconds,
            "payout_amount": self.payout_amount,
            "payout_currency": self.payout_currency,
            "escrow_id": self.escrow_id,
            "worker_id": self.worker_id,
            "observer_id": self.observer_id,
            "proof_id": self.proof_id,
            "completed_at": self.completed_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WorkUnit":
        status = data.get("status", WorkUnitStatus.QUEUED)
        if not isinstance(status, WorkUnitStatus):
            try:
                status = WorkUnitStatus(str(status))
            except ValueError:
                status = WorkUnitStatus.QUEUED
        return cls(
            id=data.get("id", str(uuid4())),
            title=data.get("title", ""),
            description=data.get("description", ""),
            requester_id=data.get("requester_id", ""),
            created_at=data.get("created_at", _now_iso()),
            updated_at=data.get("updated_at", data.get("created_at", _now_iso())),
            status=status,
            input_ref=data.get("input_ref"),
            input_hash=data.get("input_hash"),
            expected_output=data.get("expected_output"),
            verify_method=data.get("verify_method"),
            dispute_window_seconds=data.get("dispute_window_seconds", 3600),
            payout_amount=data.get("payout_amount"),
            payout_currency=data.get("payout_currency"),
            escrow_id=data.get("escrow_id"),
            worker_id=data.get("worker_id"),
            observer_id=data.get("observer_id"),
            proof_id=data.get("proof_id"),
            completed_at=data.get("completed_at"),
            metadata=data.get("metadata", {}) or {},
        )


@dataclass
class Proof:
    id: str
    work_id: str
    worker_id: str
    submitted_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    output_ref: Optional[str] = None
    output_hash: Optional[str] = None
    proof_hash: Optional[str] = None
    observer_id: Optional[str] = None
    verification: Dict[str, Any] = field(default_factory=dict)
    status: ProofStatus = ProofStatus.PENDING
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        work_id: str,
        worker_id: str,
        **kwargs: Any,
    ) -> "Proof":
        return cls(
            id=str(uuid4()),
            work_id=work_id,
            worker_id=worker_id,
            **kwargs,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "work_id": self.work_id,
            "worker_id": self.worker_id,
            "submitted_at": self.submitted_at,
            "updated_at": self.updated_at,
            "output_ref": self.output_ref,
            "output_hash": self.output_hash,
            "proof_hash": self.proof_hash,
            "observer_id": self.observer_id,
            "verification": self.verification,
            "status": self.status.value,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Proof":
        status = data.get("status", ProofStatus.PENDING)
        if not isinstance(status, ProofStatus):
            try:
                status = ProofStatus(str(status))
            except ValueError:
                status = ProofStatus.PENDING
        return cls(
            id=data.get("id", str(uuid4())),
            work_id=data.get("work_id", ""),
            worker_id=data.get("worker_id", ""),
            submitted_at=data.get("submitted_at", _now_iso()),
            updated_at=data.get("updated_at", data.get("submitted_at", _now_iso())),
            output_ref=data.get("output_ref"),
            output_hash=data.get("output_hash"),
            proof_hash=data.get("proof_hash"),
            observer_id=data.get("observer_id"),
            verification=data.get("verification", {}) or {},
            status=status,
            metadata=data.get("metadata", {}) or {},
        )


@dataclass
class WorkEvent:
    event_id: str
    work_id: str
    event_type: WorkEventType
    actor_id: Optional[str] = None
    timestamp: str = field(default_factory=_now_iso)
    payload: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        work_id: str,
        event_type: WorkEventType,
        actor_id: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> "WorkEvent":
        return cls(
            event_id=str(uuid4()),
            work_id=work_id,
            event_type=event_type,
            actor_id=actor_id,
            payload=payload or {},
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "work_id": self.work_id,
            "event_type": self.event_type.value,
            "actor_id": self.actor_id,
            "timestamp": self.timestamp,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WorkEvent":
        event_type = data.get("event_type", WorkEventType.CREATED)
        if not isinstance(event_type, WorkEventType):
            try:
                event_type = WorkEventType(str(event_type))
            except ValueError:
                event_type = WorkEventType.CREATED
        return cls(
            event_id=data.get("event_id", str(uuid4())),
            work_id=data.get("work_id", ""),
            event_type=event_type,
            actor_id=data.get("actor_id"),
            timestamp=data.get("timestamp", _now_iso()),
            payload=data.get("payload", {}) or {},
        )


class WorkEventLog:
    def append(self, event: WorkEvent) -> None:
        raise NotImplementedError

    def list_events(self, work_id: Optional[str] = None, limit: int = 200) -> List[WorkEvent]:
        raise NotImplementedError


class FileWorkEventLog(WorkEventLog):
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event: WorkEvent) -> None:
        try:
            with self.path.open("a", encoding="utf-8") as handle:
                json.dump(event.to_dict(), handle, sort_keys=True)
                handle.write("\n")
        except Exception as exc:
            logger.warning("Failed to append work event: %s", exc)

    def list_events(self, work_id: Optional[str] = None, limit: int = 200) -> List[WorkEvent]:
        if not self.path.exists():
            return []

        events: List[WorkEvent] = []
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    event = WorkEvent.from_dict(data)
                    if work_id and event.work_id != work_id:
                        continue
                    events.append(event)
        except Exception as exc:
            logger.warning("Failed to read work events: %s", exc)
            return []

        events.sort(key=lambda item: item.timestamp, reverse=True)
        if limit and len(events) > limit:
            return events[:limit]
        return events


class WorkLedger:
    """Local store for work units and proofs with an append-only event log."""

    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = Path(data_dir) if data_dir else resolve_runtime_path("work")
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.work_path = self.data_dir / "work_units.json"
        self.proof_path = self.data_dir / "proofs.json"
        self.dispute_path = self.data_dir / "disputes.json"
        self.event_log = FileWorkEventLog(self.data_dir / "work_events.jsonl")

        self._work_units: Dict[str, WorkUnit] = {}
        self._proofs: Dict[str, Proof] = {}
        self._disputes: Dict[str, DisputeRecord] = {}
        self._supabase = None
        self._init_supabase_sync()
        self._load_state()
        self._merge_supabase_state()

    def _record_event(self, event: WorkEvent, unit: Optional[WorkUnit] = None) -> None:
        """Record event to log and emit receipt"""
        self.event_log.append(event)
        self._emit_receipt(event, unit=unit)

    def _execute_slash(self, dispute: DisputeRecord, work_unit: WorkUnit, resolver_id: str) -> Optional[Dict[str, Any]]:
        if dispute.slash_amount <= 0 or not dispute.slash_target:
            return None

        execute_onchain = get_env_bool("MUMEGA_SLASH_EXECUTE_ONCHAIN", default=False)
        target_id = dispute.slash_target
        registry = WorkerRegistry()
        profile = registry.get_worker(target_id) if target_id else None
        wallet = profile.wallet_address if profile else None
        currency = work_unit.payout_currency or "MIND"

        metadata = {
            "resolver_id": resolver_id,
            "reason": dispute.resolution_notes,
            "execute_onchain": execute_onchain,
        }

        status = PaymentStatus.SIMULATED.value
        if execute_onchain:
            status = PaymentStatus.BLOCKED.value
            metadata["block_reason"] = "onchain_slashing_not_supported"

        record = build_slashing_record(
            work_id=work_unit.id,
            dispute_id=dispute.id,
            target_id=target_id,
            wallet_address=wallet,
            amount=dispute.slash_amount,
            currency=currency,
            status=status,
            metadata=metadata,
        )

        WorkSlashingLedger().add(record)

        if profile:
            registry.apply_slash(target_id, dispute.slash_amount, metadata={
                "work_id": work_unit.id,
                "dispute_id": dispute.id,
                "status": status,
            })

        return {
            "slashing_id": record.id,
            "status": record.status,
            "currency": record.currency,
        }

    def _require_status(self, unit: WorkUnit, allowed: List[WorkUnitStatus], action: str) -> None:
        if not _strict_work_transitions():
            return
        if unit.status not in allowed:
            allowed_text = _format_statuses(allowed)
            raise ValueError(f"{action} requires status {allowed_text} (current: {unit.status.value})")

    def _require_worker(self, unit: WorkUnit, worker_id: str, action: str) -> None:
        if not _strict_work_transitions():
            return
        if unit.worker_id and unit.worker_id != worker_id:
            raise ValueError(f"{action} denied: work assigned to {unit.worker_id}")

    def _emit_receipt(self, event: WorkEvent, unit: Optional[WorkUnit] = None) -> None:
        try:
            from mumega.core.receipts import build_receipt, get_receipt_store

            position = os.getenv("MUMEGA_POSITION_DEFAULT") or os.getenv("MUMEGA_POSITION")
            agent = {
                "name": os.getenv("MUMEGA_AGENT_NAME", "mumega"),
                "position": position,
            }
            job = {
                "type": "work_unit",
                "id": event.work_id,
                "event_id": event.event_id,
                "event_type": event.event_type.value,
            }
            intent = {
                "text": unit.title if unit else None,
                "rider_id": unit.requester_id if unit else event.actor_id,
            }
            execution = {
                "event_type": event.event_type.value,
                "payload": event.payload,
            }
            governance = {
                "actor_id": event.actor_id,
            }
            receipt = build_receipt(
                agent=agent,
                job=job,
                intent=intent,
                execution=execution,
                governance=governance,
                settlement={},
            )
            get_receipt_store().append(receipt.to_dict())
        except Exception as exc:
            logger.debug("Receipt logging failed for work event %s: %s", event.event_type, exc)

    def _init_supabase_sync(self) -> None:
        try:
            from .work_supabase import SupabaseWorkSync
        except Exception:
            self._supabase = None
            return
        sync = SupabaseWorkSync()
        self._supabase = sync if sync.enabled else None

    def _load_state(self) -> None:
        self._work_units = {}
        self._proofs = {}
        self._disputes = {}

        if self.work_path.exists():
            try:
                with self.work_path.open("r", encoding="utf-8") as handle:
                    data = json.load(handle)
                for item in data.get("work_units", []):
                    unit = WorkUnit.from_dict(item)
                    self._work_units[unit.id] = unit
            except Exception as exc:
                logger.warning("Failed to load work units: %s", exc)

        if self.proof_path.exists():
            try:
                with self.proof_path.open("r", encoding="utf-8") as handle:
                    data = json.load(handle)
                for item in data.get("proofs", []):
                    proof = Proof.from_dict(item)
                    self._proofs[proof.id] = proof
            except Exception as exc:
                logger.warning("Failed to load proofs: %s", exc)

        if self.dispute_path.exists():
            try:
                with self.dispute_path.open("r", encoding="utf-8") as handle:
                    data = json.load(handle)
                for item in data.get("disputes", []):
                    dispute = DisputeRecord.from_dict(item)
                    self._disputes[dispute.id] = dispute
            except Exception as exc:
                logger.warning("Failed to load disputes: %s", exc)

    def _merge_supabase_state(self) -> None:
        if not self._supabase:
            return

        updated = False

        for payload in self._supabase.fetch_units():
            unit = WorkUnit.from_dict(payload)
            local = self._work_units.get(unit.id)
            if not local or self._is_newer(unit.updated_at, local.updated_at):
                self._work_units[unit.id] = unit
                updated = True

        for payload in self._supabase.fetch_proofs():
            proof = Proof.from_dict(payload)
            local = self._proofs.get(proof.id)
            if not local or self._is_newer(proof.updated_at, local.updated_at):
                self._proofs[proof.id] = proof
                updated = True

        for payload in self._supabase.fetch_disputes():
            dispute = DisputeRecord.from_dict(payload)
            local = self._disputes.get(dispute.id)
            if not local or self._is_newer(dispute.updated_at, local.updated_at):
                self._disputes[dispute.id] = dispute
                updated = True

        if updated:
            self._save_work_units()
            self._save_proofs()
            self._save_disputes()

    def _is_newer(self, candidate: Optional[str], current: Optional[str]) -> bool:
        if not candidate:
            return False
        if not current:
            return True
        try:
            return datetime.fromisoformat(candidate) > datetime.fromisoformat(current)
        except Exception:
            return False

    def _save_work_units(self) -> None:
        payload = {"work_units": [unit.to_dict() for unit in self._work_units.values()]}
        with self.work_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)

    def _save_proofs(self) -> None:
        payload = {"proofs": [proof.to_dict() for proof in self._proofs.values()]}
        with self.proof_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)

    def _save_disputes(self) -> None:
        payload = {"disputes": [d.to_dict() for d in self._disputes.values()]}
        with self.dispute_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)

    def submit_dispute(
        self,
        work_id: str,
        challenger_id: str,
        reason: str,
        evidence_refs: Optional[List[str]] = None,
    ) -> DisputeRecord:
        """Submit a dispute against a work unit."""
        unit = self.get_work_unit(work_id)
        if not unit:
            raise ValueError(f"Work unit {work_id} not found")
        
        if unit.status != WorkUnitStatus.VERIFIED:
            raise ValueError(f"Cannot dispute work in status {unit.status}")

        deadline = _dispute_deadline(unit)
        if deadline:
            try:
                deadline_dt = datetime.fromisoformat(deadline)
            except Exception:
                deadline_dt = None
            if deadline_dt and datetime.utcnow() > deadline_dt:
                raise ValueError(f"Dispute window closed at {deadline}")

        dispute = DisputeRecord.create(
            work_id=work_id,
            challenger_id=challenger_id,
            reason=reason,
            evidence_refs=evidence_refs,
        )
        dispute.updated_at = dispute.created_at
        self._disputes[dispute.id] = dispute
        self._save_disputes()

        # Update work status
        unit.status = WorkUnitStatus.DISPUTED
        unit.updated_at = _now_iso()
        self._save_work_units()

        # Log event
        event = WorkEvent.create(
            work_id=work_id,
            event_type=WorkEventType.DISPUTED,
            actor_id=challenger_id,
            payload={
                "dispute_id": dispute.id,
                "reason": reason,
            },
        )
        self._record_event(event)
        logger.info(f"🚩 Work {work_id} disputed by {challenger_id}: {reason}")
        self._sync_dispute(dispute)
        self._sync_work_unit(unit)
        return dispute

    def get_dispute(self, dispute_id: str) -> Optional[DisputeRecord]:
        return self._disputes.get(dispute_id)

    def list_disputes(self, work_id: Optional[str] = None) -> List[DisputeRecord]:
        disputes = list(self._disputes.values())
        if work_id:
            disputes = [d for d in disputes if d.work_id == work_id]
        return sorted(disputes, key=lambda item: item.created_at, reverse=True)

    def list_open_disputes(self) -> List[DisputeRecord]:
        disputes = [
            d for d in self._disputes.values()
            if d.status in (DisputeStatus.OPEN, DisputeStatus.UNDER_REVIEW)
        ]
        return sorted(disputes, key=lambda item: item.created_at, reverse=True)

    def list_disputes_by_status(self, status: DisputeStatus) -> List[DisputeRecord]:
        disputes = [d for d in self._disputes.values() if d.status == status]
        return sorted(disputes, key=lambda item: item.created_at, reverse=True)

    def list_overdue_disputes(self, arbiter_id: Optional[str] = None) -> List[DisputeRecord]:
        disputes = [
            d for d in self._disputes.values()
            if self.is_dispute_overdue(d)
        ]
        if arbiter_id:
            disputes = [d for d in disputes if d.assigned_to == arbiter_id]
        return sorted(disputes, key=lambda item: item.created_at, reverse=True)

    def notify_overdue_disputes(self, arbiter_id: Optional[str] = None) -> List[DisputeRecord]:
        disputes = self.list_overdue_disputes(arbiter_id=arbiter_id)
        if not disputes:
            return []

        from .work_notifications import DisputeNotifier

        notifier = DisputeNotifier()
        registry = WorkerRegistry()
        for dispute in disputes:
            work_unit = self.get_work_unit(dispute.work_id) if dispute.work_id else None
            arbiter = registry.get_worker(dispute.assigned_to) if dispute.assigned_to else None
            notifier.notify_overdue(dispute, work_unit=work_unit, arbiter=arbiter)
        return disputes

    def is_dispute_overdue(self, dispute: DisputeRecord) -> bool:
        if not dispute.due_at:
            return False
        if dispute.status not in (DisputeStatus.OPEN, DisputeStatus.UNDER_REVIEW):
            return False
        try:
            due = datetime.fromisoformat(dispute.due_at)
        except Exception:
            return False
        return datetime.utcnow() > due

    def list_disputes_by_arbiter(
        self,
        arbiter_id: str,
        status: Optional[DisputeStatus] = None,
    ) -> List[DisputeRecord]:
        disputes = [d for d in self._disputes.values() if d.assigned_to == arbiter_id]
        if status:
            disputes = [d for d in disputes if d.status == status]
        return sorted(disputes, key=lambda item: item.created_at, reverse=True)

    def assign_dispute(
        self,
        dispute_id: str,
        arbiter_id: str,
        actor_id: Optional[str] = None,
        sla_seconds: Optional[int] = None,
    ) -> DisputeRecord:
        dispute = self._disputes.get(dispute_id)
        if not dispute:
            raise ValueError(f"Dispute {dispute_id} not found")

        if dispute.status in (
            DisputeStatus.RESOLVED_WORKER_WINS,
            DisputeStatus.RESOLVED_CHALLENGER_WINS,
        ):
            raise ValueError(f"Dispute already resolved/closed: {dispute.status}")

        if _require_arbiter_role():
            registry = WorkerRegistry()
            profile = registry.get_worker(arbiter_id)
            if not profile or "arbiter" not in [role.lower() for role in profile.roles]:
                raise ValueError("Arbiter role required for assignment")

        assigned_at = _now_iso()
        dispute.assigned_to = arbiter_id
        dispute.assigned_at = assigned_at
        if sla_seconds is None:
            sla_seconds = dispute.sla_seconds or _default_dispute_sla_seconds()
        dispute.sla_seconds = sla_seconds
        dispute.due_at = _add_seconds(assigned_at, sla_seconds) if sla_seconds else None
        dispute.status = DisputeStatus.UNDER_REVIEW
        if actor_id:
            dispute.metadata["assigned_by"] = actor_id
        dispute.updated_at = _now_iso()
        self._save_disputes()

        event = WorkEvent.create(
            work_id=dispute.work_id,
            event_type=WorkEventType.DISPUTE_ASSIGNED,
            actor_id=actor_id or arbiter_id,
            payload={
                "dispute_id": dispute.id,
                "arbiter_id": arbiter_id,
                "sla_seconds": sla_seconds,
                "due_at": dispute.due_at,
            },
        )
        self.event_log.append(event)
        logger.info(f"⚖️ Dispute {dispute_id} assigned to {arbiter_id}")
        self._sync_dispute(dispute)
        try:
            from .work_notifications import DisputeNotifier

            work_unit = self.get_work_unit(dispute.work_id) if dispute.work_id else None
            arbiter = None
            if _require_arbiter_role():
                arbiter = profile
            else:
                registry = WorkerRegistry()
                arbiter = registry.get_worker(arbiter_id)
            DisputeNotifier().notify_assignment(dispute, work_unit=work_unit, arbiter=arbiter)
        except Exception as exc:
            logger.debug("Dispute assignment notification failed: %s", exc)
        return dispute

    def resolve_dispute(
        self,
        dispute_id: str,
        resolution: DisputeStatus,
        resolver_id: str,
        notes: str,
        slash_amount: float = 0.0,
        slash_target: Optional[str] = None,
    ) -> DisputeRecord:
        """Resolve a dispute and update work status."""
        dispute = self._disputes.get(dispute_id)
        if not dispute:
            raise ValueError(f"Dispute {dispute_id} not found")
        
        if dispute.status != DisputeStatus.OPEN and dispute.status != DisputeStatus.UNDER_REVIEW:
            raise ValueError(f"Dispute already resolved/closed: {dispute.status}")
        if _require_assigned_resolver() and dispute.assigned_to and resolver_id != dispute.assigned_to:
            raise ValueError("Resolver is not assigned to this dispute")

        dispute.status = resolution
        dispute.resolver_id = resolver_id
        dispute.resolution_notes = notes
        dispute.resolved_at = _now_iso()
        dispute.slash_amount = slash_amount
        dispute.slash_target = slash_target
        dispute.updated_at = _now_iso()
        
        self._save_disputes()

        # Update work status based on resolution
        work_unit = self.get_work_unit(dispute.work_id)
        if work_unit:
            if slash_amount == 0.0 and work_unit.payout_amount:
                default_slash = work_unit.payout_amount * _default_slash_ratio()
                if resolution == DisputeStatus.RESOLVED_CHALLENGER_WINS and work_unit.worker_id:
                    dispute.slash_amount = default_slash
                    dispute.slash_target = dispute.slash_target or work_unit.worker_id
                elif resolution == DisputeStatus.RESOLVED_WORKER_WINS and dispute.challenger_id:
                    dispute.slash_amount = default_slash
                    dispute.slash_target = dispute.slash_target or dispute.challenger_id

            if resolution == DisputeStatus.RESOLVED_WORKER_WINS:
                work_unit.status = WorkUnitStatus.VERIFIED
                work_unit.updated_at = _now_iso()
                logger.info(f"✅ Dispute {dispute_id} resolved: Worker Wins. Work {work_unit.id} -> VERIFIED")
            elif resolution == DisputeStatus.RESOLVED_CHALLENGER_WINS:
                work_unit.status = WorkUnitStatus.REJECTED
                work_unit.updated_at = _now_iso()
                logger.info(f"❌ Dispute {dispute_id} resolved: Challenger Wins. Work {work_unit.id} -> REJECTED")
            
            self._save_work_units()

            # Record slashing event if applicable
            if dispute.slash_amount > 0 and dispute.slash_target:
                slashing_result = self._execute_slash(dispute, work_unit, resolver_id)
                event = WorkEvent.create(
                    work_id=work_unit.id,
                    event_type=WorkEventType.SLASHING_APPLIED,
                    actor_id=resolver_id,
                    payload={
                        "dispute_id": dispute_id,
                        "target": dispute.slash_target,
                        "amount": dispute.slash_amount,
                        "reason": notes
                    } | (slashing_result or {})
                )
                self._record_event(event, unit=work_unit)

            # Record resolution event
            event = WorkEvent.create(
                work_id=work_unit.id,
                event_type=WorkEventType.DISPUTE_RESOLVED,
                actor_id=resolver_id,
                payload={
                    "dispute_id": dispute_id,
                    "resolution": resolution.value,
                    "notes": notes
                }
            )
            self._record_event(event, unit=work_unit)

            self._save_disputes()
            if work_unit:
                self._sync_work_unit(work_unit)
            self._sync_dispute(dispute)

        return dispute

    def create_work_unit(self, unit: WorkUnit, actor_id: Optional[str] = None) -> WorkUnit:
        unit.updated_at = unit.created_at or _now_iso()
        self._work_units[unit.id] = unit
        self._save_work_units()
        self._record_event(WorkEvent.create(unit.id, WorkEventType.CREATED, actor_id=actor_id), unit=unit)
        self._sync_work_unit(unit)
        return unit

    def get_work_unit(self, work_id: str) -> Optional[WorkUnit]:
        return self._work_units.get(work_id)

    def list_work_units(self, status: Optional[WorkUnitStatus] = None) -> List[WorkUnit]:
        units = list(self._work_units.values())
        if status:
            units = [unit for unit in units if unit.status == status]
        return sorted(units, key=lambda unit: unit.created_at, reverse=True)

    def update_work_status(
        self,
        work_id: str,
        status: WorkUnitStatus,
        actor_id: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Optional[WorkUnit]:
        unit = self._work_units.get(work_id)
        if not unit:
            return None
        unit.status = status
        if status in (WorkUnitStatus.VERIFIED, WorkUnitStatus.REJECTED, WorkUnitStatus.PAID):
            unit.completed_at = unit.completed_at or _now_iso()
        unit.updated_at = _now_iso()
        self._work_units[work_id] = unit
        self._save_work_units()
        event_type = {
            WorkUnitStatus.CLAIMED: WorkEventType.CLAIMED,
            WorkUnitStatus.IN_PROGRESS: WorkEventType.STARTED,
            WorkUnitStatus.SUBMITTED: WorkEventType.PROOF_SUBMITTED,
            WorkUnitStatus.VERIFIED: WorkEventType.PROOF_VERIFIED,
            WorkUnitStatus.REJECTED: WorkEventType.PROOF_REJECTED,
            WorkUnitStatus.DISPUTED: WorkEventType.DISPUTED,
            WorkUnitStatus.PAID: WorkEventType.PAYOUT_SENT,
        }.get(status, WorkEventType.CREATED)
        self._record_event(WorkEvent.create(work_id, event_type, actor_id=actor_id, payload=payload), unit=unit)
        self._sync_work_unit(unit)
        return unit

    def update_work_metadata(self, work_id: str, metadata: Dict[str, Any]) -> Optional[WorkUnit]:
        unit = self._work_units.get(work_id)
        if not unit:
            return None
        unit.metadata = metadata
        unit.updated_at = _now_iso()
        self._work_units[unit.id] = unit
        self._save_work_units()
        self._sync_work_unit(unit)
        return unit

    def record_payout_requested(self, work_id: str, actor_id: Optional[str] = None, payload: Optional[Dict[str, Any]] = None) -> bool:
        unit = self._work_units.get(work_id)
        if not unit:
            return False
        event = WorkEvent.create(
            work_id=work_id,
            event_type=WorkEventType.PAYOUT_REQUESTED,
            actor_id=actor_id,
            payload=payload or {},
        )
        self._record_event(event, unit=unit)
        return True

    def claim_work(self, work_id: str, worker_id: str, actor_id: Optional[str] = None) -> Optional[WorkUnit]:
        unit = self._work_units.get(work_id)
        if not unit:
            return None
        if unit.status == WorkUnitStatus.CLAIMED and unit.worker_id == worker_id:
            return unit
        self._require_status(unit, [WorkUnitStatus.QUEUED], "claim")
        self._require_worker(unit, worker_id, "claim")
        unit.worker_id = worker_id
        unit.status = WorkUnitStatus.CLAIMED
        unit.updated_at = _now_iso()
        self._work_units[unit.id] = unit
        self._save_work_units()
        self._record_event(WorkEvent.create(
            work_id,
            WorkEventType.CLAIMED,
            actor_id=actor_id,
            payload={"worker_id": worker_id},
        ), unit=unit)
        self._sync_work_unit(unit)
        return unit

    def start_work(self, work_id: str, worker_id: str, actor_id: Optional[str] = None) -> Optional[WorkUnit]:
        unit = self._work_units.get(work_id)
        if not unit:
            return None
        if unit.status == WorkUnitStatus.IN_PROGRESS and unit.worker_id == worker_id:
            return unit
        self._require_status(unit, [WorkUnitStatus.QUEUED, WorkUnitStatus.CLAIMED], "start")
        self._require_worker(unit, worker_id, "start")
        unit.worker_id = worker_id
        unit.status = WorkUnitStatus.IN_PROGRESS
        unit.updated_at = _now_iso()
        self._work_units[unit.id] = unit
        self._save_work_units()
        self._record_event(WorkEvent.create(
            work_id,
            WorkEventType.STARTED,
            actor_id=actor_id,
            payload={"worker_id": worker_id},
        ), unit=unit)
        self._sync_work_unit(unit)
        return unit

    def assign_witness(self, work_id: str, observer_id: str, actor_id: Optional[str] = None) -> Optional[WorkUnit]:
        unit = self._work_units.get(work_id)
        if not unit:
            return None
        unit.observer_id = observer_id
        unit.updated_at = _now_iso()
        self._work_units[unit.id] = unit
        self._save_work_units()
        self._record_event(WorkEvent.create(
            work_id,
            WorkEventType.WITNESS_ASSIGNED,
            actor_id=actor_id,
            payload={"observer_id": observer_id, "action": "witness_assigned"},
        ), unit=unit)
        self._sync_work_unit(unit)
        return unit

    def dispute_work(
        self,
        work_id: str,
        actor_id: Optional[str] = None,
        reason: Optional[str] = None,
        evidence_refs: Optional[List[str]] = None,
    ) -> Optional[WorkUnit]:
        """Legacy helper: creates a dispute record and returns the work unit."""
        challenger_id = actor_id or "anonymous"
        reason_text = reason or "No reason provided"
        try:
            self.submit_dispute(
                work_id=work_id,
                challenger_id=challenger_id,
                reason=reason_text,
                evidence_refs=evidence_refs,
            )
        except ValueError:
            return None
        return self._work_units.get(work_id)

    def add_proof(self, proof: Proof, actor_id: Optional[str] = None) -> Proof:
        unit = self._work_units.get(proof.work_id)
        if not unit:
            raise ValueError(f"Work unit {proof.work_id} not found")
        self._require_status(unit, [WorkUnitStatus.CLAIMED, WorkUnitStatus.IN_PROGRESS, WorkUnitStatus.REJECTED], "submit proof")
        self._require_worker(unit, proof.worker_id, "submit proof")
        proof.updated_at = proof.submitted_at or _now_iso()
        self._proofs[proof.id] = proof
        self._save_proofs()
        was_rejected = unit.status == WorkUnitStatus.REJECTED
        self._record_event(WorkEvent.create(
            proof.work_id,
            WorkEventType.PROOF_SUBMITTED,
            actor_id=actor_id,
            payload={"proof_id": proof.id},
        ), unit=unit)
        unit.proof_id = proof.id
        if not unit.worker_id:
            unit.worker_id = proof.worker_id
        if proof.observer_id and not unit.observer_id:
            unit.observer_id = proof.observer_id
        unit.status = WorkUnitStatus.SUBMITTED
        if was_rejected:
            unit.completed_at = None
        unit.updated_at = _now_iso()
        self._work_units[unit.id] = unit
        self._save_work_units()
        self._sync_work_unit(unit)
        self._sync_proof(proof)
        return proof

    def get_proof(self, proof_id: str) -> Optional[Proof]:
        return self._proofs.get(proof_id)

    def update_proof_status(
        self,
        proof_id: str,
        status: ProofStatus,
        actor_id: Optional[str] = None,
        verification: Optional[Dict[str, Any]] = None,
    ) -> Optional[Proof]:
        proof = self._proofs.get(proof_id)
        if not proof:
            return None
        if proof.status in (ProofStatus.VERIFIED, ProofStatus.REJECTED):
            if proof.status != status:
                if _strict_work_transitions():
                    raise ValueError(f"Proof {proof_id} already {proof.status.value}")
                return proof
            return proof
        unit = self._work_units.get(proof.work_id)
        if _strict_work_transitions():
            if not unit:
                raise ValueError(f"Work unit {proof.work_id} not found")
            if unit.status != WorkUnitStatus.SUBMITTED:
                raise ValueError(f"verify proof requires status submitted (current: {unit.status.value})")
            if unit.proof_id and unit.proof_id != proof.id:
                raise ValueError("verify proof denied: proof does not match active proof")
        proof.status = status
        if verification:
            proof.verification = verification
        proof.updated_at = _now_iso()
        self._proofs[proof.id] = proof
        self._save_proofs()

        if status == ProofStatus.VERIFIED:
            self.update_work_status(
                proof.work_id,
                WorkUnitStatus.VERIFIED,
                actor_id=actor_id,
                payload={"proof_id": proof.id},
            )
        elif status == ProofStatus.REJECTED:
            self.update_work_status(
                proof.work_id,
                WorkUnitStatus.REJECTED,
                actor_id=actor_id,
                payload={"proof_id": proof.id},
            )

        self._sync_proof(proof)
        return proof

    def list_proofs(self, work_id: Optional[str] = None) -> List[Proof]:
        proofs = list(self._proofs.values())
        if work_id:
            proofs = [proof for proof in proofs if proof.work_id == work_id]
        return sorted(proofs, key=lambda item: item.submitted_at, reverse=True)

    def _sync_work_unit(self, unit: WorkUnit) -> None:
        if not self._supabase:
            return
        self._supabase.upsert_work_unit(unit)

    def _sync_proof(self, proof: Proof) -> None:
        if not self._supabase:
            return
        self._supabase.upsert_proof(proof)

    def _sync_dispute(self, dispute: DisputeRecord) -> None:
        if not self._supabase:
            return
        self._supabase.upsert_dispute(dispute)
