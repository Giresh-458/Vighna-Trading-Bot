import pytest
from app.signal_parser import SignalParser
from app.models import OptionType

def test_parse_nifty_standard():
    msg = """
    TSM - PRIME ☀️
    #INTRADAY

    #NIFTY 23450 PE

    Entry - 48 - 53
    Target - 68/84/100
    SL - 25

    Qty - 1 - 2 Lots
    Note - Close Today
    """
    signal = SignalParser.parse(msg)
    assert signal is not None
    assert signal.underlying == "NIFTY"
    assert signal.strike == 23450
    assert signal.option_type == OptionType.PE
    assert signal.expiry is None
    assert signal.entry_low == 48.0
    assert signal.entry_high == 53.0
    assert signal.target_1 == 68.0
    assert signal.target_2 == 84.0
    assert signal.target_3 == 100.0
    assert signal.stop_loss == 25.0
    assert signal.requested_lot_range == "1 - 2 Lots"
    assert signal.note == "Close Today"

def test_parse_stock_with_expiry():
    msg = """
    TSM - PRIME
    #GODFRYPHLP
    1900 PE SEP

    Entry - 28 - 31
    Target - 35/40/45
    SL - 24

    Qty - 1 - 2 Lots
    Note - Close Today
    """
    signal = SignalParser.parse(msg)
    assert signal is not None
    assert signal.underlying == "GODFRYPHLP"
    assert signal.strike == 1900
    assert signal.option_type == OptionType.PE
    assert signal.expiry == "SEP"
    assert signal.entry_low == 28.0
    assert signal.entry_high == 31.0
    assert signal.target_1 == 35.0
    assert signal.stop_loss == 24.0

def test_parse_stock_with_expiry_techm():
    msg = """
    TSM - PRIME
    #TECHM
    1580 PE SEP

    Entry - 14 - 16
    Target - 18/21/25
    SL - 12

    Qty - 1 - 2 Lots
    Note - Close Today
    """
    signal = SignalParser.parse(msg)
    assert signal is not None
    assert signal.underlying == "TECHM"
    assert signal.strike == 1580
    assert signal.option_type == OptionType.PE
    assert signal.expiry == "SEP"
    assert signal.entry_low == 14.0
    assert signal.entry_high == 16.0
    assert signal.target_1 == 18.0
    assert signal.stop_loss == 12.0

def test_parse_formatting_variations():
    msg = """
    TSM - PRIME
    NIFTY 24000 CE OCT
    Entry: 100-105
    Target: 120 / 140
    SL: 80
    """
    signal = SignalParser.parse(msg)
    assert signal is not None
    assert signal.underlying == "NIFTY"
    assert signal.strike == 24000
    assert signal.option_type == OptionType.CE
    assert signal.expiry == "OCT"
    assert signal.entry_low == 100.0
    assert signal.entry_high == 105.0
    assert signal.target_1 == 120.0
    assert signal.target_2 == 140.0
    assert signal.target_3 is None
    assert signal.stop_loss == 80.0

def test_invalid_messages():
    msg1 = "Just a random chat message with no trade info"
    assert SignalParser.parse(msg1) is None
    
    msg2 = "TSM - PRIME\n#NIFTY 23450 PE\nTarget - 68\nSL - 25" # Missing Entry
    assert SignalParser.parse(msg2) is None
    
    msg3 = "TSM - PRIME\n#NIFTY 23450 PE\nEntry - 48 - 53\nSL - 25" # Missing Target
    assert SignalParser.parse(msg3) is None

    msg4 = "TSM - PRIME\n#NIFTY 23450 PE\nEntry - 48 - 53\nTarget - 68" # Missing SL
    assert SignalParser.parse(msg4) is None

