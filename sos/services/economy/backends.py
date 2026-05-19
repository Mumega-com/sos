import sqlite3
from typing import Dict, Optional
from dataclasses import dataclass
from pathlib import Path
import time

from sos.observability.logging import get_logger

log = get_logger("economy_backends")

@dataclass
class TransactionRecord:
    id: str
    user_id: str
    amount: float
    type: str # 'credit', 'debit'
    timestamp: float
    reason: str

class WalletBackend:
    """Abstract base for wallet storage."""
    def get_balance(self, user_id: str) -> float: ...
    def update_balance(self, user_id: str, amount: float) -> float: ...
    def log_transaction(self, record: TransactionRecord) -> None: ...

class SQLiteWalletBackend(WalletBackend):
    """
    SQLite implementation for persistent wallet storage.
    """
    def __init__(self, db_path: str = "data/economy.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            # Balances Table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS balances (
                    user_id TEXT PRIMARY KEY,
                    balance REAL DEFAULT 0.0,
                    updated_at REAL
                )
            """)
            # Ledger Table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ledger (
                    id TEXT PRIMARY KEY,
                    user_id TEXT,
                    amount REAL,
                    type TEXT,
                    timestamp REAL,
                    reason TEXT
                )
            """)
            conn.commit()

    def get_balance(self, user_id: str) -> float:
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("SELECT balance FROM balances WHERE user_id = ?", (user_id,))
                row = cursor.fetchone()
                return row[0] if row else 0.0
        except Exception as e:
            log.error("SQLite get_balance failed", error=str(e))
            return 0.0

    def update_balance(self, user_id: str, amount: float) -> float:
        """
        Updates balance atomically using SQLite transaction.
        amount can be positive (credit) or negative (debit).
        Returns new balance.
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                # Upsert balance
                cursor = conn.execute(
                    """
                    INSERT INTO balances (user_id, balance, updated_at) 
                    VALUES (?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                    balance = balance + ?,
                    updated_at = ?
                    RETURNING balance
                    """,
                    (user_id, amount, time.time(), amount, time.time())
                )
                new_balance = cursor.fetchone()[0]
                return new_balance
        except Exception as e:
            log.error("SQLite update_balance failed", error=str(e))
            raise

    def log_transaction(self, record: TransactionRecord) -> None:
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT INTO ledger (id, user_id, amount, type, timestamp, reason) VALUES (?, ?, ?, ?, ?, ?)",
                    (record.id, record.user_id, record.amount, record.type, record.timestamp, record.reason)
                )
        except Exception as e:
            log.error("SQLite log_transaction failed", error=str(e))
