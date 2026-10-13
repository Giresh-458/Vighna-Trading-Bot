from datetime import date, datetime, timedelta
from threading import Lock
import time
from unittest.mock import MagicMock, patch

import pytest

import app.config as config
import app.paper_engine as paper_engine_module
from app.database import Database
from app.portfolio import Portfolio
from app.market_data_cache import is_fresh_persisted_tick
from app.config import INITIAL_CAPITAL
from app.instrument_manager import InstrumentManager
from app.models import OptionType, ParsedSignal
from app.paper_engine import PaperEngine
from app.position_manager import PositionManager
from app.reply_action_validator import validate_reply_price_update
from app.signal_parser import SignalParser
from app.market_data_cache import MarketDataCache


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


class FlatCharges:
    def calculate_total_trade_charges(self, entry_price, exit_price, quantity, segment, trade_type):
        return {
            "total_charges": 3.0,
            "buy": {"brokerage": 1.0, "stt": 0.5},
            "sell": {"brokerage": 1.0, "stt": 0.5},
        }


def make_sqlite_manager(path):
    db = Database(str(path))
    portfolio = Portfolio(db)
    manager = PositionManager(db, portfolio, FlatCharges())
    return db, portfolio, manager


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


def test_equity_entry_uses_exactly_one_lot(tmp_path):
    db, portfolio, manager = make_sqlite_manager(tmp_path / "entry.sqlite3")
    signal_id = manager.register_signal(make_signal(), make_instrument(lotsize=25))
    assert signal_id is not None

    manager.execute_entry(signal_id, 10.0)

    position = db.fetch_one("SELECT * FROM positions WHERE signal_id = ?", (signal_id,))
    assert position is not None
    assert position["quantity"] == 25
    assert position["margin_used"] == pytest.approx(250.0)
    assert portfolio.available_margin == pytest.approx(INITIAL_CAPITAL - 250.0)
    assert db.fetch_one("SELECT status FROM signals WHERE id = ?", (signal_id,))["status"] == "ENTERED"


def test_full_paper_trade_lifecycle_updates_one_shared_pool_once(tmp_path):
    db, portfolio, manager = make_sqlite_manager(tmp_path / "lifecycle.sqlite3")
    signal_id = manager.register_signal(make_signal(channel_id=-1009), make_instrument(lotsize=25))
    assert signal_id is not None

    manager.execute_entry(signal_id, 10.0)
    position = db.fetch_one("SELECT * FROM positions WHERE signal_id = ?", (signal_id,))
    assert portfolio.available_margin == pytest.approx(INITIAL_CAPITAL - 250.0)
    assert portfolio.has_sufficient_margin("commodity_options", INITIAL_CAPITAL - 250.0)
    assert not portfolio.has_sufficient_margin("commodity_options", INITIAL_CAPITAL)

    assert manager.execute_exit(position["id"], 15.0, "TARGET_HIT") is True
    assert portfolio.available_margin == pytest.approx(INITIAL_CAPITAL + 122.0)
    assert db.fetch_one("SELECT status, exit_price FROM positions WHERE id = ?", (position["id"],)) == {
        "status": "CLOSED",
        "exit_price": 15.0,
    }
    trade = db.fetch_one("SELECT * FROM trades WHERE position_id = ?", (position["id"],))
    assert trade["gross_pnl"] == pytest.approx(125.0)
    assert trade["net_pnl"] == pytest.approx(122.0)

    # A duplicate exit must not release margin or book P&L a second time.
    assert manager.execute_exit(position["id"], 15.0, "TARGET_HIT") is False
    assert portfolio.available_margin == pytest.approx(INITIAL_CAPITAL + 122.0)
    assert len(db.fetch_all("SELECT * FROM trades WHERE position_id = ?", (position["id"],))) == 1


def test_raw_signal_to_instrument_entry_and_target_exit_end_to_end(tmp_path, monkeypatch):
    """Exercise the paper path from source text through the trade ledger."""
    raw_message = """
        #NIFTY 23450 CE
        Entry - 10 - 11
        Target - 15/18/20
        SL - 8
    """
    signal = SignalParser.parse(raw_message)
    assert signal is not None
    signal.telegram_message_id = 987654
    signal.channel_id = -100987

    expiry = (date.today() + timedelta(days=30)).strftime("%d%b%Y").upper()
    instrument_data = {
        "exch_seg": "NFO",
        "instrumenttype": "OPTIDX",
        "symbol": f"NIFTY{expiry}23450CE",
        "name": "NIFTY",
        "strike": "2345000.000000",
        "token": "98765001",
        "expiry": expiry,
        "lotsize": "25",
    }
    instruments = InstrumentManager()
    instruments.CACHE_FILE = str(tmp_path / "missing-master.json")
    instruments._build_indexes([instrument_data])
    instruments.is_loaded = True
    instrument, error = instruments.resolve_signal(signal)
    assert instrument is not None, error

    db, portfolio, manager = make_sqlite_manager(tmp_path / "end-to-end.sqlite3")
    manager.inst_manager = instruments
    signal_id = manager.register_signal(signal, instrument)
    assert signal_id is not None

    cache = MarketDataCache()
    ws = MagicMock()
    engine = PaperEngine(cache, manager, ws)
    fixed_time = datetime(2026, 10, 13, 12, 0, tzinfo=paper_engine_module.IST)

    with patch("app.paper_engine.datetime") as mock_datetime:
        mock_datetime.now.return_value = fixed_time

        # In-range, fresh WebSocket tick creates exactly one-lot paper position.
        cache.update_tick(instrument["token"], 10.5)
        engine.check_signals()
        position = db.fetch_one("SELECT * FROM positions WHERE signal_id = ?", (signal_id,))
        assert position is not None
        assert position["quantity"] == 25
        assert position["status"] == "OPEN"
        assert portfolio.available_margin == pytest.approx(INITIAL_CAPITAL - 10.5 * 25)

        # A fresh target tick closes the position and books P&L/capital atomically.
        cache.update_tick(instrument["token"], 15.5)
        engine.check_positions()

    position = db.fetch_one("SELECT * FROM positions WHERE id = ?", (position["id"],))
    trade = db.fetch_one("SELECT * FROM trades WHERE position_id = ?", (position["id"],))
    assert position["status"] == "CLOSED"
    assert position["exit_reason"] == "TARGET_HIT"
    assert position["exit_price"] == pytest.approx(15.5)
    assert trade["gross_pnl"] == pytest.approx(125.0)
    assert trade["net_pnl"] == pytest.approx(122.0)
    assert portfolio.available_margin == pytest.approx(INITIAL_CAPITAL + 122.0)
    ws.unsubscribe.assert_called_once_with(instrument["token"])


def test_reply_price_updates_reject_nonpositive_nonfinite_and_crossed_levels():
    pending = {
        "status": "WATCHING_ENTRY",
        "trade_type": "LONG",
        "entry_low": 10.0,
        "entry_high": 11.0,
        "target_1": 15.0,
        "stop_loss": 8.0,
    }
    assert validate_reply_price_update("MODIFY_TARGET", 0, pending)[0] is False
    assert validate_reply_price_update("MODIFY_TARGET", float("nan"), pending)[0] is False
    assert validate_reply_price_update("MODIFY_TARGET", 9.0, pending)[0] is False
    assert validate_reply_price_update("MODIFY_SL", 16.0, pending)[0] is False
    assert validate_reply_price_update("MODIFY_TARGET", 17.0, pending)[0] is True
    assert validate_reply_price_update("MODIFY_SL", 9.5, pending)[0] is False
    assert validate_reply_price_update("MODIFY_SL", 7.0, pending)[0] is True

    pending_short = {
        "status": "WATCHING_ENTRY",
        "trade_type": "SHORT",
        "entry_low": 10.0,
        "entry_high": 11.0,
        "target_1": 8.0,
        "stop_loss": 13.0,
    }
    assert validate_reply_price_update("MODIFY_TARGET", 9.0, pending_short)[0] is True
    assert validate_reply_price_update("MODIFY_TARGET", 10.5, pending_short)[0] is False
    assert validate_reply_price_update("MODIFY_SL", 10.5, pending_short)[0] is False
    assert validate_reply_price_update("MODIFY_SL", 12.0, pending_short)[0] is True


def test_reply_price_updates_reject_modification_of_closed_signal():
    closed = {
        "status": "ENTERED",
        "trade_type": "LONG",
        "entry_low": 10.0,
        "entry_high": 11.0,
        "target_1": 15.0,
        "stop_loss": 8.0,
    }
    assert validate_reply_price_update("MODIFY_TARGET", 17.0, closed, position_row=None)[0] is False


def test_cancel_pending_signal_does_not_change_entered_signal(tmp_path):
    db, portfolio, manager = make_sqlite_manager(tmp_path / "cancel-status.sqlite3")
    signal_id = manager.register_signal(make_signal(), make_instrument())
    manager.execute_entry(signal_id, 10.0)

    assert manager.cancel_pending_signal(signal_id) is False
    assert db.fetch_one("SELECT status FROM signals WHERE id = ?", (signal_id,))["status"] == "ENTERED"
    position = db.fetch_one("SELECT id FROM positions WHERE signal_id = ? AND status = 'OPEN'", (signal_id,))
    assert position is not None


def test_legacy_segment_balances_migrate_without_multiplying_capital(tmp_path):
    db = Database(str(tmp_path / "migration.sqlite3"))
    db.execute(
        "INSERT INTO trades (position_id, net_pnl, timestamp) VALUES (?, ?, ?)",
        ("closed-position", 200.0, "2026-10-13T10:00:00+00:00"),
    )
    db.execute(
        "INSERT INTO signals (id, status, segment) VALUES (?, ?, ?)",
        ("open-signal", "ENTERED", "equity"),
    )
    db.execute(
        """INSERT INTO positions
           (id, signal_id, symbol, entry_price, quantity, entry_time, status, margin_used)
           VALUES (?, ?, ?, ?, ?, ?, ?, NULL)""",
        ("open-position", "open-signal", "NIFTY30NOV2623450CE", 10.0, 25, "2026-10-13T10:00:00+00:00", "OPEN"),
    )
    for segment in ("equity", "commodity_futures", "commodity_options", "hero_zero"):
        db.execute(
            "INSERT INTO system_state (key, value) VALUES (?, ?)",
            (f"available_margin_{segment}", "100000.0"),
        )

    portfolio = Portfolio(db)

    # Starting capital + realized P&L - margin for the one open position; never sum legacy keys.
    expected = INITIAL_CAPITAL + 200.0 - 250.0
    assert portfolio.available_margin == pytest.approx(expected)
    assert float(db.fetch_one(
        "SELECT value FROM system_state WHERE key = 'available_margin_total'"
    )["value"]) == pytest.approx(expected)


def test_entry_database_failure_rolls_back_capital_and_keeps_signal_eligible(tmp_path):
    db, portfolio, manager = make_sqlite_manager(tmp_path / "entry-failure.sqlite3")
    signal_id = manager.register_signal(make_signal(), make_instrument())
    assert signal_id is not None
    db.execute("""
        CREATE TRIGGER fail_position_insert BEFORE INSERT ON positions
        BEGIN SELECT RAISE(ABORT, 'injected entry failure'); END
    """)

    manager.execute_entry(signal_id, 10.0)

    assert portfolio.available_margin == pytest.approx(INITIAL_CAPITAL)
    assert db.fetch_one("SELECT value FROM system_state WHERE key = 'available_margin_total'")["value"] == str(INITIAL_CAPITAL)
    assert db.fetch_one("SELECT status FROM signals WHERE id = ?", (signal_id,))["status"] == "WATCHING_ENTRY"
    assert db.fetch_one("SELECT id FROM positions WHERE signal_id = ?", (signal_id,)) is None
    assert signal_id in manager.active_signals


def test_exit_database_failure_rolls_back_trade_and_keeps_position_open(tmp_path):
    db, portfolio, manager = make_sqlite_manager(tmp_path / "exit-failure.sqlite3")
    signal_id = manager.register_signal(make_signal(), make_instrument())
    manager.execute_entry(signal_id, 10.0)
    position = db.fetch_one("SELECT * FROM positions WHERE signal_id = ?", (signal_id,))
    cash_after_entry = portfolio.available_margin
    db.execute("""
        CREATE TRIGGER fail_trade_insert BEFORE INSERT ON trades
        BEGIN SELECT RAISE(ABORT, 'injected trade failure'); END
    """)

    assert manager.execute_exit(position["id"], 15.0, "TARGET_HIT") is False

    assert portfolio.available_margin == pytest.approx(cash_after_entry)
    assert db.fetch_one("SELECT status FROM positions WHERE id = ?", (position["id"],))["status"] == "OPEN"
    assert db.fetch_all("SELECT * FROM trades WHERE position_id = ?", (position["id"],)) == []
    assert position["id"] in manager.active_positions


def test_manual_close_command_stays_pending_when_exit_transaction_fails(tmp_path):
    import json

    db, portfolio, manager = make_sqlite_manager(tmp_path / "manual-exit-failure.sqlite3")
    signal_id = manager.register_signal(make_signal(), make_instrument())
    manager.execute_entry(signal_id, 10.0)
    position = db.fetch_one("SELECT * FROM positions WHERE signal_id = ?", (signal_id,))
    db.execute("""
        CREATE TRIGGER fail_trade_insert BEFORE INSERT ON trades
        BEGIN SELECT RAISE(ABORT, 'injected trade failure'); END
    """)
    command_id = db.execute(
        "INSERT INTO commands (command, payload, status, created_at) VALUES (?, ?, 'PENDING', ?)",
        ("CLOSE_POSITION", json.dumps({"pos_id": position["id"]}), "2026-10-13T10:00:00+00:00"),
    )

    cache = MagicMock()
    cache.get_ltp.return_value = None
    ws = MagicMock()
    ws.auth.api.ltpData.return_value = {"status": True, "data": {"ltp": 15.0}}
    engine = PaperEngine(cache, manager, ws)

    engine.process_commands()

    assert db.fetch_one("SELECT status FROM commands WHERE id = ?", (command_id,))["status"] == "PENDING"
    assert db.fetch_one("SELECT status FROM positions WHERE id = ?", (position["id"],))["status"] == "OPEN"
    assert position["id"] in manager.active_positions
    assert db.fetch_all("SELECT * FROM trades WHERE position_id = ?", (position["id"],)) == []


def test_manual_close_recovers_open_position_missing_from_memory(tmp_path):
    import json

    db, portfolio, manager = make_sqlite_manager(tmp_path / "manual-recovery.sqlite3")
    signal_id = manager.register_signal(make_signal(), make_instrument())
    manager.execute_entry(signal_id, 10.0)
    position = db.fetch_one("SELECT * FROM positions WHERE signal_id = ?", (signal_id,))
    manager.active_positions.clear()
    command_id = db.execute(
        "INSERT INTO commands (command, payload, status, created_at) VALUES (?, ?, 'PENDING', ?)",
        ("CLOSE_POSITION", json.dumps({"pos_id": position["id"]}), "2026-10-13T10:00:00+00:00"),
    )

    cache = MagicMock()
    cache.get_ltp.return_value = None
    ws = MagicMock()
    ws.auth.api.ltpData.return_value = {"status": False, "data": None}
    engine = PaperEngine(cache, manager, ws)

    engine.process_commands()

    assert position["id"] in manager.active_positions
    assert db.fetch_one("SELECT status FROM positions WHERE id = ?", (position["id"],))["status"] == "OPEN"
    assert db.fetch_one("SELECT status FROM commands WHERE id = ?", (command_id,))["status"] == "PENDING"


def test_database_preserves_original_market_tick_timestamp(tmp_path):
    from datetime import timezone
    from app.market_data_cache import is_fresh_persisted_tick

    db = Database(str(tmp_path / "tick-time.sqlite3"))
    received_at = datetime.now(timezone.utc) - timedelta(seconds=5)
    db.update_live_tick("token-1", "NIFTY", 12.5, updated_at=received_at.isoformat())

    persisted = db.fetch_one(
        "SELECT ltp, updated_at FROM live_ticks WHERE token = ?", ("token-1",)
    )
    assert persisted["ltp"] == pytest.approx(12.5)
    assert persisted["updated_at"] == received_at.isoformat()
    assert not is_fresh_persisted_tick(
        persisted["updated_at"], max_age_ms=2000, now=datetime.now(timezone.utc)
    )


def test_persisted_tick_freshness_rejects_missing_and_stale_timestamps():
    from datetime import timezone

    now = datetime(2026, 10, 13, 12, 0, 0, tzinfo=timezone.utc)
    fresh = (now - timedelta(milliseconds=1000)).isoformat()
    stale = (now - timedelta(milliseconds=3000)).isoformat()
    assert is_fresh_persisted_tick(fresh, max_age_ms=2000, now=now)
    assert not is_fresh_persisted_tick(stale, max_age_ms=2000, now=now)
    assert not is_fresh_persisted_tick(None, max_age_ms=2000, now=now)


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


def test_position_checks_use_throttled_rest_quote_when_websocket_is_stale():
    from types import SimpleNamespace

    cache = MagicMock()
    cache.get_ltp.return_value = None
    manager = MagicMock()
    manager._state_lock = Lock()
    manager.inst_manager = SimpleNamespace(
        token_to_instrument={"10001": {"exch_seg": "NFO"}}
    )
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
    manager.execute_exit.return_value = True
    ws = MagicMock()
    ws.auth.api.ltpData.return_value = {"status": True, "data": {"ltp": 15.5}}
    engine = PaperEngine(cache, manager, ws)

    engine.check_positions()

    manager.execute_exit.assert_called_once_with("position-1", 15.5, "TARGET_HIT")
    ws.unsubscribe.assert_called_once_with("10001")
    # REST values are used for the decision, but are not disguised as WebSocket ticks.
    manager.db.update_live_tick.assert_not_called()


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
            "_received_ts": time.time(),
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
