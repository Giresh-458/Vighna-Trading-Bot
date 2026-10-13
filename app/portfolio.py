import logging
import threading
from contextlib import contextmanager
from typing import Iterator, Tuple

from app.database import Database
from app.config import INITIAL_CAPITAL

logger = logging.getLogger(__name__)

SEGMENTS = ("equity", "commodity_futures", "commodity_options", "hero_zero")
TOTAL_CAPITAL_KEY = "available_margin_total"


def _legacy_position_margin(entry_price: float, quantity: int, segment: str, symbol: str) -> float:
    """Best-effort margin reconstruction for positions opened before margin_used existed."""
    is_futures = segment == "commodity_futures" or "FUT" in str(symbol).upper()
    multiplier = 0.10 if is_futures else 1.0
    return float(entry_price) * int(quantity) * multiplier


class Portfolio:
    """One shared paper-trading cash pool for every supported segment.

    The legacy per-segment keys are deliberately not summed: older versions could
    initialize each one to INITIAL_CAPITAL, accidentally multiplying the budget.
    A one-time migration reconstructs available cash from the configured starting
    capital, realized ledger P&L, and margin reserved by currently open positions.
    """

    def __init__(self, db: Database):
        self.db = db
        self._lock = threading.RLock()
        self.available_margin = self._load_capital()
        # Compatibility snapshot only. These entries all refer to the same shared
        # balance; they are not separate allocations and must never be summed.
        self.margins = {segment: self.available_margin for segment in SEGMENTS}

    def _load_capital(self) -> float:
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT value FROM system_state WHERE key = ?",
                (TOTAL_CAPITAL_KEY,),
            ).fetchone()
            if row:
                return float(row["value"])

            realized = conn.execute(
                "SELECT COALESCE(SUM(net_pnl), 0) AS net_pnl FROM trades"
            ).fetchone()["net_pnl"]
            open_positions = conn.execute("""
                SELECT p.entry_price, p.quantity, p.symbol, p.margin_used,
                       COALESCE(s.segment, 'equity') AS segment
                FROM positions p
                LEFT JOIN signals s ON s.id = p.signal_id
                WHERE p.status = 'OPEN'
            """).fetchall()

            blocked = 0.0
            for pos in open_positions:
                stored_margin = pos["margin_used"]
                if stored_margin is not None:
                    blocked += max(0.0, float(stored_margin))
                else:
                    blocked += _legacy_position_margin(
                        pos["entry_price"], pos["quantity"], pos["segment"], pos["symbol"]
                    )

            available = float(INITIAL_CAPITAL) + float(realized or 0.0) - blocked
            conn.execute(
                "INSERT INTO system_state (key, value) VALUES (?, ?)",
                (TOTAL_CAPITAL_KEY, str(available)),
            )
            logger.warning(
                "Migrated legacy segment capital to one shared pool: initial=%.2f, "
                "realized_pnl=%.2f, open_margin=%.2f, available=%.2f. "
                "Legacy per-segment balances were not added together.",
                float(INITIAL_CAPITAL), float(realized or 0.0), blocked, available,
            )
            return available

    @contextmanager
    def accounting_transaction(self) -> Iterator[Tuple[object, float]]:
        """Serialize capital writes and refresh in-memory balance after DB commit."""
        with self._lock:
            committed_balance = None
            with self.db.transaction() as conn:
                row = conn.execute(
                    "SELECT value FROM system_state WHERE key = ?",
                    (TOTAL_CAPITAL_KEY,),
                ).fetchone()
                if row is None:
                    raise RuntimeError("Shared paper capital has not been initialized")
                starting_balance = float(row["value"])
                yield conn, starting_balance
                row = conn.execute(
                    "SELECT value FROM system_state WHERE key = ?",
                    (TOTAL_CAPITAL_KEY,),
                ).fetchone()
                if row is None:
                    raise RuntimeError("Shared paper capital disappeared during transaction")
                committed_balance = float(row["value"])
            # This code runs only after the SQLite commit succeeded.
            self.available_margin = committed_balance
            self.margins = {segment: committed_balance for segment in SEGMENTS}

    @staticmethod
    def write_available_margin(conn, amount: float) -> None:
        """Write the shared balance using the caller's active transaction."""
        conn.execute(
            """INSERT INTO system_state (key, value) VALUES (?, ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (TOTAL_CAPITAL_KEY, str(float(amount))),
        )

    def has_sufficient_margin(self, segment: str, required_margin: float) -> bool:
        if required_margin < 0:
            return False
        with self._lock:
            return self.available_margin >= float(required_margin)

    def block_margin(self, segment: str, amount: float):
        amount = float(amount)
        if amount < 0:
            raise ValueError("Margin amount cannot be negative")
        with self.accounting_transaction() as (conn, available):
            if available < amount:
                raise ValueError(
                    f"Insufficient shared paper capital for {segment}. "
                    f"Required: {amount}, Available: {available}"
                )
            self.write_available_margin(conn, available - amount)
        logger.info(
            "Blocked %s margin: Rs %.2f. Shared available cash: Rs %.2f",
            segment, amount, self.available_margin,
        )

    def release_margin(self, segment: str, amount: float):
        amount = float(amount)
        if amount < 0:
            raise ValueError("Margin amount cannot be negative")
        with self.accounting_transaction() as (conn, available):
            self.write_available_margin(conn, available + amount)
        logger.info(
            "Released %s margin: Rs %.2f. Shared available cash: Rs %.2f",
            segment, amount, self.available_margin,
        )

    def apply_pnl(self, segment: str, net_pnl: float):
        net_pnl = float(net_pnl)
        with self.accounting_transaction() as (conn, available):
            self.write_available_margin(conn, available + net_pnl)
        logger.info(
            "Applied net P&L for %s: Rs %.2f. Shared available cash: Rs %.2f",
            segment, net_pnl, self.available_margin,
        )
