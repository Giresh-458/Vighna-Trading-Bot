import pytest
from app.commodity_signal_parser import CommoditySignalParser
from app.models import OptionType, TradeType

def test_parse_commodity_futures_long():
    msg = """TSM - COMMODITIES
#NG 🌍 FUT 📈

Entry - Above 289 ⬆️
Target - 291/293/295 🎯
SL - 287 ⚠️"""
    sig = CommoditySignalParser.parse(msg)
    assert sig is not None
    assert sig.underlying == "NG"
    assert sig.trade_type == TradeType.LONG
    assert sig.strike is None
    assert sig.option_type is None
    assert sig.entry_low == 289.0
    assert sig.entry_high == 999999999.0
    assert sig.target_1 == 291.0
    assert sig.target_2 == 293.0
    assert sig.target_3 == 295.0
    assert sig.stop_loss == 287.0
    assert sig.segment == "commodity_futures"

def test_parse_commodity_futures_short():
    msg = """TSM - COMMODITIES
#NG 🌍 FUT 📈

Entry - Below 287 ⬇️
Target - 285/283/281 🎯
SL - 289 ⚠️"""
    sig = CommoditySignalParser.parse(msg)
    assert sig is not None
    assert sig.underlying == "NG"
    assert sig.trade_type == TradeType.SHORT
    assert sig.strike is None
    assert sig.option_type is None
    assert sig.entry_low == 0.0
    assert sig.entry_high == 287.0
    assert sig.target_1 == 285.0
    assert sig.target_2 == 283.0
    assert sig.target_3 == 281.0
    assert sig.stop_loss == 289.0
    assert sig.segment == "commodity_futures"

def test_parse_commodity_k_multiplier():
    msg = """TSM - COMMODITIES
#GOLD 🌍 FUT 📈

Entry - Above 149.7K ⬆️
Target - 150K/150.5K/151K 🎯
SL - 149.3K ⚠️"""
    sig = CommoditySignalParser.parse(msg)
    assert sig is not None
    assert sig.underlying == "GOLD"
    assert sig.trade_type == TradeType.LONG
    assert sig.entry_low == 149700.0
    assert sig.target_1 == 150000.0
    assert sig.target_2 == 150500.0
    assert sig.target_3 == 151000.0
    assert sig.stop_loss == 149300.0
    assert sig.segment == "commodity_futures"

def test_parse_commodity_options():
    msg = """TSM - COMMODITIES
#CRUDEOIL 8500 PE OCT 📈

Entry - Above 370 📈
Target - 390/410/430/450 🎯
SL - 330 ⚠️

Note - Wait for the level to come and let the price sustain. Don't enter at current price ⏳ 🛑"""
    sig = CommoditySignalParser.parse(msg)
    assert sig is not None
    assert sig.underlying == "CRUDEOIL"
    assert sig.strike == 8500.0
    assert sig.option_type == OptionType.PE
    assert sig.expiry == "OCT"
    assert sig.entry_low == 370.0
    assert sig.target_1 == 390.0
    assert sig.target_2 == 410.0
    assert sig.target_3 == 430.0
    assert sig.stop_loss == 330.0
    assert "Wait for the level to come" in sig.note
    assert sig.segment == "commodity_options"

def test_parse_all_dual_direction():
    msg = """TSM - COMMODITIES
#NG 🌍 FUT 📈

Entry - Above 289 ⬆️
Target - 291/293/295 🎯
SL - 287 ⚠️

Entry - Below 287 ⬇️
Target - 285/283/281 🎯
SL - 289 ⚠️"""
    signals = CommoditySignalParser.parse_all(msg)
    assert len(signals) == 2
    assert signals[0].trade_type == TradeType.LONG
    assert signals[0].entry_low == 289.0
    assert signals[1].trade_type == TradeType.SHORT
    assert signals[1].entry_high == 287.0

def test_parse_invalid_commodity_message():
    assert CommoditySignalParser.parse("Good morning traders") is None
    assert CommoditySignalParser.parse_all("Good morning traders") == []
