"""
Work Slashing Ledger
====================
Records slashing actions executed during dispute resolution.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List
from uuid import uuid4

import os; resolve_runtime_path = lambda x, **kw: os.path.expanduser(f"~/.mumega/{x}")

logger = logging.getLogger(__name__)


@dataclass
class WorkSlashingRecord:
    id: str
    work_id: str
    dispute_id: str
    target_id: str
    wallet_address: Optional[str]
    amount: float
    currency: str
    status: str
    timestamp: str
    tx_hash: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class WorkSlashingLedger:
    """Local ledger of slashing actions."""

    def __init__(self, storage_path: Optional[Path] = None):
        self.storage_path = Path(storage_path) if storage_path else resolve_runtime_path("work", "work_slashes.json")
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._records: List[WorkSlashingRecord] = []
        self._load()

    def _load(self) -> None:
        if not self.storage_path.exists():
            return
        try:
            with self.storage_path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            for item in data.get("slashes", []):
                self._records.append(WorkSlashingRecord(**item))
        except Exception as exc:
            logger.warning("Failed to load work slashes: %s", exc)

    def _save(self) -> None:
        payload = {"slashes": [asdict(record) for record in self._records]}
        with self.storage_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)

    def add(self, record: WorkSlashingRecord) -> WorkSlashingRecord:
        self._records.append(record)
        self._save()
        return record

    def list(
        self,
        work_id: Optional[str] = None,
        dispute_id: Optional[str] = None,
        target_id: Optional[str] = None,
    ) -> List[WorkSlashingRecord]:
        records = list(self._records)
        if work_id:
            records = [item for item in records if item.work_id == work_id]
        if dispute_id:
            records = [item for item in records if item.dispute_id == dispute_id]
        if target_id:
            records = [item for item in records if item.target_id == target_id]
        return records


def build_slashing_record(
    work_id: str,
    dispute_id: str,
    target_id: str,
    wallet_address: Optional[str],
    amount: float,
    currency: str,
    status: str,
    metadata: Optional[Dict[str, Any]] = None,
    tx_hash: Optional[str] = None,
) -> WorkSlashingRecord:
    return WorkSlashingRecord(
        id=str(uuid4()),
        work_id=work_id,
        dispute_id=dispute_id,
        target_id=target_id,
        wallet_address=wallet_address,
        amount=amount,
        currency=currency,
        status=status,
        timestamp=datetime.utcnow().isoformat(),
        tx_hash=tx_hash,
        metadata=metadata or {},
    )
