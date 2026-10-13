import logging
import uuid
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from app.config import PAPER_ONLY, APP_ENV, MAX_TICK_AGE_MS
from app.market_data_cache import is_fresh_persisted_tick

logger = logging.getLogger(__name__)

class RealOrderManager:
    """
    Handles live order execution via Angel One SmartAPI when real trading is enabled.
    Safely no-ops when PAPER_ONLY=True or APP_ENV != 'real'.
    Includes:
    - BFO limit order handling (BSE rejects MARKET orders on F&O).
    - Marketable limit retry fallback on NFO/MCX if MARKET is rejected.
    - Freeze quantity boundary protection.
    - Consistent CARRYFORWARD product type to prevent broker RMS auto-square off penalties.
    - Precise broker error message recording in SQLite.
    """
    def __init__(self, auth, db):
        self.auth = auth
        self.db = db
        self._init_table()

    def _init_table(self):
        try:
            self.db.execute("""
            CREATE TABLE IF NOT EXISTS real_orders (
                id TEXT PRIMARY KEY,
                order_id TEXT,
                position_id TEXT,
                signal_id TEXT,
                symbol TEXT,
                token TEXT,
                transaction_type TEXT,
                quantity INTEGER,
                order_type TEXT,
                product_type TEXT,
                price REAL,
                status TEXT,
                response_msg TEXT,
                created_at TEXT
            )
            """)
        except Exception as e:
            logger.warning(f"Could not init real_orders table: {e}")

    @property
    def is_real_trading_enabled(self) -> bool:
        return (not PAPER_ONLY) and (APP_ENV == "real")

    def _calculate_limit_price(self, ltp: float, tx_type: str, tick_size_raw: Any = 5.0) -> str:
        """
        Calculates a marketable limit price:
        - 3% above LTP for BUY (ensures immediate fill at best ask)
        - 3% below LTP for SELL (ensures immediate fill at best bid)
        Ticks in Angel master are multiplied by 100 (e.g. 5.0 = 0.05).
        """
        try:
            tick = float(tick_size_raw) / 100.0 if float(tick_size_raw) >= 1.0 else 0.05
            if tick <= 0:
                tick = 0.05
        except Exception:
            tick = 0.05

        if tx_type == "BUY":
            target = ltp * 1.03
            rounded = round(round(target / tick) * tick, 2)
        else:
            target = max(ltp * 0.97, 0.05)
            rounded = round(round(target / tick) * tick, 2)

        return f"{rounded:.2f}"

    def place_entry_order(
        self,
        signal_id: str,
        instrument: dict,
        trade_type: str,
        quantity: int,
        ltp: float,
        segment: str
    ) -> Optional[str]:
        if not self.is_real_trading_enabled:
            logger.info(f"[PAPER MODE] Simulated real entry order for {instrument['symbol']} (Qty: {quantity})")
            return None

        if not self.auth or not self.auth.api:
            logger.error(f"[REAL TRADE ERROR] Angel One API not authenticated. Cannot place order for {instrument['symbol']}")
            return None

        symbol = instrument["symbol"]
        token = str(instrument["token"])
        exch = instrument.get("exch_seg", "NFO")
        freeze_qty = int(instrument.get("freeze_qty", 0) or 0)
        tick_size = instrument.get("tick_size", 5.0)

        # Protect against freeze quantity rejection
        if freeze_qty > 0 and quantity > freeze_qty:
            logger.warning(f"Order quantity {quantity} exceeds freeze limit {freeze_qty}. Clamping to {freeze_qty}.")
            quantity = freeze_qty

        # BUY for LONG, SELL for SHORT
        tx_type = "BUY" if trade_type == "LONG" else "SELL"
        
        # Product type: CARRYFORWARD gives the bot full control and avoids broker MIS square-off charges
        product_type = "CARRYFORWARD"

        # BSE F&O (BFO) forbids MARKET orders entirely; use marketable LIMIT
        is_bfo = (exch == "BFO")
        order_type = "LIMIT" if is_bfo else "MARKET"
        price_str = self._calculate_limit_price(ltp, tx_type, tick_size) if is_bfo else "0"

        order_params = {
            "variety": "NORMAL",
            "tradingsymbol": symbol,
            "symboltoken": token,
            "transactiontype": tx_type,
            "exchange": exch,
            "ordertype": order_type,
            "producttype": product_type,
            "duration": "DAY",
            "price": price_str,
            "squareoff": "0",
            "stoploss": "0",
            "quantity": str(quantity)
        }

        logger.info(f"🚨 [REAL TRADE ENTRY] Placing LIVE {tx_type} order: {symbol} x {quantity} on {exch} ({order_type} @ {price_str})")
        
        record_id = str(uuid.uuid4())
        now_str = datetime.now(timezone.utc).isoformat()

        try:
            # Place order via SmartConnect SDK
            response = self.auth.api._postRequest("api.order.place", order_params)
            
            if response and response.get("status"):
                order_id = str(response.get("data", {}).get("orderid"))
                logger.info(f"✅ [REAL TRADE SUCCESS] Entry order placed! Order ID: {order_id}")
                self.db.execute("""
                INSERT INTO real_orders (id, order_id, signal_id, symbol, token, transaction_type, quantity, order_type, product_type, price, status, response_msg, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PLACED', 'Success', ?)
                """, (record_id, order_id, signal_id, symbol, token, tx_type, quantity, order_type, product_type, ltp, now_str))
                return order_id
            else:
                err_msg = response.get("message", "No order ID returned") if response else "No response"
                logger.error(f"❌ [REAL TRADE FAILED] Entry failed for {symbol}: {err_msg}")
                
                # If market order was rejected due to exchange/RMS market order ban, retry with marketable LIMIT
                if order_type == "MARKET" and ("market" in err_msg.lower() or "rms" in err_msg.lower()):
                    logger.info(f"🔄 Retrying {symbol} as marketable LIMIT order...")
                    order_params["ordertype"] = "LIMIT"
                    order_params["price"] = self._calculate_limit_price(ltp, tx_type, tick_size)
                    retry_res = self.auth.api._postRequest("api.order.place", order_params)
                    if retry_res and retry_res.get("status"):
                        order_id = str(retry_res.get("data", {}).get("orderid"))
                        logger.info(f"✅ [REAL TRADE SUCCESS] Entry retry succeeded! Order ID: {order_id}")
                        self.db.execute("""
                        INSERT INTO real_orders (id, order_id, signal_id, symbol, token, transaction_type, quantity, order_type, product_type, price, status, response_msg, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, 'LIMIT', ?, ?, 'PLACED', 'Success (Limit Retry)', ?)
                        """, (record_id, order_id, signal_id, symbol, token, tx_type, quantity, product_type, ltp, now_str))
                        return order_id
                    else:
                        err_msg = retry_res.get("message", err_msg) if retry_res else err_msg

                self.db.execute("""
                INSERT INTO real_orders (id, order_id, signal_id, symbol, token, transaction_type, quantity, order_type, product_type, price, status, response_msg, created_at)
                VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, 'FAILED', ?, ?)
                """, (record_id, signal_id, symbol, token, tx_type, quantity, order_type, product_type, ltp, err_msg, now_str))
                return None

        except Exception as e:
            logger.exception(f"❌ [REAL TRADE EXCEPTION] Exception placing order for {symbol}: {e}")
            return None

    def place_exit_order(
        self,
        pos_id: str,
        symbol: str,
        token: str,
        trade_type: str,
        quantity: int,
        segment: str,
        reason: str
    ) -> Optional[str]:
        if not self.is_real_trading_enabled:
            logger.info(f"[PAPER MODE] Simulated real exit order for {symbol} (Qty: {quantity}, Reason: {reason})")
            return None

        if not self.auth or not self.auth.api:
            logger.error(f"[REAL TRADE ERROR] Angel One API not authenticated. Cannot place exit order for {symbol}")
            return None

        # Opposite of entry: SELL for LONG, BUY for SHORT
        tx_type = "SELL" if trade_type == "LONG" else "BUY"
        exch = "MCX" if "commodity" in (segment or "") else ("BFO" if symbol.startswith("SENSEX") or symbol.startswith("BANKEX") else "NFO")
        product_type = "CARRYFORWARD"

        is_bfo = (exch == "BFO")
        order_type = "LIMIT" if is_bfo else "MARKET"

        # BFO exits require a genuinely fresh persisted WebSocket quote. Never submit
        # a made-up limit price or trust an old quote if current data is unavailable.
        price_str = "0"
        if is_bfo:
            try:
                row = self.db.fetch_one(
                    "SELECT ltp, updated_at FROM live_ticks WHERE token = ?", (str(token),)
                )
                if not row or not is_fresh_persisted_tick(row.get("updated_at"), MAX_TICK_AGE_MS):
                    logger.error(
                        "Refusing BFO live exit for %s: no fresh quote is available.",
                        symbol,
                    )
                    return None
                exit_ltp = float(row.get("ltp") or 0.0)
                if exit_ltp <= 0:
                    logger.error("Refusing BFO live exit for %s: invalid quote.", symbol)
                    return None
                price_str = self._calculate_limit_price(exit_ltp, tx_type)
            except Exception:
                logger.exception("Refusing BFO live exit for %s: quote validation failed.", symbol)
                return None

        order_params = {
            "variety": "NORMAL",
            "tradingsymbol": symbol,
            "symboltoken": str(token),
            "transactiontype": tx_type,
            "exchange": exch,
            "ordertype": order_type,
            "producttype": product_type,
            "duration": "DAY",
            "price": price_str,
            "squareoff": "0",
            "stoploss": "0",
            "quantity": str(quantity)
        }

        logger.info(f"🚨 [REAL TRADE EXIT] Placing LIVE exit {tx_type} order for {symbol} x {quantity} (Reason: {reason}) on {exch}")
        
        record_id = str(uuid.uuid4())
        now_str = datetime.now(timezone.utc).isoformat()

        try:
            response = self.auth.api._postRequest("api.order.place", order_params)
            
            if response and response.get("status"):
                order_id = str(response.get("data", {}).get("orderid"))
                logger.info(f"✅ [REAL TRADE EXIT SUCCESS] Exit order placed! Order ID: {order_id}")
                self.db.execute("""
                INSERT INTO real_orders (id, order_id, position_id, symbol, token, transaction_type, quantity, order_type, product_type, status, response_msg, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'EXIT_PLACED', ?, ?)
                """, (record_id, order_id, pos_id, symbol, str(token), tx_type, quantity, order_type, product_type, reason, now_str))
                return order_id
            else:
                err_msg = response.get("message", "No order ID returned") if response else "No response"
                logger.error(f"❌ [REAL TRADE EXIT FAILED] Failed to place exit order for {symbol}: {err_msg}")
                
                # Retry with marketable limit if market order was rejected
                if order_type == "MARKET":
                    logger.info(f"🔄 Retrying exit for {symbol} as marketable LIMIT...")
                    order_params["ordertype"] = "LIMIT"
                    exit_ltp = 100.0
                    try:
                        row = self.db.fetch_one("SELECT ltp FROM live_ticks WHERE token = ?", (str(token),))
                        if row and row.get("ltp"):
                            exit_ltp = float(row["ltp"])
                    except Exception:
                        pass
                    order_params["price"] = self._calculate_limit_price(exit_ltp, tx_type)
                    retry_res = self.auth.api._postRequest("api.order.place", order_params)
                    if retry_res and retry_res.get("status"):
                        order_id = str(retry_res.get("data", {}).get("orderid"))
                        logger.info(f"✅ [REAL TRADE EXIT SUCCESS] Exit retry succeeded! Order ID: {order_id}")
                        self.db.execute("""
                        INSERT INTO real_orders (id, order_id, position_id, symbol, token, transaction_type, quantity, order_type, product_type, status, response_msg, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, 'LIMIT', ?, 'EXIT_PLACED', ?, ?)
                        """, (record_id, order_id, pos_id, symbol, str(token), tx_type, quantity, product_type, f"{reason} (Limit Retry)", now_str))
                        return order_id

                self.db.execute("""
                INSERT INTO real_orders (id, order_id, position_id, symbol, token, transaction_type, quantity, order_type, product_type, status, response_msg, created_at)
                VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, 'EXIT_FAILED', ?, ?)
                """, (record_id, pos_id, symbol, str(token), tx_type, quantity, order_type, product_type, err_msg, now_str))
                return None

        except Exception as e:
            logger.exception(f"❌ [REAL TRADE EXIT EXCEPTION] Exception placing exit order for {symbol}: {e}")
            return None
