from datetime import date, datetime, timedelta
from threading import Lock
from unittest.mock import MagicMock, patch

import pytest

import app.config as config
import app.paper_engine as paper_engine_module
from app.instrument_manager import InstrumentManager
from app.models import OptionType, ParsedSignal
from app.paper_engine import PaperEngine
from app.position_manager import PositionManager


def make_signal(message_id=101, channel_id=-1001):
    return ParsedSignal(
        underlying="NIFTY",
        strike=23450,
        option_type=OptionType.CE,
        entry_low=10,
        entry_high=11,
        target_1=15,
        stop_loss=8,
        telegram_message_id=message_id,
        channel_id=channel_id,
    )


def make_instrument(token="10001", lotsize=25):
    return {
        "token": token,
        "symbol": "NIFTY30NOV2623450CE",
        "expiry": "30NOV2026",
        "exch_seg": "NFO",
        "instrumenttype": "OPTIDX",
        "lotsize": str(lotsize),
    }


def test_same_telegram_message_id_is_allowed_in_different_channels():
    db = MagicMock()
    db.fetch_one.side_effect = [None, None, None, None]
    portfolio = MagicMock()
    charges = MagicMock()
    manager = PositionManager(db, portfolio, charges)

    first = make_signal(message_id=777, channel_id=-100111)
    second = make_signal(message_id=777, channel_id=-100222)

    assert manager.register_signal(first, make_instrument("10001")) is not None
    assert manager.register_signal(second, make_instrument("10002")) is not None

    duplicate_queries = [
        call for call in db.fetch_one.call_args_list
        if "telegram_message_id" in call.args[0]
    ]
    assert duplicate_queries[0].args[1] == (777, -100111)
    assert duplicate_queries[1].args[1] == (777, -100222)
    assert all("channel_id = ?" in call.args[0] for call in duplicate_queries)


def test_duplicate_message_in_same_channel_is_rejected():
    db = MagicMock()
    db.fetch_one.side_effect = [None, None, {"id": "already-registered"}]
    manager = PositionManager(db, MagicMock(), MagicMock())

    first = make_signal(message_id=888, channel_id=-100333)
    duplicate = make_signal(message_id=888, channel_id=-100333)

    assert manager.register_signal(first, make_instrument()) is not None
    assert manager.register_signal(duplicate, make_instrument("10002")) is None


def test_equity_entry_uses_exactly_one_lot():
    db = MagicMock()
    db.fetch_one.return_value = None
    portfolio = MagicMock()
    portfolio.has_sufficient_margin.return_value = True
    manager = PositionManager(db, portfolio, MagicMock())
    instrument = make_instrument(lotsize=25)
    manager.active_signals["signal-1"] = {
        "id": "signal-1",
        "segment": "equity",
        "_instrument": instrument,
        "target_1": 15.0,
        "stop_loss": 8.0,
        "trade_type": "LONG",
        "underlying": "NIFTY",
        "strike": 23450,
        "option_type": "CE",
    }

    manager.execute_entry("signal-1", 10.0)

    position_insert = next(
        call for call in db.execute.call_args_list
        if "INSERT INTO positions" in call.args[0]
    )
    assert position_insert.args[1][5] == 25
    portfolio.block_margin.assert_called_once_with("equity", 250.0)


def make_future_item(token, expiry):
    return {
        "exch_seg": "MCX",
        "instrumenttype": "FUTCOM",
        "symbol": f"CRUDEOIL{expiry}FUT",
        "name": "CRUDEOIL",
        "strike": "0.000000",
        "token": token,
        "expiry": expiry,
        "lotsize": "100",
    }


def test_instrument_refresh_replaces_indexes_and_removes_old_tokens():
    manager = InstrumentManager()
    expiry = (date.today() + timedelta(days=60)).strftime("%d%b%Y").upper()
    manager._build_indexes([make_future_item("old-token", expiry)])
    assert "old-token" in manager.token_to_instrument

    manager._build_indexes([make_future_item("new-token", expiry)])

    assert "new-token" in manager.token_to_instrument
    assert "old-token" not in manager.token_to_instrument
    tokens = {
        contract["token"]
        for strike_map in manager.options_map.values()
        for type_map in strike_map.values()
        for contracts in type_map.values()
        for contract in contracts
    }
    assert tokens == {"new-token"}


def test_position_checks_ignore_stale_ticks(monkeypatch):
    monkeypatch.setattr(paper_engine_module, "MAX_TICK_AGE_MS", 1234)
    cache = MagicMock()
    cache.get_ltp.return_value = None
    manager = MagicMock()
    manager._state_lock = Lock()
    manager.active_positions = {
        "position-1": {
            "token": "10001",
            "symbol": "NIFTY30NOV2623450CE",
            "entry_price": 10.0,
            "quantity": 25,
            "target_1": 15.0,
            "stop_loss": 8.0,
            "trade_type": "LONG",
            "signal_id": "signal-1",
        }
    }
    manager.active_signals = {}
    engine = PaperEngine(cache, manager, MagicMock())

    engine.check_positions()

    cache.get_ltp.assert_called_once_with("10001", max_age_ms=1234)
    manager.execute_exit.assert_not_called()


def test_eod_does_not_invent_entry_price_when_no_quote_exists():
    cache = MagicMock()
    cache.get_ltp.return_value = None
    ws = MagicMock()
    ws.auth.api.ltpData.return_value = {"status": False, "data": None}

    manager = MagicMock()
    manager._state_lock = Lock()
    manager.inst_manager = None
    manager.active_positions = {
        "position-1": {
            "token": "10001",
            "symbol": "NIFTY30NOV2623450CE",
            "entry_price": 10.0,
            "quantity": 25,
            "signal_id": "signal-1",
        }
    }
    manager.active_signals = {}
    manager.db.fetch_one.return_value = {"segment": "equity"}
    engine = PaperEngine(cache, manager, ws)

    with patch("app.paper_engine.datetime") as mock_datetime:
        mock_datetime.now.return_value = datetime(2026, 10, 13, 15, 20, tzinfo=paper_engine_module.IST)
        engine.check_eod_squareoff()

    manager.execute_exit.assert_not_called()
    assert "position-1" in manager.active_positions


def test_config_refuses_unsafe_live_mode(monkeypatch):
    required = {
        "TELEGRAM_API_ID": 12345,
        "TELEGRAM_API_HASH": "hash",
        "TELEGRAM_CHANNEL_ID": -100123,
        "TELEGRAM_BOT_TOKEN": "token",
        "ANGEL_API_KEY": "key",
        "ANGEL_CLIENT_ID": "client",
        "ANGEL_PASSWORD": "password",
        "ANGEL_TOTP_SECRET": "secret",
    }
    for name, value in required.items():
        monkeypatch.setattr(config, name, value)
    monkeypatch.setattr(config, "APP_ENV", "real")
    monkeypatch.setattr(config, "PAPER_ONLY", False)
    monkeypatch.setattr(config, "ENTRY_WAIT_SECONDS", 300)
    monkeypatch.setattr(config, "COMMODITY_FUTURES_ENTRY_WAIT_SECONDS", 14400)
    monkeypatch.setattr(config, "MAX_TICK_AGE_MS", 2000)

    with pytest.raises(ValueError, match="Live trading is blocked"):
        config.validate_config()



def test_dashboard_manual_close_waits_for_a_fresh_quote():
    cache = MagicMock()
    cache.get_ltp.return_value = None
    ws = MagicMock()
    ws.auth.api.ltpData.return_value = {"status": False, "data": None}

    manager = MagicMock()
    manager.inst_manager = None
    manager.active_positions = {
        "position-1": {
            "token": "10001",
            "symbol": "NIFTY30NOV2623450CE",
            "entry_price": 10.0,
        }
    }
    manager.db.fetch_all.return_value = [{
        "id": 17,
        "command": "CLOSE_POSITION",
        "payload": '{"pos_id":"position-1","cmp":99.0}',
        "status": "PENDING",
    }]
    engine = PaperEngine(cache, manager, ws)

    engine.process_commands()

    manager.execute_exit.assert_not_called()
    # The command must remain pending for a later retry instead of being marked DONE.
    manager.db.execute.assert_not_called()



def test_hero_zero_entry_remains_eligible_until_its_own_cutoff():
    cache = MagicMock()
    cache.get_ltp.return_value = 10.0
    manager = MagicMock()
    manager._state_lock = Lock()
    manager.active_signals = {
        "hero-signal": {
            "_instrument": {"token": "hero-token", "exch_seg": "BFO"},
            "segment": "hero_zero",
            "_received_ts": 1_000_000_000_000,
            "entry_low": 9.0,
            "entry_high": 11.0,
            "target_1": 15.0,
            "stop_loss": 8.0,
            "trade_type": "LONG",
        }
    }
    manager.active_positions = {}
    engine = PaperEngine(cache, manager, MagicMock())

    with patch("app.paper_engine.datetime") as mock_datetime:
        mock_datetime.now.return_value = datetime(2026, 10, 13, 15, 35, tzinfo=paper_engine_module.IST)
        engine.check_signals()

    manager.execute_entry.assert_called_once_with("hero-signal", 10.0)
