import pytest
from datetime import date, timedelta
from app.instrument_manager import InstrumentManager
from app.signal_parser import SignalParser


def make_option(token, underlying, strike, option_type, days):
    expiry_date = date.today() + timedelta(days=days)
    expiry = expiry_date.strftime("%d%b%Y").upper()
    return {
        "exch_seg": "NFO",
        "instrumenttype": "OPTIDX" if underlying in {"NIFTY", "BANKNIFTY"} else "OPTSTK",
        "symbol": f"{underlying}{expiry}{strike}{option_type}",
        "name": underlying,
        "strike": f"{strike * 100:.6f}",
        "token": token,
        "expiry": expiry,
        "lotsize": "75",
    }


@pytest.fixture
def inst_manager():
    manager = InstrumentManager()
    manager._build_indexes([
        make_option("10001", "NIFTY", 23450, "PE", 30),
        make_option("10002", "BANKNIFTY", 50000, "CE", 60),
    ])
    manager.is_loaded = True
    return manager


def test_resolve_nifty_no_expiry(inst_manager):
    signal = SignalParser.parse("""
    TSM - PRIME
    #NIFTY 23450 PE
    Entry - 48 - 53
    Target - 68/84/100
    SL - 25
    """)
    assert signal is not None
    item, error = inst_manager.resolve_signal(signal)
    assert item is not None, error
    assert item["name"] == "NIFTY"
    assert item["strike"] == "2345000.000000"
    assert item["instrumenttype"] == "OPTIDX"


def test_resolve_banknifty(inst_manager):
    signal = SignalParser.parse("""
    TSM - PRIME
    #BANKNIFTY 50000 CE
    Entry - 48 - 53
    Target - 68
    SL - 25
    """)
    item, error = inst_manager.resolve_signal(signal)
    assert item is not None, error
    assert item["name"] == "BANKNIFTY"
    assert float(item["strike"]) == 5000000.0
    assert item["_option_type"] == "CE"
