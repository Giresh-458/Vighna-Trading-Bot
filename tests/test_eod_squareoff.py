import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone, timedelta
from app.paper_engine import PaperEngine

IST = timezone(timedelta(hours=5, minutes=30))

def test_is_eod_false_during_market_hours():
    mock_md = MagicMock()
    mock_pos = MagicMock()
    mock_ws = MagicMock()
    engine = PaperEngine(mock_md, mock_pos, mock_ws)
    
    # 10:30 AM IST
    mock_time = datetime(2026, 9, 24, 10, 30, tzinfo=IST)
    with patch("app.paper_engine.datetime") as mock_dt:
        mock_dt.now.return_value = mock_time
        assert engine.is_eod() is False

def test_is_eod_true_at_or_after_15_15():
    mock_md = MagicMock()
    mock_pos = MagicMock()
    mock_ws = MagicMock()
    engine = PaperEngine(mock_md, mock_pos, mock_ws)
    
    # 3:15 PM IST (15:15)
    mock_time = datetime(2026, 9, 24, 15, 15, tzinfo=IST)
    with patch("app.paper_engine.datetime") as mock_dt:
        mock_dt.now.return_value = mock_time
        assert engine.is_eod() is True
        
    # 3:20 PM IST (15:20)
    mock_time_after = datetime(2026, 9, 24, 15, 20, tzinfo=IST)
    with patch("app.paper_engine.datetime") as mock_dt:
        mock_dt.now.return_value = mock_time_after
        assert engine.is_eod() is True

def test_check_eod_squareoff_closes_positions_and_expires_signals():
    mock_md = MagicMock()
    mock_pos = MagicMock()
    mock_ws = MagicMock()
    engine = PaperEngine(mock_md, mock_pos, mock_ws)
    
    # Set up active position
    mock_pos.active_positions = {
        "pos_1": {
            "token": "73921",
            "symbol": "NIFTY29SEP2623250CE",
            "entry_price": 115.0
        }
    }
    mock_md.get_raw_tick.return_value = {"ltp": 125.0}
    mock_md.get_ltp.return_value = 125.0
    
    # Set up active signal
    mock_pos.active_signals = {
        "sig_1": {
            "_instrument": {"token": "86456"}
        }
    }
    
    # Force is_eod to True
    with patch.object(engine, "is_eod", return_value=True):
        engine.check_eod_squareoff()
        
    # Verify position was closed with reason EOD_CLOSED
    mock_pos.execute_exit.assert_called_once_with("pos_1", 125.0, "EOD_CLOSED")
    mock_ws.unsubscribe.assert_any_call("73921")
    
    # Verify signal was marked expired with EOD_CLOSED
    mock_pos.mark_signal_expired.assert_called_once_with("sig_1", "EOD_CLOSED")
    mock_ws.unsubscribe.assert_any_call("86456")
