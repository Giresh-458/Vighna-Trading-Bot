import sqlite3
import logging
from typing import Dict, Any, List
from threading import Lock

logger = logging.getLogger(__name__)

class Database:
    def __init__(self, db_path="paper_trading.db"):
        self.db_path = db_path
        self._lock = Lock()
        self._init_db()

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path, timeout=15.0)
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 10000;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        return conn

    def _init_db(self):
        with self._lock:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS live_ticks (
                    token TEXT PRIMARY KEY,
                    symbol TEXT,
                    ltp REAL,
                    updated_at TEXT
                )
                """)
                # Signals table
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS signals (
                    id TEXT PRIMARY KEY,
                    telegram_message_id INTEGER,
                    channel_id INTEGER,
                    received_at TEXT,
                    underlying TEXT,
                    strike REAL,
                    option_type TEXT,
                    expiry TEXT,
                    entry_low REAL,
                    entry_high REAL,
                    target_1 REAL,
                    target_2 REAL,
                    target_3 REAL,
                    stop_loss REAL,
                    status TEXT,
                    created_at TEXT
                )
                """)
                # Positions table
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS positions (
                    id TEXT PRIMARY KEY,
                    signal_id TEXT,
                    token TEXT,
                    symbol TEXT,
                    entry_price REAL,
                    quantity INTEGER,
                    entry_time TEXT,
                    target_1 REAL,
                    stop_loss REAL,
                    status TEXT,
                    exit_price REAL,
                    exit_time TEXT,
                    exit_reason TEXT
                )
                """)
                # Trades table for P&L tracking
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    position_id TEXT,
                    entry_price REAL,
                    exit_price REAL,
                    quantity INTEGER,
                    gross_pnl REAL,
                    brokerage REAL,
                    taxes REAL,
                    charges REAL,
                    net_pnl REAL,
                    timestamp TEXT
                )
                """)
                # System state (e.g. daily capital)
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS system_state (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
                """)
                # Commands table for inter-process communication
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS commands (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    command TEXT,
                    payload TEXT,
                    status TEXT,
                    created_at TEXT
                )
                """)
                # Subscribers table for paid alerts
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS subscribers (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    expiry_date TEXT,
                    created_at TEXT
                )
                """)
                
                # Migration to add trade_type if it doesn't exist
                try:
                    cursor.execute("ALTER TABLE signals ADD COLUMN trade_type TEXT DEFAULT 'LONG'")
                except sqlite3.OperationalError:
                    pass
                
                try:
                    cursor.execute("ALTER TABLE positions ADD COLUMN trade_type TEXT DEFAULT 'LONG'")
                except sqlite3.OperationalError:
                    pass
                
                try:
                    cursor.execute("ALTER TABLE subscribers ADD COLUMN allowed_segments TEXT DEFAULT 'equity,commodity_futures,commodity_options,hero_zero'")
                except sqlite3.OperationalError:
                    pass
                    
                try:
                    cursor.execute("ALTER TABLE signals ADD COLUMN segment TEXT DEFAULT 'equity'")
                except sqlite3.OperationalError:
                    pass
                
                try:
                    cursor.execute("ALTER TABLE subscribers ADD COLUMN skip_date TEXT")
                except sqlite3.OperationalError:
                    pass
                
                conn.commit()

    def execute(self, query: str, params: tuple = ()) -> int:
        with self._lock:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute(query, params)
                conn.commit()
                return cursor.lastrowid

    def fetch_all(self, query: str, params: tuple = ()) -> List[dict]:
        with self._lock:
            with self._get_conn() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(query, params)
                return [dict(row) for row in cursor.fetchall()]

    def fetch_one(self, query: str, params: tuple = ()) -> Dict[str, Any]:
        with self._lock:
            with self._get_conn() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(query, params)
                row = cursor.fetchone()
                return dict(row) if row else None

    def update_live_tick(self, token: str, symbol: str, ltp: float):
        import time
        from datetime import datetime, timezone
        now_str = datetime.now(timezone.utc).isoformat()
        self.execute("INSERT OR REPLACE INTO live_ticks (token, symbol, ltp, updated_at) VALUES (?, ?, ?, ?)",
                     (str(token), symbol, float(ltp), now_str))

    def get_all_live_ticks(self) -> Dict[str, float]:
        rows = self.fetch_all("SELECT token, ltp FROM live_ticks")
        return {r["token"]: r["ltp"] for r in rows}
