import sqlite3
import json
from typing import Any, Dict, List, Optional
from dataclasses import dataclass
import time
from pathlib import Path

from sos.observability.logging import get_logger

log = get_logger("memory_backends")

@dataclass
class MemoryMetadata:
    id: str
    timestamp: float
    source: str
    type: str
    tags: List[str]
    raw_metadata: Dict[str, Any]

class MetadataStore:
    """Abstract base for metadata storage."""
    def add(self, item_id: str, metadata: Dict[str, Any]) -> None: ...
    def get(self, item_id: str) -> Optional[MemoryMetadata]: ...
    def list(self, limit: int = 10) -> List[MemoryMetadata]: ...

class SQLiteMetadataStore(MetadataStore):
    """
    SQLite implementation for persistent metadata storage.
    Aligned with Task kasra-20260110-002.
    """
    def __init__(self, db_path: str = "data/memory.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memory_metadata (
                    id TEXT PRIMARY KEY,
                    timestamp REAL,
                    source TEXT,
                    type TEXT,
                    tags TEXT,
                    raw_json TEXT
                )
            """)
            conn.commit()

    def add(self, item_id: str, metadata: Dict[str, Any]) -> None:
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO memory_metadata (id, timestamp, source, type, tags, raw_json) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        item_id,
                        time.time(),
                        metadata.get("source", "unknown"),
                        metadata.get("type", "generic"),
                        json.dumps(metadata.get("tags", [])),
                        json.dumps(metadata)
                    )
                )
        except Exception as e:
            log.error("SQLite add failed", error=str(e))
            raise

    def get(self, item_id: str) -> Optional[MemoryMetadata]:
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("SELECT * FROM memory_metadata WHERE id = ?", (item_id,))
                row = cursor.fetchone()
                if row:
                    return MemoryMetadata(
                        id=row[0],
                        timestamp=row[1],
                        source=row[2],
                        type=row[3],
                        tags=json.loads(row[4]),
                        raw_metadata=json.loads(row[5])
                    )
        except Exception as e:
            log.error("SQLite get failed", error=str(e))
        return None

    def list(self, limit: int = 10) -> List[MemoryMetadata]:
        results = []
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("SELECT * FROM memory_metadata ORDER BY timestamp DESC LIMIT ?", (limit,))
                for row in cursor:
                    results.append(MemoryMetadata(
                        id=row[0],
                        timestamp=row[1],
                        source=row[2],
                        type=row[3],
                        tags=json.loads(row[4]),
                        raw_metadata=json.loads(row[5])
                    ))
        except Exception as e:
            log.error("SQLite list failed", error=str(e))
        return results
