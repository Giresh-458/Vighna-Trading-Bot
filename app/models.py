from enum import Enum
from typing import Optional
from pydantic import BaseModel

class OptionType(str, Enum):
    CE = "CE"
    PE = "PE"

class TradeType(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"

class SignalStatus(str, Enum):
    RECEIVED = "RECEIVED"
    PARSING = "PARSING"
    VALIDATED = "VALIDATED"
    WATCHING_ENTRY = "WATCHING_ENTRY"
    ENTERED = "ENTERED"
    TARGET_HIT = "TARGET_HIT"
    SL_HIT = "SL_HIT"
    ENTRY_MISSED = "ENTRY_MISSED"
    EXPIRED = "EXPIRED"
    EXPIRED_TARGET_REACHED = "EXPIRED_TARGET_REACHED"
    EOD_CLOSED = "EOD_CLOSED"
    INVALID = "INVALID"
    DUPLICATE = "DUPLICATE"
    INSUFFICIENT_CAPITAL = "INSUFFICIENT_CAPITAL"
    NO_MARKET_DATA = "NO_MARKET_DATA"
    INSTRUMENT_NOT_FOUND = "INSTRUMENT_NOT_FOUND"
    ERROR = "ERROR"


class ParsedSignal(BaseModel):
    underlying: str
    trade_type: TradeType = TradeType.LONG
    strike: Optional[float] = None
    option_type: Optional[OptionType] = None
    expiry: Optional[str] = None
    entry_low: float
    entry_high: float
    target_1: float
    target_2: Optional[float] = None
    target_3: Optional[float] = None
    stop_loss: float
    
    requested_lot_range: Optional[str] = None
    note: Optional[str] = None

    telegram_message_id: Optional[int] = None
    channel_id: Optional[int] = None
    segment: str = "equity"
