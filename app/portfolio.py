import logging
import threading
from app.database import Database
from app.config import INITIAL_CAPITAL

logger = logging.getLogger(__name__)

class Portfolio:
    def __init__(self, db: Database):
        self.db = db
        self._lock = threading.RLock()
        self.margins = self._load_capital()

    def _load_capital(self) -> dict:
        margins = {}
        for seg in ["equity", "commodity_futures", "commodity_options", "hero_zero"]:
            row = self.db.fetch_one("SELECT value FROM system_state WHERE key = ?", (f"available_margin_{seg}",))
            if row:
                margins[seg] = float(row["value"])
            else:
                cap = float(INITIAL_CAPITAL)
                self.db.execute("INSERT INTO system_state (key, value) VALUES (?, ?)", (f"available_margin_{seg}", str(cap)))
                margins[seg] = cap
        return margins
            
    def _save_capital(self, segment: str):
        self.db.execute("UPDATE system_state SET value = ? WHERE key = ?", (str(self.margins[segment]), f"available_margin_{segment}"))

    def has_sufficient_margin(self, segment: str, required_margin: float) -> bool:
        with self._lock:
            return self.margins.get(segment, 0.0) >= required_margin

    def block_margin(self, segment: str, amount: float):
        with self._lock:
            if not self.has_sufficient_margin(segment, amount):
                raise ValueError(f"Insufficient margin for {segment}. Required: {amount}, Available: {self.margins.get(segment, 0.0)}")
            self.margins[segment] -= amount
            self._save_capital(segment)
            logger.info(f"Blocked {segment} margin: Rs {amount:.2f}. Available: Rs {self.margins[segment]:.2f}")

    def release_margin(self, segment: str, amount: float):
        with self._lock:
            self.margins[segment] += amount
            self._save_capital(segment)
            logger.info(f"Released {segment} margin: Rs {amount:.2f}. Available: Rs {self.margins[segment]:.2f}")

    def apply_pnl(self, segment: str, net_pnl: float):
        with self._lock:
            self.margins[segment] += net_pnl
            self._save_capital(segment)
            logger.info(f"Applied Net PnL to {segment}: Rs {net_pnl:.2f}. New Capital: Rs {self.margins[segment]:.2f}")
