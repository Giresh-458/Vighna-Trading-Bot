import time
import logging
from typing import Dict, Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

class MarketDataCache:
    def __init__(self):
        # Maps token -> {ltp, timestamp, exchange_timestamp, received_timestamp_ns}
        self.cache: Dict[str, dict] = {}
        
    def update_tick(self, token: str, ltp: float, exchange_timestamp_ms: Optional[int] = None):
        now_ns = time.time_ns()
        
        # If exchange_timestamp_ms is available from the WebSocket tick, use it.
        # Otherwise fall back to local system time.
        # WebSocket V2 usually provides 'exchange_timestamp' in milliseconds.
        
        self.cache[token] = {
            "ltp": ltp,
            "received_timestamp_ns": now_ns,
            "exchange_timestamp_ms": exchange_timestamp_ms
        }

    def get_ltp(self, token: str, max_age_ms: int = 2000) -> Optional[float]:
        """
        Returns the LTP if it's not older than max_age_ms.
        """
        data = self.cache.get(token)
        if not data:
            return None
        
        age_ns = time.time_ns() - data["received_timestamp_ns"]
        age_ms = age_ns / 1_000_000
        
        if age_ms > max_age_ms:
            # Stale data
            return None
            
        return data["ltp"]
        
    def get_raw_tick(self, token: str) -> Optional[dict]:
        return self.cache.get(token)
