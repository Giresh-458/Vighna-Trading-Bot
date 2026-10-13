import pytest
import time
from unittest.mock import MagicMock
from app.position_manager import PositionManager
from app.models import ParsedSignal, OptionType, SignalStatus
from app.angelone_marketdata import AngelOneWebSocket
from app.market_data_cache import MarketDataCache

@pytest.fixture
def mock_db():
    db = MagicMock()
    db.fetch_one.return_value = None
    db.fetch_all.return_value = []
    db.execute.return_value = 1
    return db

@pytest.fixture
def mock_portfolio():
    p = MagicMock()
    p.has_sufficient_margin.return_value = True
    return p

@pytest.fixture
def mock_charges():
    c = MagicMock()
    return c

def test_duplicate_signal_rejection(mock_db, mock_portfolio, mock_charges):
    pos_manager = PositionManager(mock_db, mock_portfolio, mock_charges)
    
    signal = ParsedSignal(
        underlying="NIFTY",
        strike=23250.0,
        option_type=OptionType.CE,
        entry_low=115.0,
        entry_high=120.0,
        target_1=135.0,
        stop_loss=90.0,
        telegram_message_id=12345
    )
    inst = {"token": "73921", "symbol": "NIFTY29SEP2623250CE", "lotsize": 65, "expiry": "29SEP2026"}
    
    # 1. First registration succeeds
    mock_db.fetch_one.return_value = None
    sig_id = pos_manager.register_signal(signal, inst)
    assert sig_id is not None
    
    # 2. Second registration with same telegram_message_id is rejected
    mock_db.fetch_one.return_value = {"id": sig_id}
    dup_id = pos_manager.register_signal(signal, inst)
    assert dup_id is None

def test_duplicate_token_rejection(mock_db, mock_portfolio, mock_charges):
    pos_manager = PositionManager(mock_db, mock_portfolio, mock_charges)
    
    signal1 = ParsedSignal(
        underlying="NIFTY",
        strike=23250.0,
        option_type=OptionType.CE,
        entry_low=115.0,
        entry_high=120.0,
        target_1=135.0,
        stop_loss=90.0,
        telegram_message_id=101
    )
    inst = {"token": "73921", "symbol": "NIFTY29SEP2623250CE", "lotsize": 65, "expiry": "29SEP2026"}
    
    mock_db.fetch_one.return_value = None
    pos_manager.register_signal(signal1, inst)
    
    # Same contract, different message ID
    signal2 = ParsedSignal(
        underlying="NIFTY",
        strike=23250.0,
        option_type=OptionType.CE,
        entry_low=115.0,
        entry_high=120.0,
        target_1=135.0,
        stop_loss=90.0,
        telegram_message_id=102
    )
    dup_id = pos_manager.register_signal(signal2, inst)
    assert dup_id is None

def test_websocket_threadsafe_set():
    auth_tokens = {"jwtToken": "t", "apiKey": "k", "clientId": "c", "feedToken": "f"}
    md_cache = MarketDataCache()
    ws = AngelOneWebSocket(auth_tokens, md_cache)
    
    # Concurrent subscribe & unsubscribe should not raise KeyError or crash
    import threading
    def worker():
        for i in range(100):
            token = str(i % 10)
            ws.subscribe(token)
            ws.unsubscribe(token)
            
    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads: t.start()
    for t in threads: t.join()
    
    assert isinstance(ws.active_tokens, dict)
