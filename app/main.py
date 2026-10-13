import os
import sys
import time
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from telethon import TelegramClient, events

# --- START MONKEY PATCH FOR CONTENT PROTECTION ---
from telethon.tl.functions.messages import SendMessageRequest
_orig_send_msg_init = SendMessageRequest.__init__

def _patched_send_msg_init(self, *args, **kwargs):
    try:
        kwargs['noforwards'] = True
        _orig_send_msg_init(self, *args, **kwargs)
    except TypeError:
        kwargs.pop('noforwards', None)
        _orig_send_msg_init(self, *args, **kwargs)

SendMessageRequest.__init__ = _patched_send_msg_init
# --- END MONKEY PATCH ---

IST = timezone(timedelta(hours=5, minutes=30))

from app.config import (
    TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_SESSION_NAME, TELEGRAM_CHANNEL_ID,
    COMMODITY_CHANNEL_ID, TELEGRAM_BOT_TOKEN,
    ANGEL_API_KEY, ANGEL_CLIENT_ID, ANGEL_PASSWORD, ANGEL_TOTP_SECRET,
    MAX_TICK_AGE_MS,
    validate_config
)
from app.angel_auth import AngelAuth
from app.instrument_manager import InstrumentManager
from app.market_data_cache import MarketDataCache
from app.angelone_marketdata import AngelOneWebSocket
from app.database import Database
from app.portfolio import Portfolio
from app.charge_engine import ChargeEngine
from app.position_manager import PositionManager
from app.paper_engine import PaperEngine
from app.signal_parser import SignalParser
from app.commodity_signal_parser import CommoditySignalParser
from app.local_llm_parser import LocalLLMParser
from app.candle_checker import check_recent_target_reached
from app.real_order_manager import RealOrderManager
from app.reply_action_validator import validate_reply_price_update

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def run_system():
    # 0. Validate Environment Config
    validate_config()

    # 6. Initialize Telegram Clients
    client = TelegramClient(TELEGRAM_SESSION_NAME, TELEGRAM_API_ID, TELEGRAM_API_HASH)
    bot_client = TelegramClient('bot_session', TELEGRAM_API_ID, TELEGRAM_API_HASH) if TELEGRAM_BOT_TOKEN else None
    loop = asyncio.get_running_loop()
    
    # Track the admin (user) ID dynamically
    admin_id = [None]

    def on_trade_callback(msg: str, signal_data: dict):
        if not bot_client:
            return
            
        # Determine trade category
        trade_category = signal_data.get("segment", "equity")
            
        # Get active subscribers
        now_utc = datetime.now(timezone.utc)
        today_str = datetime.now(IST).date().isoformat()
        all_subs = db.fetch_all("SELECT user_id, allowed_segments, skip_date, expiry_date FROM subscribers")
        
        valid_subs = []
        for sub in all_subs:
            try:
                exp_dt = datetime.fromisoformat(sub["expiry_date"])
                if exp_dt.tzinfo is None:
                    exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                if exp_dt > now_utc:
                    valid_subs.append(sub)
            except Exception:
                pass # Invalid date format, treat as expired

        async def broadcast():
            # Always send to the admin too
            if admin_id[0]:
                try:
                    await bot_client.send_message(admin_id[0], msg)
                except Exception:
                    pass
            for sub in valid_subs:
                # Check skip date
                if sub["skip_date"] == today_str:
                    continue
                    
                # Check segment permission
                allowed = sub["allowed_segments"] or "equity,commodity_futures,commodity_options,hero_zero"
                if trade_category not in allowed:
                    continue # Skip this user
                    
                try:
                    await bot_client.send_message(sub["user_id"], msg)
                except Exception as e:
                    logger.error(f"Broadcast failed for {sub['user_id']}: {e}")
                    
        asyncio.run_coroutine_threadsafe(broadcast(), loop)

    # 1. Initialize DB and State
    db = Database()
    portfolio = Portfolio(db)
    charge_engine = ChargeEngine()
    
    # 2. Authenticate Angel One
    auth = AngelAuth()
    if not auth.login():
        logger.error("Angel One login failed. Exiting.")
        sys.exit(1)

    real_order_manager = RealOrderManager(auth, db)
    logger.info(f"Execution Mode: {'🔴 REAL LIVE TRADING' if real_order_manager.is_real_trading_enabled else '🟢 PAPER TRADING SIMULATION'}")

    pos_manager = PositionManager(db, portfolio, charge_engine, on_trade_callback=on_trade_callback, real_order_manager=real_order_manager)
        
    # 3. Load Instruments
    inst_manager = InstrumentManager()
    inst_manager.load_instruments()
    pos_manager.load_active_state(inst_manager)
    logger.info(f"Restored active state: {len(pos_manager.active_signals)} watching signal(s), {len(pos_manager.active_positions)} open position(s)")
    
    # 4. Connect WebSocket
    md_cache = MarketDataCache()
    ws = AngelOneWebSocket(auth, md_cache)
    ws.connect()
    
    # Wait for ws to connect
    await asyncio.sleep(3)
    
    # 5. Start Paper Engine in a background thread or async task
    # Since PaperEngine has a blocking while loop, we should run it in a thread
    import threading
    engine = PaperEngine(md_cache, pos_manager, ws)
    engine_thread = threading.Thread(target=engine.start, daemon=True)
    engine_thread.start()
    
    # 6. Start Telegram Client (already initialized above)
    
    listen_chats = []
    if TELEGRAM_CHANNEL_ID: listen_chats.append(int(TELEGRAM_CHANNEL_ID))
    if COMMODITY_CHANNEL_ID: listen_chats.append(int(COMMODITY_CHANNEL_ID))
    
    @client.on(events.NewMessage(chats=listen_chats))
    async def new_message_handler(event):
        # Prevent processing outside of 08:55 to 23:00 IST
        now_ist = datetime.now(IST)
        current_minutes = now_ist.hour * 60 + now_ist.minute
        start_minutes = 8 * 60 + 55
        end_minutes = 23 * 60 + 0
        if current_minutes < start_minutes or current_minutes >= end_minutes:
            logger.info("Market is closed (Outside 08:55 - 23:00 IST). Ignoring incoming message.")
            return

        msg_text = event.message.message
        msg_id = event.message.id
        chat_id = event.chat_id
        
        logger.info(f"New Telegram Message [{msg_id}] in chat {chat_id}:\n{msg_text}")
        
        if COMMODITY_CHANNEL_ID and chat_id == int(COMMODITY_CHANNEL_ID):
            signals = CommoditySignalParser.parse_all(msg_text)
            for s in signals:
                s.segment = "commodity_options" if s.strike and s.option_type else "commodity_futures"
        else:
            single = SignalParser.parse(msg_text)
            if single:
                single.segment = "hero_zero" if "#HERO2ZERO" in msg_text.upper() else "equity"
                signals = [single]
            else:
                signals = []
                
        if not signals:
            if event.message.is_reply:
                reply_to_msg_id = event.message.reply_to_msg_id
                # Fetch ALL signals from this message (could be paired dual-direction)
                signal_rows = db.fetch_all(
                    """SELECT id, segment, status, trade_type, entry_low, entry_high, target_1, stop_loss
                       FROM signals WHERE telegram_message_id = ? AND channel_id = ?""",
                    (reply_to_msg_id, chat_id),
                )
                if signal_rows:
                    action_dict = await asyncio.to_thread(LocalLLMParser.parse_reply, msg_text)
                    logger.info(f"Parsed action from reply: {action_dict}")
                    
                    action = action_dict.get("action", "IGNORE")
                    price = action_dict.get("price")
                    
                    broadcast_msg = None
                    for signal_row in signal_rows:
                        signal_id = signal_row["id"]
                        sig_segment = signal_row.get("segment", "equity")
                        logger.info(f"Message is a reply to our active signal {signal_id}. Applying action: {action}")
                    
                        if action == "EXIT_MARKET":
                            # If open, exit. If pending, cancel.
                            pos_row = db.fetch_one("SELECT id, token, symbol, entry_price FROM positions WHERE signal_id = ? AND status = 'OPEN'", (signal_id,))
                            if pos_row:
                                pos_id = pos_row["id"]
                                token = str(pos_row["token"])
                                symbol = pos_row["symbol"]
                                
                                # Fetch live REST CMP first
                                live_cmp = None
                                try:
                                    if auth and auth.api:
                                        exch_seg = "MCX" if "commodity" in sig_segment else ("BFO" if symbol.startswith("SENSEX") or symbol.startswith("BANKEX") else "NFO")
                                        res = auth.api.ltpData(exch_seg, symbol, token)
                                        if res and res.get("status") and res.get("data"):
                                            live_cmp = float(res["data"]["ltp"])
                                except Exception as e:
                                    logger.warning(f"Could not fetch live REST CMP for {symbol}: {e}")

                                cached_cmp = md_cache.get_ltp(token, max_age_ms=MAX_TICK_AGE_MS)
                                exit_price = live_cmp if live_cmp is not None and live_cmp > 0 else cached_cmp

                                if exit_price is not None and exit_price > 0:
                                    logger.info(f"Executing manual EXIT for {symbol} at {exit_price} based on Telegram reply.")
                                    if pos_manager.execute_exit(pos_id, exit_price, "MANUAL_EXIT"):
                                        ws.unsubscribe(token)
                                    else:
                                        # A valid quote does not mean the ledger commit succeeded.
                                        # Queue the request so the engine retries after DB recovery.
                                        import json
                                        db.execute(
                                            "INSERT INTO commands (command, payload, status, created_at) VALUES (?, ?, 'PENDING', ?)",
                                            (
                                                "CLOSE_POSITION",
                                                json.dumps({"pos_id": pos_id}),
                                                datetime.now(timezone.utc).isoformat(),
                                            ),
                                        )
                                        logger.error(
                                            "Manual EXIT for %s could not be committed; queued for retry.",
                                            symbol,
                                        )
                                else:
                                    # Preserve the user's exit request and let the engine retry
                                    # when a fresh quote is available; never substitute entry price.
                                    import json
                                    db.execute(
                                        "INSERT INTO commands (command, payload, status, created_at) VALUES (?, ?, 'PENDING', ?)",
                                        (
                                            "CLOSE_POSITION",
                                            json.dumps({"pos_id": pos_id}),
                                            datetime.now(timezone.utc).isoformat(),
                                        ),
                                    )
                                    logger.warning(
                                        "Manual EXIT for %s queued for retry: no fresh REST/WebSocket price is available.",
                                        symbol,
                                    )
                            else:
                                # An EXIT reply cancels only a signal that is still waiting to enter.
                                if signal_row.get("status") == "WATCHING_ENTRY":
                                    pos_manager.cancel_pending_signal(signal_id, "MANUAL_CANCEL")
                                else:
                                    logger.warning(
                                        "EXIT_MARKET reply ignored for %s: no open position or pending entry exists.",
                                        signal_id,
                                    )

                        elif action == "CANCEL":
                            # CANCEL must never alter an already-entered/closed signal.
                            if signal_row.get("status") == "WATCHING_ENTRY":
                                pos_manager.cancel_pending_signal(signal_id, "MANUAL_CANCEL")
                            else:
                                logger.warning(
                                    "CANCEL reply ignored for %s because it is no longer pending.",
                                    signal_id,
                                )
                                if not broadcast_msg:
                                    broadcast_msg = (
                                        "⚠️ This trade is already entered or completed. "
                                        "Use an explicit EXIT request to close an open position."
                                    )

                        elif action in ["MODIFY_TARGET", "MODIFY_SL"] and price is not None:
                            # Only pending signals and open positions may be adjusted.
                            pos_row = db.fetch_one(
                                """SELECT id, symbol, entry_price, target_1, stop_loss, trade_type
                                   FROM positions WHERE signal_id = ? AND status = 'OPEN'""",
                                (signal_id,),
                            )
                            valid, reason = validate_reply_price_update(
                                action, price, signal_row, pos_row
                            )
                            if not valid:
                                logger.warning(
                                    "Rejected %s reply for signal %s (price=%r): %s",
                                    action, signal_id, price, reason,
                                )
                                if not broadcast_msg:
                                    broadcast_msg = f"⚠️ Update rejected: {reason}."
                                continue

                            price = float(price)
                            field = "target_1" if action == "MODIFY_TARGET" else "stop_loss"
                            try:
                                # Update signal and open position in one SQLite transaction.
                                with db.transaction() as conn:
                                    signal_update = conn.execute(
                                        f"UPDATE signals SET {field} = ? WHERE id = ? AND status IN ('WATCHING_ENTRY', 'ENTERED')",
                                        (price, signal_id),
                                    )
                                    if signal_update.rowcount != 1:
                                        raise RuntimeError("Signal is no longer pending or entered")
                                    if pos_row:
                                        position_update = conn.execute(
                                            f"UPDATE positions SET {field} = ? WHERE id = ? AND status = 'OPEN'",
                                            (price, pos_row["id"]),
                                        )
                                        if position_update.rowcount != 1:
                                            raise RuntimeError("Position is no longer open")
                            except Exception:
                                logger.exception("Could not commit %s update for signal %s", action, signal_id)
                                if not broadcast_msg:
                                    broadcast_msg = "⚠️ Update could not be saved. No in-memory values were changed."
                                continue

                            with pos_manager._state_lock:
                                if signal_id in pos_manager.active_signals:
                                    pos_manager.active_signals[signal_id][field] = price
                                if pos_row and pos_row["id"] in pos_manager.active_positions:
                                    pos_manager.active_positions[pos_row["id"]][field] = price

                            symbol_name = pos_row["symbol"] if pos_row else "Pending Trade"
                            if not broadcast_msg:
                                broadcast_msg = f"⚠️ UPDATE: {symbol_name}\n"
                                broadcast_msg += f"New Target: {price}" if action == "MODIFY_TARGET" else f"New Stop Loss: {price}"
                    
                    # 3. Broadcast the update (once)
                    if broadcast_msg and admin_id[0]:
                        try:
                            asyncio.create_task(bot_client.send_message(admin_id[0], broadcast_msg))
                        except: pass
                    
                    return

            logger.info("Message did not match signal format and is not a relevant reply. Ignored.")
            return
        
        # Register all parsed signals (1 for single, 2 for dual-direction)
        registered_ids = []
        for signal in signals:
            signal.telegram_message_id = msg_id
            signal.channel_id = chat_id
            
            # Resolve instrument
            inst, error = inst_manager.resolve_signal(signal)
            if not inst:
                logger.warning(f"Could not resolve instrument: {error}")
                continue
                
            # Pre-call Target Check: Did price already touch or cross Target 1 in the last 3 minutes?
            exchange = inst.get("exch_seg", "NFO")
            await asyncio.to_thread(auth.refresh_if_needed)
            target_check = await asyncio.to_thread(
                check_recent_target_reached,
                auth.api,
                exchange,
                inst["token"],
                signal.target_1,
                3,
                signal.trade_type.value,
            )
            if target_check is None:
                logger.error(
                    "Cannot validate recent target history for %s; skipping signal instead of assuming it is safe.",
                    inst["symbol"],
                )
                continue
            if target_check:
                logger.warning(f"Target 1 ({signal.target_1}) was already reached in the last 3 minutes for {inst['symbol']}. Ignoring trade.")
                continue

            # For the second signal in a pair, link it to the first
            paired_id = registered_ids[0] if registered_ids else None
            registered_id = pos_manager.register_signal(signal, inst, paired_signal_id=paired_id)
            if registered_id:
                registered_ids.append(registered_id)
                logger.info(f"Signal successfully parsed, resolved, and registered: {inst['symbol']}")
            else:
                logger.info(f"Signal for {inst['symbol']} was skipped (duplicate or invalid).")
        
        # If we registered 2 paired signals, cross-link the first to the second
        if len(registered_ids) == 2:
            with pos_manager._state_lock:
                if registered_ids[0] in pos_manager.active_signals:
                    pos_manager.active_signals[registered_ids[0]]["_paired_signal_id"] = registered_ids[1]
            logger.info(f"Paired signals linked: {registered_ids[0]} <-> {registered_ids[1]}")
        
        if registered_ids:
            engine.sync_subscriptions()

    logger.info("Starting Telegram listener...")
    await client.start()
    
    me = await client.get_me()
    admin_id[0] = me.id
    
    if bot_client:
        await bot_client.start(bot_token=TELEGRAM_BOT_TOKEN)
        
        @bot_client.on(events.NewMessage(pattern=r'^/start$'))
        async def bot_start(event):
            sender = await event.get_sender()
            uid = event.chat_id
            username = sender.username or ""
            first_name = sender.first_name or ""
            
            # Upsert into subscribers (if not exists)
            row = db.fetch_one("SELECT * FROM subscribers WHERE user_id = ?", (uid,))
            if not row:
                now_str = datetime.now(timezone.utc).isoformat()
                # Default to an expired date, so they don't get calls until admin adds them
                db.execute("INSERT INTO subscribers (user_id, username, first_name, expiry_date, created_at) VALUES (?, ?, ?, ?, ?)",
                          (uid, username, first_name, "2000-01-01T00:00:00+00:00", now_str))
                          
            await event.respond(
                f"Welcome to KB4Trade Premium Calls!\n\n"
                f"Your User ID is: `{uid}`\n\n"
                f"Please send this ID to the admin to activate your subscription."
            )
            
        @bot_client.on(events.NewMessage(pattern=r'^/add\s+(\d+)\s+(\d+)$'))
        async def bot_add(event):
            if event.chat_id != admin_id[0]:
                return # Only admin can do this
            
            user_id = int(event.pattern_match.group(1))
            days = int(event.pattern_match.group(2))
            
            now_utc = datetime.now(timezone.utc)
            
            # Get current expiry or now
            row = db.fetch_one("SELECT expiry_date FROM subscribers WHERE user_id = ?", (user_id,))
            if not row:
                await event.respond("User not found in database. They must click /start first.")
                return
                
            current_expiry_str = row["expiry_date"]
            try:
                current_expiry = datetime.fromisoformat(current_expiry_str)
            except:
                current_expiry = now_utc
                
            # If already expired, start from today
            if current_expiry < now_utc:
                new_expiry = now_utc + timedelta(days=days)
            else:
                new_expiry = current_expiry + timedelta(days=days)
                
            db.execute("UPDATE subscribers SET expiry_date = ? WHERE user_id = ?", (new_expiry.isoformat(), user_id))
            
            await event.respond(f"✅ Subscription extended for {user_id} by {days} days!\nNew Expiry: {new_expiry.strftime('%Y-%m-%d %H:%M UTC')}")
            
            # Notify the user
            try:
                await bot_client.send_message(user_id, f"🎉 Your subscription has been activated/extended by {days} days!\nEnjoy the calls!")
            except:
                pass
                
        logger.info("Bot Client is running...")
        
        # Start background loop for subscription notifications
        async def subscription_manager_loop():
            last_check_time = datetime.now(timezone.utc)
            last_known_expiry = {}
            
            # Populate initial state so we don't trigger alerts on boot
            subs = db.fetch_all("SELECT user_id, expiry_date FROM subscribers")
            for sub in subs:
                try:
                    last_known_expiry[sub["user_id"]] = datetime.fromisoformat(sub["expiry_date"])
                except:
                    pass

            while True:
                await asyncio.sleep(30) # check every 30 seconds
                try:
                    now_utc = datetime.now(timezone.utc)
                    current_subs = db.fetch_all("SELECT user_id, expiry_date FROM subscribers")
                    
                    for sub in current_subs:
                        uid = sub["user_id"]
                        try:
                            exp = datetime.fromisoformat(sub["expiry_date"])
                        except:
                            continue
                            
                        # 1. Detect Extension (Expiry date changed in DB)
                        prev_exp = last_known_expiry.get(uid)
                        if prev_exp and exp > prev_exp and exp > now_utc:
                            ist_time = exp.astimezone(timezone(timedelta(hours=5, minutes=30))).strftime('%d %b %Y, %I:%M %p')
                            msg = f"🎉 **Subscription Activated!**\n\nYou will now receive live alerts.\n📅 **Expiry:** {ist_time} IST"
                            try:
                                await bot_client.send_message(uid, msg)
                            except Exception:
                                pass
                        
                        # 2. Detect Natural Expiry (Clock passed the expiry date)
                        if exp > last_check_time and exp <= now_utc:
                            msg = f"⚠️ **Subscription Expired!**\n\nYour premium plan has ended. You will no longer receive live trade alerts. Please contact the admin to renew."
                            try:
                                await bot_client.send_message(uid, msg)
                            except Exception:
                                pass
                                
                        last_known_expiry[uid] = exp
                        
                    last_check_time = now_utc
                except Exception as e:
                    logger.error(f"Sub manager error: {e}")
                    
        loop.create_task(subscription_manager_loop())
        
    logger.info("System is now RUNNING. Press Ctrl+C to stop.")
    
    # Wait until disconnected
    if bot_client:
        await asyncio.gather(
            client.run_until_disconnected(),
            bot_client.run_until_disconnected()
        )
    else:
        await client.run_until_disconnected()

if __name__ == '__main__':
    try:
        asyncio.run(run_system())
    except KeyboardInterrupt:
        logger.info("Shutting down...")
