"""
Supabase Work Sync
==================
Optional Supabase-backed persistence for work units, proofs, and disputes.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, TYPE_CHECKING

#from mumega.config.settings import settings  # TODO: wire to SOS config
get_env_bool = lambda k, d=False: os.environ.get(k, str(d)).lower() in ("1","true","yes")

if TYPE_CHECKING:
    from .work_ledger import WorkUnit, Proof, DisputeRecord

logger = logging.getLogger(__name__)


class SupabaseWorkSync:
    """Best-effort Supabase sync for work records."""

    def __init__(self) -> None:
        # TD-023: Use centralized boolean parsing
        self.enabled = get_env_bool("MUMEGA_WORK_SUPABASE_SYNC", default=False)
        self.client = None
        self.schema = os.getenv("MUMEGA_WORK_SUPABASE_SCHEMA", "").strip() or None
        self.table_units = os.getenv("MUMEGA_WORK_SUPABASE_TABLE_UNITS", "work_units")
        self.table_proofs = os.getenv("MUMEGA_WORK_SUPABASE_TABLE_PROOFS", "work_proofs")
        self.table_disputes = os.getenv("MUMEGA_WORK_SUPABASE_TABLE_DISPUTES", "work_disputes")

        if not self.enabled:
            return

        url = settings.supabase.url
        key = settings.supabase.service_role_key or settings.supabase.key
        if not url or not key:
            logger.warning("Supabase work sync enabled but SUPABASE_URL/KEY not set")
            self.enabled = False
            return

        try:
            from supabase import create_client
        except Exception:
            logger.warning("Supabase library not installed. Run: pip install supabase")
            self.enabled = False
            return

        try:
            self.client = create_client(url, key)
        except Exception as exc:
            logger.warning("Supabase work sync init failed: %s", exc)
            self.enabled = False

    def fetch_units(self) -> List[Dict[str, Any]]:
        return self._fetch_all(self.table_units)

    def fetch_proofs(self) -> List[Dict[str, Any]]:
        return self._fetch_all(self.table_proofs)

    def fetch_disputes(self) -> List[Dict[str, Any]]:
        return self._fetch_all(self.table_disputes)

    def upsert_work_unit(self, unit: "WorkUnit") -> None:
        self._upsert(self.table_units, unit.to_dict())

    def upsert_proof(self, proof: "Proof") -> None:
        self._upsert(self.table_proofs, proof.to_dict())

    def upsert_dispute(self, dispute: "DisputeRecord") -> None:
        self._upsert(self.table_disputes, dispute.to_dict())

    def _table(self, name: str):
        if not self.client:
            return None
        if self.schema:
            return self.client.schema(self.schema).table(name)
        return self.client.table(name)

    def _fetch_all(self, name: str) -> List[Dict[str, Any]]:
        if not self.enabled:
            return []
        table = self._table(name)
        if not table:
            return []
        try:
            result = table.select("*").execute()
            return result.data or []
        except Exception as exc:
            logger.warning("Supabase fetch failed (%s): %s", name, exc)
            return []

    def _upsert(self, name: str, payload: Dict[str, Any]) -> None:
        if not self.enabled:
            return
        table = self._table(name)
        if not table:
            return
        try:
            table.upsert(payload, on_conflict="id").execute()
        except Exception as exc:
            logger.warning("Supabase upsert failed (%s): %s", name, exc)
