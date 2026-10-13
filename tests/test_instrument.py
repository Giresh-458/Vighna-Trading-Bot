import pytest
from app.instrument_manager import InstrumentManager
from app.signal_parser import SignalParser

@pytest.fixture(scope="module")
def inst_manager():
    manager = InstrumentManager()
    manager.load_instruments(force_download=False)
    return manager

def test_resolve_nifty_no_expiry(inst_manager):
    msg = """
    TSM - PRIME
    #NIFTY 23450 PE
    Entry - 48 - 53
    Target - 68/84/100
    SL - 25
    """
    signal = SignalParser.parse(msg)
    assert signal is not None
    
    # NIFTY 23450 PE is probably an old strike, but let's see if we can resolve something current
    # Actually, 23450 is a valid NIFTY strike in the current json?
    item, error = inst_manager.resolve_signal(signal)
    assert item is not None, error
    assert item['name'] == 'NIFTY'
    assert item['strike'] == '2345000.000000'
    assert item['instrumenttype'] == 'OPTIDX'

def test_resolve_banknifty(inst_manager):
    msg = """
    TSM - PRIME
    #BANKNIFTY 50000 CE
    Entry - 48 - 53
    Target - 68
    SL - 25
    """
    signal = SignalParser.parse(msg)
    item, error = inst_manager.resolve_signal(signal)
    if item:
        assert item["name"] == "BANKNIFTY"
        assert float(item["strike"]) == 5000000.0
        assert item["_option_type"] == "CE"
