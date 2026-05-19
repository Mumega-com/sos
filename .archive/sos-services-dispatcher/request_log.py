"""Async request log — SQLite at ~/.sos/data/dispatcher.db."""
from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


LOG_DB_PATH = Path.home() / ".sos" / "data" / "dispatcher.db"
_init_lock = asyncio.Lock()
_initialized = False


def _ensure_schema() -> None:
    LOG_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(LOG_DB_PATH))
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS requests (
                ts TEXT NOT NULL,
                tenant_id TEXT,
                agent TEXT NOT NULL,
                scope TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                method TEXT NOT NULL,
                status INTEGER NOT NULL,
                latency_ms INTEGER NOT NULL,
                bytes_out INTEGER DEFAULT 0,
                error_code TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_requests_ts ON requests(ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_requests_tenant ON requests(tenant_id, ts)")
        conn.commit()
    finally:
        conn.close()


async def log_request(
    *,
    tenant_id: Optional[str],
    agent: str,
    scope: str,
    endpoint: str,
    method: str,
    status: int,
    latency_ms: int,
    bytes_out: int = 0,
    error_code: Optional[str] = None,
) -> None:
    """Fire-and-forget request log write. Safe from any async context."""
    global _initialized
    if not _initialized:
        async with _init_lock:
            if not _initialized:
                await asyncio.to_thread(_ensure_schema)
                _initialized = True

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _write() -> None:
        try:
            conn = sqlite3.connect(str(LOG_DB_PATH), timeout=2.0)
            conn.execute(
                "INSERT INTO requests (ts, tenant_id, agent, scope, endpoint, method, status, latency_ms, bytes_out, error_code) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ts, tenant_id, agent, scope, endpoint, method, status, latency_ms, bytes_out, error_code),
            )
            conn.commit()
            conn.close()
        except Exception:
            # Logging must not break the hot path. Swallow failures.
            pass

    await asyncio.to_thread(_write)
