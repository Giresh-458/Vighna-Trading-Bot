import asyncio
import logging
import threading
from typing import Set, Dict
from SmartApi.smartWebSocketV2 import SmartWebSocketV2
from app.market_data_cache import MarketDataCache

# Fix Angel One SDK bug where SmartWebSocketV2._on_close signature expects 1 arg but websocket-client passes 3
def _patched_on_close(self, *args, **kwargs):
    if hasattr(self, 'on_close') and callable(self.on_close):
        try:
            self.on_close(*args, **kwargs)
        except TypeError:
            self.on_close(args[0] if args else self.wsapp)

SmartWebSocketV2._on_close = _patched_on_close

logger = logging.getLogger(__name__)

class AngelOneWebSocket:
    def __init__(self, auth, md_cache: MarketDataCache):
        self.auth = auth
        self.md_cache = md_cache
        self.sws = None
        self.active_tokens: Dict[str, str] = {}
        self._lock = threading.Lock()
        self._thread = None
        self.is_connected = False
        self._reconnect_timer = None
        self._watchdog_started = False

    def connect(self):
        # Clean up any existing connection
        if self.sws:
            try:
                self.sws.close_connection()
            except Exception:
                pass
            self.sws = None
        self.is_connected = False

        if hasattr(self.auth, "get_tokens"):
            tokens = self.auth.get_tokens()
        elif isinstance(self.auth, dict):
            tokens = self.auth
        else:
            tokens = {}
        jwt_token = tokens.get("jwtToken")
        api_key = tokens.get("apiKey")
        client_code = tokens.get("clientId")
        feed_token = tokens.get("feedToken")

        if not all([jwt_token, api_key, client_code, feed_token]):
            logger.error("Missing authentication tokens for WebSocket. Retrying in 10s...")
            self._schedule_reconnect(10.0)
            return

        try:
            self.sws = SmartWebSocketV2(jwt_token, api_key, client_code, feed_token)
            self.sws.on_data = self._on_data
            self.sws.on_error = self._on_error
            self.sws.on_close = self._on_close
            self.sws.on_open = self._on_open

            logger.info("Connecting to Angel One SmartWebSocketV2...")
            self._thread = threading.Thread(target=self.sws.connect, daemon=True)
            self._thread.start()

            if not self._watchdog_started:
                self._start_watchdog()
                self._watchdog_started = True
        except Exception as e:
            logger.error(f"Failed to initialize WebSocket: {e}. Retrying in 5s...")
            self._schedule_reconnect(5.0)

    def _on_open(self, *args, **kwargs):
        logger.info("Angel One WebSocket connection established.")
        self.is_connected = True
        self._resubscribe_all()

    def _on_close(self, *args, **kwargs):
        logger.warning("Angel One WebSocket connection closed. Attempting reconnect in 5s...")
        self.is_connected = False
        self._schedule_reconnect(5.0)

    def _on_error(self, *args, **kwargs):
        logger.error(f"Angel One WebSocket Error: {args}")
        err_msg = str(args).lower()
        if "closed" in err_msg or "retry" in err_msg or "failed" in err_msg or "broken" in err_msg:
            self.is_connected = False
            self._schedule_reconnect(5.0)

    def _schedule_reconnect(self, delay: float = 5.0):
        with self._lock:
            if self._reconnect_timer and self._reconnect_timer.is_alive():
                return
            self._reconnect_timer = threading.Timer(delay, self.connect)
            self._reconnect_timer.daemon = True
            self._reconnect_timer.start()

    def _start_watchdog(self):
        def _watchdog_loop():
            import time
            while True:
                time.sleep(15)
                with self._lock:
                    has_tokens = bool(self.active_tokens)
                if has_tokens and not self.is_connected:
                    logger.warning("WebSocket Watchdog: active tokens exist but WebSocket is disconnected. Reconnecting...")
                    self._schedule_reconnect(1.0)
        t = threading.Thread(target=_watchdog_loop, daemon=True)
        t.start()

    def _on_data(self, wsapp, message):
        # Example message format (from SmartWebSocketV2 docs):
        # {'type': 1, 'exchangeType': 2, 'token': '47055', 'sequenceNumber': 2139634, 
        #  'exchangeTimeStamp': 1680687103000, 'lastTradedPrice': 3010, ...}
        
        # DEBUG: Print raw message to see what we are receiving
        # print(f"RAW WS MSG: {message}")
        
        if isinstance(message, dict):
            token = message.get("token")
            # Angel One uses snake_case keys in current versions
            ltp = message.get("last_traded_price") or message.get("lastTradedPrice")
            if token and ltp is not None:
                # Divide by 100 to get actual float price
                real_ltp = float(ltp) / 100.0
                exchange_timestamp = message.get("exchange_timestamp") or message.get("exchangeTimeStamp")
                
                self.md_cache.update_tick(token, real_ltp, exchange_timestamp)

    def subscribe(self, token: str, exch_seg: str = "NFO"):
        with self._lock:
            self.active_tokens[token] = exch_seg
        if self.is_connected and self.sws:
            # Action: 1 = Subscribe, Mode: 1 = LTP
            exchange_type = 5 if exch_seg == "MCX" else 4 if exch_seg == "BFO" else 2
            req = {
                "correlation_id": f"sub_{token}",
                "action": 1,
                "mode": 1,
                "exchangeType": exchange_type,
                "tokens": [token]
            }
            try:
                logger.info(f"Subscribing to token {token} on {exch_seg}")
                self.sws.subscribe(req["correlation_id"], req["mode"], [{"exchangeType": req["exchangeType"], "tokens": req["tokens"]}])
            except Exception as e:
                logger.warning(f"Error subscribing to {token}: {e}. Triggering reconnect...")
                self.is_connected = False
                self._schedule_reconnect(1.0)
        else:
            # We are not connected yet; ensure reconnect is underway so it gets subscribed on open
            self._schedule_reconnect(1.0)

    def unsubscribe(self, token: str):
        with self._lock:
            exch_seg = self.active_tokens.pop(token, "NFO")
        if self.is_connected and self.sws:
            # Action: 0 = Unsubscribe
            exchange_type = 5 if exch_seg == "MCX" else 4 if exch_seg == "BFO" else 2
            req = {
                "correlation_id": f"unsub_{token}",
                "action": 0,
                "mode": 1,
                "exchangeType": exchange_type,
                "tokens": [token]
            }
            try:
                logger.info(f"Unsubscribing from token {token} on {exch_seg}")
                self.sws.unsubscribe(req["correlation_id"], req["mode"], [{"exchangeType": req["exchangeType"], "tokens": req["tokens"]}])
            except Exception as e:
                logger.debug(f"Ignored unsubscribe error: {e}")

    def _resubscribe_all(self):
        with self._lock:
            if not self.active_tokens or not self.sws:
                return
            tokens_nfo = [t for t, exch in self.active_tokens.items() if exch not in ["MCX", "BFO"]]
            tokens_mcx = [t for t, exch in self.active_tokens.items() if exch == "MCX"]
            tokens_bfo = [t for t, exch in self.active_tokens.items() if exch == "BFO"]
            
        logger.info(f"Resubscribing to {len(self.active_tokens)} active tokens...")
        try:
            if tokens_nfo:
                self.sws.subscribe("resub_nfo", 1, [{"exchangeType": 2, "tokens": tokens_nfo}])
            if tokens_mcx:
                self.sws.subscribe("resub_mcx", 1, [{"exchangeType": 5, "tokens": tokens_mcx}])
            if tokens_bfo:
                self.sws.subscribe("resub_bfo", 1, [{"exchangeType": 4, "tokens": tokens_bfo}])
        except Exception as e:
            logger.error(f"Error resubscribing tokens: {e}")
        
    def stop(self):
        if self.sws:
            # sws.close_connection() method exists in SmartWebSocketV2
            try:
                self.sws.close_connection()
            except AttributeError:
                pass # depends on sdk version
