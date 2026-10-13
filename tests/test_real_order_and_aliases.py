import pytest
from unittest.mock import MagicMock
from app.real_order_manager import RealOrderManager
import app.real_order_manager as real_order_manager_module
from app.instrument_manager import InstrumentManager
from app.models import ParsedSignal, OptionType, TradeType

def test_real_order_manager_paper_safety():
    # When PAPER_ONLY is True or APP_ENV is paper, no live order should be placed
    mock_auth = MagicMock()
    mock_db = MagicMock()
    rom = RealOrderManager(mock_auth, mock_db)
    
    # In paper mode, it should safely return None without invoking broker API
    res = rom.place_entry_order(
        signal_id="sig-1",
        instrument={"symbol": "NIFTY29SEP2623250CE", "token": "73921", "exch_seg": "NFO"},
        trade_type="LONG",
        quantity=75,
        ltp=120.0,
        segment="equity"
    )
    assert res is None
    assert mock_auth.api.placeOrder.call_count == 0

def test_bfo_live_exit_refuses_stale_price_instead_of_using_default(monkeypatch):
    from datetime import datetime, timedelta, timezone

    monkeypatch.setattr(real_order_manager_module, "PAPER_ONLY", False)
    monkeypatch.setattr(real_order_manager_module, "APP_ENV", "real")
    auth = MagicMock()
    db = MagicMock()
    old_time = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    db.fetch_one.return_value = {"ltp": 100.0, "updated_at": old_time}
    manager = RealOrderManager(auth, db)

    result = manager.place_exit_order(
        pos_id="position-1",
        symbol="SENSEX13OCT2668000CE",
        token="12345",
        trade_type="LONG",
        quantity=20,
        segment="hero_zero",
        reason="MANUAL_EXIT",
    )

    assert result is None
    auth.api._postRequest.assert_not_called()


def test_marketable_limit_price_calculation():
    mock_auth = MagicMock()
    mock_db = MagicMock()
    rom = RealOrderManager(mock_auth, mock_db)

    # BUY at 100 with tick 0.05 -> 100 * 1.03 = 103.00
    buy_price = rom._calculate_limit_price(100.0, "BUY", 5.0)
    assert buy_price == "103.00"

    # SELL at 100 with tick 0.05 -> 100 * 0.97 = 97.00
    sell_price = rom._calculate_limit_price(100.0, "SELL", 5.0)
    assert sell_price == "97.00"

def test_instrument_manager_aliases():
    im = InstrumentManager()
    # Mock options map
    im.options_map = {
        "CRUDEOIL": {
            6800.0: {"CE": [{"symbol": "CRUDEOIL15OCT266800CE", "expiry": "15OCT2026", "_parsed_expiry": "2026-10-15"}]}
        },
        "NATURALGAS": {
            290.0: {"CE": [{"symbol": "NATURALGAS26OCT26290CE", "expiry": "26OCT2026", "_parsed_expiry": "2026-10-26"}]}
        }
    }
    im.is_loaded = True

    # Test 'CRUDE' alias resolves to CRUDEOIL
    sig_crude = ParsedSignal(
        underlying="CRUDE",
        strike=6800.0,
        option_type=OptionType.CE,
        entry_low=100.0,
        entry_high=105.0,
        target_1=120.0,
        stop_loss=80.0
    )
    inst, err = im.resolve_signal(sig_crude)
    assert inst is not None
    assert inst["symbol"] == "CRUDEOIL15OCT266800CE"

    # Test 'NATGAS' alias resolves to NATURALGAS
    sig_natgas = ParsedSignal(
        underlying="NATGAS",
        strike=290.0,
        option_type=OptionType.CE,
        entry_low=10.0,
        entry_high=12.0,
        target_1=15.0,
        stop_loss=8.0
    )
    inst, err = im.resolve_signal(sig_natgas)
    assert inst is not None
    assert inst["symbol"] == "NATURALGAS26OCT26290CE"
