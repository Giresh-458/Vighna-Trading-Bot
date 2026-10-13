import time
import logging
from typing import Dict, List
from datetime import datetime, timezone, timedelta
from app.market_data_cache import MarketDataCache
from app.position_manager import PositionManager
from app.models import SignalStatus
from app.angelone_marketdata import AngelOneWebSocket
from app.config import EOD_CLOSE_TIME

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))

class PaperEngine:
    ENTRY_WAIT_SECONDS = 300  # 5 minutes max wait for entry

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
            max_wait = 14400 if segment == "commodity_futures" else self.ENTRY_WAIT_SECONDS
            
            received_ts = sig.get("_received_ts", now)
            if now - received_ts > max_wait:
                self.pos_manager.mark_signal_expired(sig_id, SignalStatus.EXPIRED.value)
                self.ws.unsubscribe(token)
                continue
                
            # Use longer staleness window — illiquid options may tick infrequently
            ltp = self.md_cache.get_ltp(token, max_age_ms=30000)
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
        """Checks open positions for Target or SL. Never stops until one is hit."""
        with self.pos_manager._state_lock:
            pos_ids = list(self.pos_manager.active_positions.keys())
        for pos_id in pos_ids:
            pos = self.pos_manager.active_positions.get(pos_id)
            if not pos: continue
            
            token = pos["token"]
            # Always use the last known price — never skip a position.
            # An illiquid option's last trade at 5.0 is still 5.0
            # even if that tick was minutes ago.
            raw = self.md_cache.get_raw_tick(token)
            if not raw:
                continue
            ltp = raw["ltp"]
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
                continue  # Skip MCX until 23:30
            if exch_seg == "BFO" and not is_bfo_eod:
                continue  # Skip BFO until 15:30
            
            # Retrieve signal to check if it's hero_zero
            sig_data = self.pos_manager.db.fetch_one("SELECT segment FROM signals WHERE id = ?", (pos.get("signal_id"),))
            segment = sig_data["segment"] if sig_data else "equity"
            if segment == "hero_zero" and not is_hz_eod:
                continue  # Skip Hero-Zero until 15:40
            
            raw = self.md_cache.get_raw_tick(token)
            ltp = raw["ltp"] if raw else pos["entry_price"]
            
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
        
        if mcx_eod:
            if not self.eod_processed:
                self.check_eod_squareoff()
                self.eod_processed = True
            return  # Everything is closed
            
        if hz_eod and not self.hz_eod_processed:
            self.check_eod_squareoff()
            self.hz_eod_processed = True
        elif bfo_eod and not self.bfo_eod_processed:
            self.check_eod_squareoff()
            self.bfo_eod_processed = True
        elif nfo_eod and not self.nfo_eod_processed:
            self.check_eod_squareoff()
            self.nfo_eod_processed = True
                
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
                reported_cmp = None
                try:
                    import json
                    if payload and payload.strip().startswith("{"):
                        p_data = json.loads(payload)
                        pos_id = p_data.get("pos_id", payload)
                        reported_cmp = p_data.get("cmp")
                except Exception:
                    pos_id = payload

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
                            if res and res.get("status") and res.get("data"):
                                live_cmp = float(res["data"]["ltp"])
                    except Exception as e:
                        logger.warning(f"Could not fetch live REST CMP for {symbol}: {e}")

                    # 2. Priority: Live REST CMP -> Reported UI CMP -> Cache -> Entry Price
                    raw = self.md_cache.get_raw_tick(token)
                    cache_ltp = raw["ltp"] if raw else None
                    
                    exit_price = live_cmp or reported_cmp or cache_ltp or pos["entry_price"]
                    logger.info(f"Manual Close Triggered for {pos_id} at {exit_price} (live_cmp={live_cmp}, reported_cmp={reported_cmp}, cache_ltp={cache_ltp})")
                    self.pos_manager.execute_exit(pos_id, exit_price, "MANUAL_EXIT")
                    self.ws.unsubscribe(token)
                
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
