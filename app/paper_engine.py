import time
import logging
from typing import Dict, List
from datetime import datetime, timezone, timedelta
from app.market_data_cache import MarketDataCache
from app.position_manager import PositionManager
from app.models import SignalStatus
from app.angelone_marketdata import AngelOneWebSocket
from app.config import (
    EOD_CLOSE_TIME,
    ENTRY_WAIT_SECONDS,
    COMMODITY_FUTURES_ENTRY_WAIT_SECONDS,
    MAX_TICK_AGE_MS,
)

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))

class PaperEngine:
    EOD_RETRY_INTERVAL_SECONDS = 5.0

    def __init__(self, md_cache: MarketDataCache, pos_manager: PositionManager, ws: AngelOneWebSocket):
        self.md_cache = md_cache
        self.pos_manager = pos_manager
        self.ws = ws
        self.is_running = False
        self.eod_processed = False
        self.nfo_eod_processed = False
        self.bfo_eod_processed = False
        self.hz_eod_processed = False
        self._last_eod_date = None
        self._last_eod_attempt_monotonic = 0.0
        self._last_manual_exit_attempts: Dict[int, float] = {}

    def check_signals(self):
        """Checks waiting signals for entry or expiry."""
        now = time.time()
        
        # Make a copy of keys to avoid dict size changed during iteration
        with self.pos_manager._state_lock:
            sig_ids = list(self.pos_manager.active_signals.keys())
        for sig_id in sig_ids:
            sig = self.pos_manager.active_signals.get(sig_id)
            if not sig: continue
            
            inst = sig.get("_instrument")
            if not inst: continue
            
            token = inst["token"]
            
            # Check expiry (dynamic window)
            segment = sig.get("segment", "equity")
            max_wait = (
                COMMODITY_FUTURES_ENTRY_WAIT_SECONDS
                if segment == "commodity_futures"
                else ENTRY_WAIT_SECONDS
            )

            # Do not enter a new position after that instrument's session cutoff.
            now_ist = datetime.now(IST)
            current_minutes = now_ist.hour * 60 + now_ist.minute
            exch_seg = inst.get("exch_seg", "NFO")
            if exch_seg == "MCX":
                entry_cutoff = 23 * 60
            elif exch_seg == "BFO":
                entry_cutoff = 15 * 60 + 30
            elif segment == "hero_zero":
                entry_cutoff = 15 * 60 + 40
            else:
                try:
                    cutoff_hour, cutoff_minute = map(int, EOD_CLOSE_TIME.split(":"))
                    entry_cutoff = cutoff_hour * 60 + cutoff_minute
                except Exception:
                    entry_cutoff = 15 * 60 + 15
            if current_minutes >= entry_cutoff:
                continue

            received_ts = sig.get("_received_ts", now)
            if now - received_ts > max_wait:
                self.pos_manager.mark_signal_expired(sig_id, SignalStatus.EXPIRED.value)
                self.ws.unsubscribe(token)
                continue
                
            # Entry decisions must use data inside the configured freshness window.
            ltp = self.md_cache.get_ltp(token, max_age_ms=MAX_TICK_AGE_MS)
            if not ltp:
                continue
                
            # If target 1 is already reached before entry, invalidate
            # For SHORT, we check if ltp <= target_1
            trade_type = sig.get("trade_type", "LONG")
            if trade_type == "LONG" and ltp >= sig["target_1"]:
                self.pos_manager.mark_signal_expired(sig_id, SignalStatus.EXPIRED_TARGET_REACHED.value)
                self.ws.unsubscribe(token)
                continue
            elif trade_type == "SHORT" and ltp <= sig["target_1"]:
                self.pos_manager.mark_signal_expired(sig_id, SignalStatus.EXPIRED_TARGET_REACHED.value)
                self.ws.unsubscribe(token)
                continue
                
            # Check entry condition
            if sig["entry_low"] <= ltp <= sig["entry_high"]:
                self.pos_manager.execute_entry(sig_id, ltp)

    def check_positions(self):
        """Checks open positions for Target or SL using only fresh market data."""
        with self.pos_manager._state_lock:
            pos_ids = list(self.pos_manager.active_positions.keys())
        for pos_id in pos_ids:
            pos = self.pos_manager.active_positions.get(pos_id)
            if not pos: continue
            
            token = pos["token"]
            # Stale prices must not trigger simulated exits or be written as live ticks.
            ltp = self.md_cache.get_ltp(token, max_age_ms=MAX_TICK_AGE_MS)
            if ltp is None:
                continue
            try:
                self.pos_manager.db.update_live_tick(token, pos["symbol"], ltp)
            except Exception:
                pass
            
            trade_type = pos.get("trade_type", "LONG")
            
            if trade_type == "LONG":
                # Target 1 (EXIT ENTIRE POSITION)
                if ltp >= pos["target_1"]:
                    self.pos_manager.execute_exit(pos_id, ltp, SignalStatus.TARGET_HIT.value)
                    self.ws.unsubscribe(token)
                    continue
                    
                # Stop Loss (EXIT ENTIRE POSITION)
                if ltp <= pos["stop_loss"]:
                    self.pos_manager.execute_exit(pos_id, ltp, SignalStatus.SL_HIT.value)
                    self.ws.unsubscribe(token)
                    continue
            else:
                # Target 1 for SHORT (EXIT ENTIRE POSITION)
                if ltp <= pos["target_1"]:
                    self.pos_manager.execute_exit(pos_id, ltp, SignalStatus.TARGET_HIT.value)
                    self.ws.unsubscribe(token)
                    continue
                    
                # Stop Loss for SHORT (EXIT ENTIRE POSITION)
                if ltp >= pos["stop_loss"]:
                    self.pos_manager.execute_exit(pos_id, ltp, SignalStatus.SL_HIT.value)
                    self.ws.unsubscribe(token)
                    continue

    def is_eod(self) -> bool:
        """Checks if current time in IST has reached or passed EOD_CLOSE_TIME (e.g. 15:15)."""
        now_ist = datetime.now(IST)
        try:
            eod_hour, eod_minute = map(int, EOD_CLOSE_TIME.split(":"))
        except Exception:
            eod_hour, eod_minute = 15, 15
            
        current_minutes = now_ist.hour * 60 + now_ist.minute
        eod_minutes = eod_hour * 60 + eod_minute
        return current_minutes >= eod_minutes

    def check_eod_squareoff(self):
        """Closes all open positions and cancels pending signals at EOD."""
        now_ist = datetime.now(IST)
        current_minutes = now_ist.hour * 60 + now_ist.minute
        is_mcx_eod = current_minutes >= (23 * 60 + 0)  # 23:00 IST
        is_bfo_eod = current_minutes >= (15 * 60 + 30)  # 15:30 IST
        is_hz_eod = current_minutes >= (15 * 60 + 40)   # 15:40 IST

        # 1. Close all active positions
        with self.pos_manager._state_lock:
            pos_ids = list(self.pos_manager.active_positions.keys())
        for pos_id in pos_ids:
            pos = self.pos_manager.active_positions.get(pos_id)
            if not pos: continue
            
            token = pos["token"]
            exch_seg = "NFO"
            if self.pos_manager.inst_manager and token in self.pos_manager.inst_manager.token_to_instrument:
                exch_seg = self.pos_manager.inst_manager.token_to_instrument[token].get("exch_seg", "NFO")
                
            if exch_seg == "MCX" and not is_mcx_eod:
                continue  # Skip MCX until 23:00
            if exch_seg == "BFO" and not is_bfo_eod:
                continue  # Skip BFO until 15:30
            
            # Retrieve signal to check if it's hero_zero
            sig_data = self.pos_manager.db.fetch_one("SELECT segment FROM signals WHERE id = ?", (pos.get("signal_id"),))
            segment = sig_data["segment"] if sig_data else "equity"
            if segment == "hero_zero" and not is_hz_eod:
                continue  # Skip Hero-Zero until 15:40
            
            # Prefer a fresh WebSocket tick; otherwise ask Angel One for the current LTP.
            ltp = self.md_cache.get_ltp(token, max_age_ms=MAX_TICK_AGE_MS)
            if ltp is None:
                api = getattr(getattr(self.ws, "auth", None), "api", None)
                if api:
                    try:
                        response = api.ltpData(exch_seg, pos["symbol"], str(token))
                        if (
                            isinstance(response, dict)
                            and response.get("status")
                            and isinstance(response.get("data"), dict)
                        ):
                            candidate = response["data"].get("ltp")
                            if candidate is not None:
                                candidate = float(candidate)
                                if candidate > 0:
                                    ltp = candidate
                    except Exception as exc:
                        logger.warning("EOD live LTP lookup failed for %s: %s", pos["symbol"], exc)

            if ltp is None or ltp <= 0:
                # Never fabricate a close price from entry price; keep the position visible/open
                # and retry EOD processing after a short delay.
                logger.error(
                    "EOD square-off deferred for %s: no fresh/REST price is available.",
                    pos["symbol"],
                )
                continue

            logger.info(f"EOD Square-Off: Closing {pos['symbol']} at {ltp} (Reason: EOD_CLOSED)")
            self.pos_manager.execute_exit(pos_id, ltp, SignalStatus.EOD_CLOSED.value)
            self.ws.unsubscribe(token)
            
        # 2. Expire all pending entry signals
        with self.pos_manager._state_lock:
            sig_ids = list(self.pos_manager.active_signals.keys())
        for sig_id in sig_ids:
            sig = self.pos_manager.active_signals.get(sig_id)
            if not sig: continue
            
            inst = sig.get("_instrument", {})
            token = inst.get("token")
            exch_seg = inst.get("exch_seg", "NFO")
            segment = sig.get("segment", "equity")
            
            if exch_seg == "MCX" and not is_mcx_eod:
                continue
            if exch_seg == "BFO" and not is_bfo_eod:
                continue
            if segment == "hero_zero" and not is_hz_eod:
                continue
                
            logger.info(f"EOD Square-Off: Expiring pending signal {sig_id}")
            self.pos_manager.mark_signal_expired(sig_id, SignalStatus.EOD_CLOSED.value)
            if token:
                self.ws.unsubscribe(token)
                
        # If we reached here and MCX is also closed (or we only had NFO), 
        # eod_processed should be True, but since MCX closes later, we might need to 
        # set eod_processed = True only if ALL are processed.
        # Actually, it's safer to just run this check once at 15:15 and once at 23:30.
        # We will let it run on every cycle after 15:15, it will just skip MCX.
        if is_mcx_eod:
            self.eod_processed = True

    def run_cycle(self):
        # We process eod square-off
        now_ist = datetime.now(IST)
        today = now_ist.date()
        if self._last_eod_date != today:
            self.eod_processed = False
            self.nfo_eod_processed = False
            self.bfo_eod_processed = False
            self.hz_eod_processed = False
            self._last_eod_date = today

        current_minutes = now_ist.hour * 60 + now_ist.minute

        # Determine if NFO/NSE EOD is reached
        try:
            eod_hour, eod_minute = map(int, EOD_CLOSE_TIME.split(":"))
        except Exception:
            eod_hour, eod_minute = 15, 15
        nfo_eod = current_minutes >= (eod_hour * 60 + eod_minute)
        
        # BFO EOD is 15:30
        bfo_eod = current_minutes >= (15 * 60 + 30)
        
        # HZ EOD is 15:40
        hz_eod = current_minutes >= (15 * 60 + 40)
        
        # MCX EOD is 23:00
        mcx_eod = current_minutes >= (23 * 60 + 0)
        
        any_eod = nfo_eod or bfo_eod or hz_eod or mcx_eod
        now_monotonic = time.monotonic()
        if any_eod and (
            now_monotonic - self._last_eod_attempt_monotonic
            >= self.EOD_RETRY_INTERVAL_SECONDS
        ):
            # Retry deferred EOD closes rather than silently leaving a made-up fill price.
            self.check_eod_squareoff()
            self._last_eod_attempt_monotonic = now_monotonic

        if mcx_eod:
            self.process_commands()
            return  # No supported segment should open new positions after the final cutoff.

        self.process_commands()
        self.check_signals()
        self.check_positions()

    def process_commands(self):
        """Polls the command_queue for UI interactions."""
        commands = self.pos_manager.db.fetch_all("SELECT * FROM commands WHERE status = 'PENDING'")
        for cmd in commands:
            cmd_id = cmd["id"]
            command = cmd["command"]
            payload = cmd["payload"]
            
            if command == "CLOSE_POSITION":
                pos_id = payload
                try:
                    import json
                    if payload and payload.strip().startswith("{"):
                        p_data = json.loads(payload)
                        pos_id = p_data.get("pos_id", payload)
                except Exception:
                    pos_id = payload

                # Avoid hammering the broker API while retrying a manual close with no price.
                now_monotonic = time.monotonic()
                last_attempt = self._last_manual_exit_attempts.get(cmd_id, 0.0)
                if now_monotonic - last_attempt < 2.0:
                    continue
                self._last_manual_exit_attempts[cmd_id] = now_monotonic

                pos = self.pos_manager.active_positions.get(pos_id)
                if pos:
                    token = pos["token"]
                    symbol = pos["symbol"]
                    
                    # 1. Fetch real-time live CMP directly via Angel One REST API
                    live_cmp = None
                    try:
                        if hasattr(self.ws, "auth") and self.ws.auth and self.ws.auth.api:
                            exch_seg = "NFO"
                            if self.pos_manager.inst_manager and str(token) in self.pos_manager.inst_manager.token_to_instrument:
                                exch_seg = self.pos_manager.inst_manager.token_to_instrument[str(token)].get("exch_seg", "NFO")
                            elif symbol.startswith("SENSEX") or symbol.startswith("BANKEX"):
                                exch_seg = "BFO"
                            elif any(comm in symbol for comm in ["CRUDE", "NATURAL", "GOLD", "SILVER", "COPPER", "ZINC", "ALUM"]):
                                exch_seg = "MCX"
                            res = self.ws.auth.api.ltpData(exch_seg, symbol, str(token))
                            if (
                                isinstance(res, dict)
                                and res.get("status")
                                and isinstance(res.get("data"), dict)
                            ):
                                candidate = res["data"].get("ltp")
                                if candidate is not None:
                                    candidate = float(candidate)
                                    if candidate > 0:
                                        live_cmp = candidate
                    except Exception as e:
                        logger.warning(f"Could not fetch live REST CMP for {symbol}: {e}")

                    # Use only a successful REST quote or a fresh WebSocket tick.
                    cache_ltp = self.md_cache.get_ltp(token, max_age_ms=MAX_TICK_AGE_MS)
                    exit_price = live_cmp if live_cmp is not None else cache_ltp
                    if exit_price is None or exit_price <= 0:
                        logger.error(
                            "Manual close for %s deferred: no fresh REST/WebSocket quote is available.",
                            symbol,
                        )
                        # Keep the command PENDING; the throttled loop retries it.
                        continue

                    logger.info(
                        "Manual Close Triggered for %s at %s (REST=%s, fresh_tick=%s)",
                        pos_id,
                        exit_price,
                        live_cmp,
                        cache_ltp,
                    )
                    self.pos_manager.execute_exit(pos_id, exit_price, "MANUAL_EXIT")
                    self.ws.unsubscribe(token)
                    self._last_manual_exit_attempts.pop(cmd_id, None)
                
            self.pos_manager.db.execute("UPDATE commands SET status = 'DONE' WHERE id = ?", (cmd_id,))
            
    def sync_subscriptions(self):
        """Ensure all required tokens are subscribed to the WebSocket."""
        required_tokens_dict = self.pos_manager.get_all_active_tokens_with_exchange()
        required_tokens = set(required_tokens_dict.keys())
        current_subs = set(self.ws.active_tokens.keys())
        
        # Subscribe to missing
        for token in required_tokens - current_subs:
            self.ws.subscribe(token, required_tokens_dict[token])
            
        # Unsubscribe from stale (optional, to keep WS light)
        for token in current_subs - required_tokens:
            self.ws.unsubscribe(token)

    def start(self):
        self.is_running = True
        logger.info("Paper Engine started.")
        
        # Initial sync
        self.sync_subscriptions()
        
        while self.is_running:
            try:
                self.run_cycle()
                time.sleep(0.05)  # 50ms tick evaluation loop
            except Exception as e:
                logger.error(f"Error in PaperEngine loop: {e}")
                time.sleep(1)

    def stop(self):
        self.is_running = False
        logger.info("Paper Engine stopped.")
