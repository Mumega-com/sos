"""
Work Settlement
===============
Links verified work units to wallet payouts.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple, Union
import os

import os; resolve_runtime_path = lambda x, **kw: os.path.expanduser(f"~/.mumega/{x}")
from .agent_trust import Permission
from .governance_gate import get_governance_gate
from .work_ledger import WorkLedger, WorkUnitStatus
from .worker_registry import WorkerRegistry
from .payment_status import PaymentStatus

logger = logging.getLogger(__name__)


@dataclass
class WorkPaymentRecord:
    id: str
    work_id: str
    recipient_role: str
    recipient_id: Optional[str]
    wallet_address: Optional[str]
    amount: float
    currency: str
    status: str
    timestamp: str
    tx_hash: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class WorkPaymentLedger:
    """Local ledger of work payouts."""

    def __init__(self, storage_path: Optional[Path] = None):
        self.storage_path = Path(storage_path) if storage_path else resolve_runtime_path("work", "work_payments.json")
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._payments: List[WorkPaymentRecord] = []
        self._load()

    def _load(self) -> None:
        if not self.storage_path.exists():
            return
        try:
            with self.storage_path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            for item in data.get("payments", []):
                self._payments.append(WorkPaymentRecord(**item))
        except Exception as exc:
            logger.warning("Failed to load work payments: %s", exc)

    def _save(self) -> None:
        payload = {"payments": [asdict(payment) for payment in self._payments]}
        with self.storage_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)

    def add(self, payment: WorkPaymentRecord) -> WorkPaymentRecord:
        self._payments.append(payment)
        self._save()
        return payment

    def list(self, work_id: Optional[str] = None) -> List[WorkPaymentRecord]:
        if not work_id:
            return list(self._payments)
        return [item for item in self._payments if item.work_id == work_id]


class WorkSettlement:
    """Settlement orchestrator for verified work units."""

    def __init__(self, ledger: Optional[WorkLedger] = None, registry: Optional[WorkerRegistry] = None):
        self.ledger = ledger or WorkLedger()
        self.registry = registry or WorkerRegistry()
        self.payments = WorkPaymentLedger()

    def _default_split(self) -> Dict[str, float]:
        env_split = os.getenv("MUMEGA_PAYOUT_SPLIT")
        if env_split:
            try:
                data = json.loads(env_split)
                if isinstance(data, dict):
                    return {str(k): float(v) for k, v in data.items()}
            except Exception:
                logger.warning("Invalid MUMEGA_PAYOUT_SPLIT; using defaults")
        return {
            "worker": 0.75,
            "observer": 0.1,
            "staker": 0.1,
            "energy_provider": 0.05,
        }

    def _normalize_split(self, split: Optional[Dict[str, float]]) -> Dict[str, float]:
        if not split:
            return {"worker": 1.0}
        total = sum(split.values())
        if total <= 0:
            return {"worker": 1.0}
        return {key: value / total for key, value in split.items()}

    def _emit_payment_receipt(
        self,
        unit,
        record: WorkPaymentRecord,
        actor_id: Optional[str],
    ) -> None:
        try:
            from mumega.core.receipts import build_receipt, get_receipt_store

            position = os.getenv("MUMEGA_POSITION_DEFAULT") or os.getenv("MUMEGA_POSITION")
            agent = {
                "name": os.getenv("MUMEGA_AGENT_NAME", "mumega"),
                "position": position,
            }
            job = {
                "type": "payout",
                "id": unit.id,
                "payment_id": record.id,
                "role": record.recipient_role,
            }
            intent = {
                "text": f"Payout work {unit.id}",
                "rider_id": actor_id,
            }
            execution = {
                "amount": record.amount,
                "currency": record.currency,
                "status": record.status,
                "recipient_id": record.recipient_id,
                "wallet_address": record.wallet_address,
            }
            governance = {
                "actor_id": actor_id,
            }
            settlement = {
                "tx_hash": record.tx_hash,
            }
            receipt = build_receipt(
                agent=agent,
                job=job,
                intent=intent,
                execution=execution,
                governance=governance,
                settlement=settlement,
            )
            get_receipt_store().append(receipt.to_dict())
        except Exception as exc:
            logger.debug("Receipt logging failed for payout %s: %s", record.id, exc)

    def _resolve_recipient(self, unit, role: str) -> Optional[Union[str, Dict[str, Any]]]:
        if role == "worker":
            return {"worker_id": unit.worker_id} if unit.worker_id else None
        if role == "observer":
            return {"worker_id": unit.observer_id} if unit.observer_id else None

        recipients = unit.metadata.get("payout_recipients", {}) if unit.metadata else {}
        if isinstance(recipients, dict) and role in recipients:
            return recipients[role]

        if role == "staker":
            if unit.metadata.get("staker_id"):
                return {"worker_id": unit.metadata.get("staker_id")}
            if unit.metadata.get("staker_wallet"):
                return {"wallet_address": unit.metadata.get("staker_wallet")}
        if role == "energy_provider":
            if unit.metadata.get("energy_provider_id"):
                return {"worker_id": unit.metadata.get("energy_provider_id")}
            if unit.metadata.get("energy_provider_wallet"):
                return {"wallet_address": unit.metadata.get("energy_provider_wallet")}

        return None

    def _resolve_recipients(self, unit, roles: List[str]) -> Dict[str, Optional[Union[str, Dict[str, Any]]]]:
        return {role: self._resolve_recipient(unit, role) for role in roles}

    def _prepare_split(self, unit, split_override: Optional[Dict[str, float]] = None) -> Tuple[Dict[str, float], Dict[str, Optional[Union[str, Dict[str, Any]]]]]:
        base_split = split_override or unit.metadata.get("payout_split") if unit.metadata else None
        if not base_split:
            base_split = self._default_split()

        roles = list(base_split.keys())
        recipients = self._resolve_recipients(unit, roles)

        filtered = {}
        for role, ratio in base_split.items():
            if ratio <= 0:
                continue
            if recipients.get(role) is None:
                continue
            filtered[role] = ratio

        if not filtered:
            filtered = {"worker": 1.0}
            recipients = {"worker": {"worker_id": unit.worker_id} if unit.worker_id else None}

        return self._normalize_split(filtered), recipients

    def _summarize_payout_status(self, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not results:
            return {"status": "skipped", "pending_roles": [], "failed_roles": []}

        pending_roles = [r["recipient_role"] for r in results if r["status"] == PaymentStatus.PENDING.value]
        failed_roles = [
            r["recipient_role"]
            for r in results
            if r["status"] in (PaymentStatus.FAILED.value, PaymentStatus.BLOCKED.value)
        ]
        statuses = {r["status"] for r in results}

        if pending_roles:
            status = "pending_approval"
        elif failed_roles:
            status = "failed"
        elif statuses == {PaymentStatus.SUCCESS.value}:
            status = "paid"
        elif statuses == {PaymentStatus.SIMULATED.value}:
            status = "simulated"
        elif statuses.issubset({PaymentStatus.SUCCESS.value, PaymentStatus.SIMULATED.value}):
            status = "partial"
        else:
            status = "partial"

        return {
            "status": status,
            "pending_roles": pending_roles,
            "failed_roles": failed_roles,
        }

    def calculate_value_score(self, unit) -> Dict[str, Any]:
        """
        Calculate value score based on RU and quality factors.
        
        Formula: value_score = clamp(RU_norm, 0.1, 2.0) × proof_score × witness_score × timeliness_score
        
        Returns dict with breakdown for transparency.
        """
        metadata = unit.metadata or {}
        
        # RU normalization: raw RU divided by baseline (default 100)
        ru_raw = metadata.get("ru_score", 100.0)
        ru_baseline = metadata.get("ru_baseline", 100.0)
        ru_norm = max(0.1, min(2.0, ru_raw / ru_baseline))  # Clamp to [0.1, 2.0]
        
        # Proof quality score (from verification)
        proof_score = metadata.get("proof_score", 1.0)
        proof_score = max(0.0, min(1.0, proof_score))  # Clamp to [0, 1]
        
        # Witness attestation bonus
        witness_score = 1.0 if unit.observer_id else 0.9
        if metadata.get("witness_verified"):
            witness_score = 1.1  # Bonus for explicit witness verification
        
        # Timeliness score (based on SLA)
        timeliness_score = 1.0
        sla_seconds = metadata.get("sla_seconds")
        if sla_seconds and unit.completed_at and unit.created_at:
            try:
                from datetime import datetime
                created = datetime.fromisoformat(unit.created_at)
                completed = datetime.fromisoformat(unit.completed_at)
                actual_seconds = (completed - created).total_seconds()
                if actual_seconds <= sla_seconds:
                    timeliness_score = 1.05  # Early bonus
                elif actual_seconds > sla_seconds * 2:
                    timeliness_score = 0.8  # Penalty for very late
            except Exception:
                pass
        
        # Final value score
        value_score = ru_norm * proof_score * witness_score * timeliness_score
        
        return {
            "value_score": round(value_score, 3),
            "ru_norm": round(ru_norm, 3),
            "proof_score": round(proof_score, 3),
            "witness_score": round(witness_score, 3),
            "timeliness_score": round(timeliness_score, 3),
        }

    async def settle_work(
        self,
        work_id: str,
        actor_id: Optional[str] = None,
        execute_onchain: bool = False,
        force: bool = False,
        split: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        unit = self.ledger.get_work_unit(work_id)
        if not unit:
            return {"error": "work_unit_not_found"}

        gate = get_governance_gate()
        if not gate.check(actor_id, Permission.EXECUTE_PAYOUT):
            return {
                "error": "permission_denied",
                "detail": gate.denial_reason(actor_id, Permission.EXECUTE_PAYOUT),
            }

        if unit.status == WorkUnitStatus.DISPUTED:
            return {"error": "work_unit_disputed", "status": unit.status.value}
        if unit.status != WorkUnitStatus.VERIFIED:
            return {"error": "work_unit_not_verified", "status": unit.status.value}

        # Check dispute window
        if unit.dispute_window_seconds and not force:
            try:
                completed_at = datetime.fromisoformat(unit.completed_at) if unit.completed_at else datetime.utcnow()
                dispute_deadline = completed_at + timedelta(seconds=unit.dispute_window_seconds)
                if datetime.utcnow() < dispute_deadline:
                    return {
                        "error": "dispute_window_open",
                        "deadline": dispute_deadline.isoformat(),
                        "remaining_seconds": (dispute_deadline - datetime.utcnow()).total_seconds()
                    }
            except Exception as e:
                logger.warning(f"Failed to check dispute window: {e}")

        if unit.payout_amount is None or unit.payout_amount <= 0:
            return {"error": "missing_payout_amount"}

        currency = unit.payout_currency or "MIND"
        payout_split, recipients = self._prepare_split(unit, split_override=split)

        payout_requested_at = datetime.utcnow().isoformat()
        self.ledger.record_payout_requested(
            unit.id,
            actor_id=actor_id,
            payload={
                "requested_at": payout_requested_at,
                "currency": currency,
                "execute_onchain": execute_onchain,
                "force": force,
            },
        )
        
        # Calculate value score from RU and quality factors
        value_breakdown = self.calculate_value_score(unit)
        value_multiplier = value_breakdown["value_score"]
        logger.info(f"📊 Value score for {work_id}: {value_multiplier:.3f} (RU={value_breakdown['ru_norm']:.2f}, proof={value_breakdown['proof_score']:.2f})")

        treasury = None
        if execute_onchain:
            from mumega.core.sovereign.treasury import TreasuryWallet
            treasury = TreasuryWallet()

        # Idempotency: Load existing payments
        existing_payments = self.payments.list(work_id)
        paid_roles = {
            p.recipient_role: p for p in existing_payments 
            if p.status in [PaymentStatus.SUCCESS.value, PaymentStatus.PENDING.value]
        }

        results = []
        for role, ratio in payout_split.items():
            # Check for existing payment
            if role in paid_roles:
                logger.info(f"Skipping {role}: already {paid_roles[role].status}")
                results.append(asdict(paid_roles[role]))
                continue
            recipient_info = recipients.get(role)
            recipient_id = None
            wallet = None
            profile = None

            if isinstance(recipient_info, dict):
                recipient_id = recipient_info.get("worker_id") or recipient_info.get("recipient_id")
                wallet = recipient_info.get("wallet_address")
            elif isinstance(recipient_info, str):
                recipient_id = recipient_info

            if recipient_id and not wallet:
                profile = self.registry.get_worker(recipient_id)
                wallet = profile.wallet_address if profile else None
            
            # Apply value score to base amount
            base_amount = unit.payout_amount * ratio * value_multiplier
            
            # Apply reputation multiplier for workers
            reputation_multiplier = 1.0
            if role == "worker" and profile:
                rep_score = profile.reputation_score
                tier = profile._calculate_tier()
                # Multiplier ranges from 0.5 (NOVICE) to 1.2 (MASTER)
                tier_multipliers = {
                    "MASTER": 1.2,
                    "EXPERT": 1.1,
                    "JOURNEYMAN": 1.0,
                    "APPRENTICE": 0.8,
                    "NOVICE": 0.5,
                }
                reputation_multiplier = tier_multipliers.get(tier, 1.0)
                logger.info(f"🎯 Worker {recipient_id} tier={tier} rep={rep_score:.2f} multiplier={reputation_multiplier}x")
            
            amount = base_amount * reputation_multiplier

            payment_id = f"{unit.id}:{role}:{len(results)}"
            status = PaymentStatus.PENDING.value
            tx_hash = None
            metadata: Dict[str, Any] = {}
            if execute_onchain:
                if not wallet:
                    status = PaymentStatus.FAILED.value
                else:
                    try:
                        payout = await treasury.pay_bounty_with_witness(
                            payment_id,
                            wallet,
                            amount,
                            reason=f"Work {unit.id} ({role})",
                            force=force,
                        )
                        if isinstance(payout, dict):
                            payout_status = payout.get("status")
                            if payout_status == "pending_approval":
                                status = PaymentStatus.PENDING.value
                                metadata["approval"] = payout.get("approval")
                            elif payout_status == "blocked":
                                status = PaymentStatus.BLOCKED.value
                                metadata["block_reason"] = payout.get("reason")
                            elif payout_status == "failed":
                                status = PaymentStatus.FAILED.value
                                metadata["error"] = payout.get("error")
                            elif payout_status in ["paid", "success"]:
                                status = PaymentStatus.SUCCESS.value
                                tx_hash = payout.get("tx_signature")
                            else:
                                logger.warning(f"Unknown payout status: {payout_status}")
                                status = PaymentStatus.FAILED.value
                        else:
                            tx_hash = payout
                            # TD-017: Check for safety block return value
                            if tx_hash == "blocked_by_safety_check":
                                status = PaymentStatus.BLOCKED.value
                                logger.warning(f"⚠️ Payment to {role} ({wallet[:8]}...) blocked by safety check")
                            else:
                                status = PaymentStatus.SUCCESS.value
                    except Exception as exc:
                        logger.error("Payout failed for %s: %s", role, exc)
                        status = PaymentStatus.FAILED.value
            else:
                status = PaymentStatus.SIMULATED.value

            record = WorkPaymentRecord(
                id=payment_id,
                work_id=unit.id,
                recipient_role=role,
                recipient_id=recipient_id,
                wallet_address=wallet,
                amount=amount,
                currency=currency,
                status=status,
                timestamp=datetime.utcnow().isoformat(),
                tx_hash=tx_hash,
                metadata=metadata,
            )
            self.payments.add(record)
            self._emit_payment_receipt(unit, record, actor_id)
            results.append(asdict(record))

            if status in (PaymentStatus.SUCCESS.value, PaymentStatus.SIMULATED.value) and recipient_id:
                self.registry.record_event(recipient_id, "paid")

        if execute_onchain and all(item["status"] == PaymentStatus.SUCCESS.value for item in results):
            self.ledger.update_work_status(unit.id, WorkUnitStatus.PAID, actor_id=actor_id)

        payout_summary = self._summarize_payout_status(results)
        payout_metadata = dict(unit.metadata or {})
        payout_metadata["payout_status"] = payout_summary["status"]
        payout_metadata["payout_mode"] = "onchain" if execute_onchain else "simulated"
        payout_metadata["payout_requested_at"] = payout_requested_at
        payout_metadata["payout_last_attempt"] = datetime.utcnow().isoformat()
        if payout_summary["status"] in ("paid", "simulated"):
            payout_metadata["payout_completed_at"] = payout_metadata["payout_last_attempt"]
        if payout_summary["pending_roles"]:
            payout_metadata["payout_pending_roles"] = payout_summary["pending_roles"]
        else:
            payout_metadata.pop("payout_pending_roles", None)
        if payout_summary["failed_roles"]:
            payout_metadata["payout_failed_roles"] = payout_summary["failed_roles"]
        else:
            payout_metadata.pop("payout_failed_roles", None)
        self.ledger.update_work_metadata(unit.id, payout_metadata)

        return {"work_id": unit.id, "currency": currency, "payments": results}
