import time
import logging
import threading
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# Lock to ensure only one thread makes an HTTP request via SmartConnect at a time
_api_lock = threading.Lock()
_last_candle_call = 0.0

def check_recent_target_reached(
    smart_api, 
    exchange: str, 
    token: str, 
    target_1: float, 
    lookback_minutes: int = 3,
    trade_type: str = "LONG"
) -> bool:
    """
    Checks if Target 1 was already reached within the last `lookback_minutes`.
    """
    global _last_candle_call
    if not smart_api:
        return False
        
    try:
        now = datetime.now()
        # Look back specified minutes
        from_time = (now - timedelta(minutes=lookback_minutes)).strftime('%Y-%m-%d %H:%M')
        to_time = now.strftime('%Y-%m-%d %H:%M')
        
        params = {
            "exchange": exchange,
            "symboltoken": str(token),
            "interval": "ONE_MINUTE",
            "fromdate": from_time,
            "todate": to_time
        }
        
        with _api_lock:
            now_t = time.time()
            elapsed = now_t - _last_candle_call
            if elapsed < 0.4:
                time.sleep(0.4 - elapsed)
            _last_candle_call = time.time()
            res = smart_api.getCandleData(params)
            
        if res and res.get("status") and res.get("data"):
            # Angel One candle format: [timestamp, open, high, low, close, volume]
            for candle in res["data"]:
                candle_time = candle[0]
                candle_high = float(candle[2])
                candle_low = float(candle[3])
                
                if trade_type == "LONG" and candle_high >= target_1:
                    logger.warning(
                        f"Pre-Call Check: Target 1 ({target_1}) was already hit at {candle_time} "
                        f"(High: {candle_high}) within the last {lookback_minutes} minutes."
                    )
                    return True
                elif trade_type == "SHORT" and candle_low <= target_1:
                    logger.warning(
                        f"Pre-Call Check: Target 1 ({target_1}) was already hit at {candle_time} "
                        f"(Low: {candle_low}) within the last {lookback_minutes} minutes."
                    )
                    return True
    except Exception as e:
        logger.warning(f"Could not verify recent candle data for token {token}: {e}")
        
    return False
