import sys
import time
import logging
from app.config import ANGEL_API_KEY, ANGEL_CLIENT_ID, ANGEL_PASSWORD, ANGEL_TOTP_SECRET
from app.angel_auth import AngelAuth
from app.instrument_manager import InstrumentManager
from app.market_data_cache import MarketDataCache
from app.angelone_marketdata import AngelOneWebSocket
from app.signal_parser import SignalParser

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def main():
    print("====================================================")
    print("ANGEL ONE LIVE MARKET DATA DIAGNOSTIC")
    print("====================================================\n")

    if not all([ANGEL_API_KEY, ANGEL_CLIENT_ID, ANGEL_PASSWORD, ANGEL_TOTP_SECRET]):
        print("FAIL: Angel One credentials missing in .env")
        sys.exit(1)

    print("1. Authenticating with Angel One...")
    auth = AngelAuth()
    if not auth.login():
        print("FAIL: Angel One login failed.")
        sys.exit(1)
        
    print("2. Downloading/Loading Instrument Master...")
    inst_manager = InstrumentManager()
    inst_manager.load_instruments()

    print("3. Connecting to SmartAPI WebSocket V2...")
    md_cache = MarketDataCache()
    ws = AngelOneWebSocket(auth.get_tokens(), md_cache)
    ws.connect()
    
    # Wait for connection
    time.sleep(3)
    if not ws.is_connected:
        print("WARN: WebSocket not connected yet. Waiting 2 more seconds...")
        time.sleep(2)

    print("\n4. Testing with NIFTY 50 Spot Index (Highly Liquid)")
    token = "99926000"  # NIFTY 50 Spot Token
    symbol = "NIFTY"
    
    print(f"Resolved to Token: {token} | Symbol: {symbol}")

    print("\n5. Subscribing to token...")
    # For NSE (Index), exchangeType is 1
    req = {
        "correlation_id": f"sub_{token}",
        "action": 1,
        "mode": 1,
        "exchangeType": 1, # NSE
        "tokens": [token]
    }
    ws.sws.subscribe(req["correlation_id"], req["mode"], [{"exchangeType": req["exchangeType"], "tokens": req["tokens"]}])

    print("\n6. Watching for live ticks for 10 seconds...")
    try:
        for i in range(10):
            ltp = md_cache.get_ltp(token, max_age_ms=5000)
            if ltp is not None:
                print(f"Live Tick [{i+1}/10]: LTP = {ltp}")
            else:
                print(f"Waiting for tick [{i+1}/10]... Cache state: {md_cache.cache}")
            time.sleep(1)
    finally:
        print("Cleaning up...")
        # Avoid calling stop() that triggers the internal smartapi bug, just let it die
        print("Done.")

if __name__ == '__main__':
    main()
