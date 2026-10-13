import uuid
import time
import threading
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional
from app.models import ParsedSignal, SignalStatus, TradeType, OptionType
from app.database import Database
from app.portfolio import Portfolio
from app.charge_engine import ChargeEngine

logger = logging.getLogger(__name__)

class PositionManager:
    def __init__(self, db: Database, portfolio: Portfolio, charge_engine: ChargeEngine, inst_manager=None, on_trade_callback=None, real_order_manager=None):
        self.db = db
        self.portfolio = portfolio
        self.charge_engine = charge_engine
        self.inst_manager = inst_manager
        self.on_trade_callback = on_trade_callback
        self.real_order_manager = real_order_manager
        # In-memory tracking of active state
        self.active_signals: Dict[str, dict] = {} 
        self.active_positions: Dict[str, dict] = {}
        self._state_lock = threading.Lock()

    def load_active_state(self, inst_manager=None):
        if inst_manager:
            self.inst_manager = inst_manager
            
        now = time.time()
        # Load WATCHING_ENTRY signals
        signals = self.db.fetch_all("SELECT * FROM signals WHERE status = 'WATCHING_ENTRY'")
        for s in signals:
            sig_dict = dict(s)
            received_ts = now
            if sig_dict.get("received_at"):
                try:
                    dt = datetime.fromisoformat(sig_dict["received_at"])
                    received_ts = dt.timestamp()
                except Exception:
                    pass
            
            # If older than threshold, expire it immediately
            segment = sig_dict.get("segment", "equity")
            max_wait = 14400 if segment == "commodity_futures" else 300
            
            if now - received_ts > max_wait:
                self.mark_signal_expired(sig_dict["id"], SignalStatus.EXPIRED.value)
                continue
                
            # If inst_manager is provided, resolve and attach instrument context
            if self.inst_manager and self.inst_manager.is_loaded:
                try:
                    parsed = ParsedSignal(
                        underlying=sig_dict["underlying"],
                        trade_type=TradeType(sig_dict.get("trade_type", "LONG")),
                        strike=float(sig_dict["strike"]) if sig_dict.get("strike") is not None else None,
                        option_type=OptionType(sig_dict["option_type"]) if sig_dict.get("option_type") else None,
                        expiry=sig_dict.get("expiry"),
                        entry_low=float(sig_dict["entry_low"]),
                        entry_high=float(sig_dict["entry_high"]),
                        target_1=float(sig_dict["target_1"]),
                        target_2=float(sig_dict["target_2"]) if sig_dict.get("target_2") else None,
                        target_3=float(sig_dict.get("target_3")) if sig_dict.get("target_3") else None,
                        stop_loss=float(sig_dict["stop_loss"])
                    )
                    inst, _ = self.inst_manager.resolve_signal(parsed)
                    if inst:
                        sig_dict["_instrument"] = inst
                        sig_dict["_received_ts"] = received_ts
                        self.active_signals[sig_dict["id"]] = sig_dict
                        continue
                except Exception as e:
                    logger.warning(f"Could not restore instrument for signal {sig_dict['id']}: {e}")
                    
            self.mark_signal_expired(sig_dict["id"], SignalStatus.EXPIRED.value)
            
        # Reconstruct paired signals based on shared telegram_message_id
        paired_map = {}
        for sig_id, sig in self.active_signals.items():
            msg_id = sig.get("telegram_message_id")
            channel_id = sig.get("channel_id")
            if msg_id:
                message_key = (channel_id, msg_id)
                if message_key in paired_map:
                    other_id = paired_map[message_key]
                    self.active_signals[sig_id]["_paired_signal_id"] = other_id
                    self.active_signals[other_id]["_paired_signal_id"] = sig_id
                else:
                    paired_map[message_key] = sig_id
            
        # Load OPEN positions
        positions = self.db.fetch_all("SELECT * FROM positions WHERE status = 'OPEN'")
        for p in positions:
            self.active_positions[p["id"]] = dict(p)

    def register_signal(self, signal: ParsedSignal, instrument: dict, paired_signal_id: str = None) -> Optional[str]:
        # Duplicate telegram message check (skip for paired signals — they share the same msg_id)
        if signal.telegram_message_id and not paired_signal_id:
            if signal.channel_id is not None:
                existing = self.db.fetch_one(
                    "SELECT id FROM signals WHERE telegram_message_id = ? AND channel_id = ?",
                    (signal.telegram_message_id, signal.channel_id),
                )
            else:
                # Compatibility for callers that do not supply a Telegram channel ID.
                existing = self.db.fetch_one(
                    "SELECT id FROM signals WHERE telegram_message_id = ? AND channel_id IS NULL",
                    (signal.telegram_message_id,),
                )
            if existing:
                logger.warning(
                    "Telegram message %s in channel %s was already registered. Ignoring duplicate.",
                    signal.telegram_message_id,
                    signal.channel_id,
                )
                return None

        # Duplicate token check: do not double-enter same contract simultaneously
        # (skip for paired signals — they watch the same token but opposite directions)
        token = instrument["token"]
        if not paired_signal_id:
            with self._state_lock:
                for s in self.active_signals.values():
                    if s.get("_instrument", {}).get("token") == token:
                        logger.warning(f"Contract {instrument['symbol']} (token: {token}) already has an active watching signal. Ignoring duplicate.")
                        return None
                        
                for p in self.active_positions.values():
                    if p.get("token") == token:
                        logger.warning(f"Contract {instrument['symbol']} (token: {token}) already has an OPEN position. Ignoring duplicate.")
                        return None

        signal_id = str(uuid.uuid4())
        now_str = datetime.now(timezone.utc).isoformat()
        
        # Insert signal
        self.db.execute("""
            INSERT INTO signals (
                id, telegram_message_id, channel_id, received_at, underlying,
                strike, option_type, expiry, entry_low, entry_high, target_1, target_2, target_3, stop_loss, status, created_at, trade_type, segment
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            signal_id,
            signal.telegram_message_id,
            signal.channel_id,
            now_str,
            signal.underlying,
            signal.strike,
            signal.option_type.value if signal.option_type else None,
            instrument["expiry"],
            signal.entry_low,
            signal.entry_high,
            signal.target_1,
            signal.target_2,
            signal.target_3,
            signal.stop_loss,
            SignalStatus.WATCHING_ENTRY.value,
            now_str,
            signal.trade_type.value,
            signal.segment
        ))
        
        # Keep in memory with instrument details attached for execution
        sig_data = self.db.fetch_one("SELECT * FROM signals WHERE id = ?", (signal_id,))
        if sig_data:
            sig_dict = dict(sig_data)
        else:
            sig_dict = {
                "id": signal_id,
                "telegram_message_id": signal.telegram_message_id,
                "channel_id": signal.channel_id,
                "received_at": now_str,
                "underlying": signal.underlying,
                "strike": signal.strike,
                "option_type": signal.option_type.value if signal.option_type else None,
                "expiry": instrument.get("expiry"),
                "entry_low": signal.entry_low,
                "entry_high": signal.entry_high,
                "target_1": signal.target_1,
                "target_2": signal.target_2,
                "target_3": signal.target_3,
                "stop_loss": signal.stop_loss,
                "status": SignalStatus.WATCHING_ENTRY.value,
                "created_at": now_str,
                "trade_type": signal.trade_type.value,
                "segment": signal.segment
            }
        sig_dict["_instrument"] = instrument
        sig_dict["_received_ts"] = time.time()
        if paired_signal_id:
            sig_dict["_paired_signal_id"] = paired_signal_id
        with self._state_lock:
            self.active_signals[signal_id] = sig_dict
        
        logger.info(f"Registered new signal {signal_id} for {instrument['symbol']}" + 
                     (f" (paired with {paired_signal_id})" if paired_signal_id else ""))
        return signal_id

    def mark_signal_expired(self, signal_id: str, reason: str):
        with self._state_lock:
            if signal_id in self.active_signals:
                del self.active_signals[signal_id]
        self.db.execute("UPDATE signals SET status = ? WHERE id = ?", (reason, signal_id))
        logger.info(f"Signal {signal_id} marked as {reason}")

    def execute_entry(self, signal_id: str, ltp: float):
        sig = self.active_signals.get(signal_id)
        if not sig:
            return
            
        inst = sig.get("_instrument")
        if not inst:
            logger.error(f"Cannot execute entry for {signal_id}, missing instrument context")
            return
            
        segment = sig.get("segment", "equity")
        
        # Trading policy: always execute exactly one lot per accepted signal.
        quantity = int(inst["lotsize"])
        
        required_margin = ltp * quantity
        
        # Apply realistic 10% margin for Futures, Options buyers pay full premium
        if segment == "commodity_futures" or (inst.get("instrumenttype") in ["FUTSTK", "FUTIDX", "FUTCOM"]):
            required_margin = required_margin * 0.10
            
        if not self.portfolio.has_sufficient_margin(segment, required_margin):
            logger.warning(f"Insufficient {segment} margin to enter {inst['symbol']}. Required: {required_margin}")
            self.mark_signal_expired(signal_id, SignalStatus.INSUFFICIENT_CAPITAL.value)
            return
            
        # Block margin
        self.portfolio.block_margin(segment, required_margin)
        
        # Create position
        pos_id = str(uuid.uuid4())
        now_str = datetime.now(timezone.utc).isoformat()
        
        self.db.execute("""
            INSERT INTO positions (id, signal_id, token, symbol, entry_price, quantity, entry_time, target_1, stop_loss, status, trade_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?)
        """, (
            pos_id, signal_id, inst["token"], inst["symbol"], ltp, quantity, now_str,
            sig["target_1"], sig["stop_loss"], sig["trade_type"]
        ))
        
        # Update signal
        self.db.execute("UPDATE signals SET status = ? WHERE id = ?", (SignalStatus.ENTERED.value, signal_id))
        with self._state_lock:
            # Cancel paired signal (opposite direction) if it exists
            paired_id = sig.get("_paired_signal_id")
            del self.active_signals[signal_id]
        
        if paired_id:
            logger.info(f"Cancelling paired signal {paired_id} (opposite direction entered)")
            self.mark_signal_expired(paired_id, "PAIRED_CANCELLED")
        
        # Add to active positions
        pos_data = self.db.fetch_one("SELECT * FROM positions WHERE id = ?", (pos_id,))
        if pos_data:
            pos_dict = dict(pos_data)
        else:
            pos_dict = {
                "id": pos_id,
                "signal_id": signal_id,
                "token": inst["token"],
                "symbol": inst["symbol"],
                "entry_price": ltp,
                "quantity": quantity,
                "entry_time": now_str,
                "target_1": sig["target_1"],
                "stop_loss": sig["stop_loss"],
                "status": "OPEN",
                "trade_type": sig["trade_type"]
            }
        with self._state_lock:
            self.active_positions[pos_id] = pos_dict
        
        # Format the notification message
        opt_str = ""
        if sig.get("strike") and sig.get("option_type"):
            opt_str = f"{int(sig['strike'])} {sig['option_type']}"
        elif sig.get("trade_type") == "SHORT" and not sig.get("strike"):
            opt_str = "FUT (SHORT)"
        elif not sig.get("strike"):
            opt_str = "FUT"
            
        msg = (
            f"✅ **TRADE ENTERED**\n"
            f"#{sig['underlying']} {opt_str}\n"
            f"Entry: {ltp}\n"
            f"Target: {sig['target_1']}\n"
            f"SL: {sig['stop_loss']}"
        )
        
        logger.info(f"ENTERED {inst['symbol']} at {ltp} (Qty: {quantity})")
        if self.real_order_manager:
            try:
                self.real_order_manager.place_entry_order(signal_id, inst, sig["trade_type"], quantity, ltp, segment)
            except Exception as e:
                logger.error(f"Error placing live entry order: {e}")

        if self.on_trade_callback:
            self.on_trade_callback(msg, sig)

    def execute_exit(self, pos_id: str, ltp: float, reason: str):
        with self._state_lock:
            pos = self.active_positions.pop(pos_id, None)
        if not pos:
            return
            
        now_str = datetime.now(timezone.utc).isoformat()
        quantity = pos["quantity"]
        entry_price = pos["entry_price"]
        trade_type = pos.get("trade_type", "LONG")
        
        # Fetch segment from signal
        sig_data = self.db.fetch_one("SELECT underlying, strike, option_type, trade_type, segment FROM signals WHERE id = ?", (pos["signal_id"],))
        segment = "equity"
        if sig_data:
            segment = sig_data.get("segment", "equity")
                
        # Calculate PnL and charges
        if trade_type == "LONG":
            gross_pnl = (ltp - entry_price) * quantity
        else:
            gross_pnl = (entry_price - ltp) * quantity
            
        charges_dict = self.charge_engine.calculate_total_trade_charges(entry_price, ltp, quantity, segment, trade_type)
        total_charges = charges_dict["total_charges"]
        net_pnl = gross_pnl - total_charges
                
        # Reconstruct initial margin that was blocked
        initial_margin = entry_price * quantity
        if segment == "commodity_futures" or (sig_data and sig_data.get("instrumenttype") in ["FUTSTK", "FUTIDX", "FUTCOM"]):
            initial_margin = initial_margin * 0.10
            
        # Release initial margin
        self.portfolio.release_margin(segment, initial_margin)
        # Apply net PnL
        self.portfolio.apply_pnl(segment, net_pnl)
        
        # Close position
        self.db.execute("""
            UPDATE positions 
            SET status = 'CLOSED', exit_price = ?, exit_time = ?, exit_reason = ?
            WHERE id = ?
        """, (ltp, now_str, reason, pos_id))
        
        # Record trade
        self.db.execute("""
            INSERT INTO trades (position_id, entry_price, exit_price, quantity, gross_pnl, brokerage, taxes, charges, net_pnl, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            pos_id, entry_price, ltp, quantity, gross_pnl,
            charges_dict["buy"]["brokerage"] + charges_dict["sell"]["brokerage"],
            charges_dict["buy"]["stt"] + charges_dict["sell"]["stt"],
            total_charges, net_pnl, now_str
        ))
        
        # Try to parse symbol components for clean notification
        symbol = pos["symbol"]
        opt_str = ""
        # sig_data already fetched above
        
        if sig_data:
            underlying = sig_data["underlying"]
            if sig_data.get("strike") and sig_data.get("option_type"):
                opt_str = f"{int(sig_data['strike'])} {sig_data['option_type']}"
            elif sig_data.get("trade_type") == "SHORT" and not sig_data.get("strike"):
                opt_str = "FUT (SHORT)"
            elif not sig_data.get("strike"):
                opt_str = "FUT"
        else:
            underlying = symbol
            opt_str = ""

        icon = "🔴" if net_pnl < 0 else "🟢"
        
        reason_display = reason.replace("_", " ")

        logger.info(f"EXITED {pos['symbol']} at {ltp} (Reason: {reason}) | Net PnL: ₹{net_pnl:.2f}")

        # Place real broker exit order if live trading is active.
        # Main startup currently blocks live mode until broker fill reconciliation exists.
        if self.real_order_manager:
            try:
                self.real_order_manager.place_exit_order(pos_id, pos["symbol"], pos["token"], trade_type, quantity, segment, reason)
            except Exception as e:
                logger.error(f"Error placing live exit order: {e}")

        exit_msg = (
            f"{icon} **TRADE EXITED — {reason_display}**\n"
            f"#{underlying} {opt_str}\n"
            f"Entry: {entry_price}\n"
            f"Exit: {ltp}\n"
            f"Gross P&L: ₹{gross_pnl:.2f}\n"
            f"Net P&L: ₹{net_pnl:.2f}"
        )
        if self.on_trade_callback:
            callback_data = dict(sig_data) if sig_data else {}
            callback_data["segment"] = segment
            self.on_trade_callback(exit_msg, callback_data)

    def get_all_active_tokens_with_exchange(self) -> Dict[str, str]:
        tokens = {}
        with self._state_lock:
            for sig in self.active_signals.values():
                inst = sig.get("_instrument")
                if inst:
                    tokens[inst["token"]] = inst.get("exch_seg", "NFO")
            for pos in self.active_positions.values():
                # fetch from inst if possible, default to NFO if not stored
                # actually we don't store exch_seg in positions yet.
                # let's try to get it from inst_manager
                exch_seg = "NFO"
                if self.inst_manager and pos["token"] in self.inst_manager.token_to_instrument:
                    exch_seg = self.inst_manager.token_to_instrument[pos["token"]].get("exch_seg", "NFO")
                tokens[pos["token"]] = exch_seg
        return tokens

