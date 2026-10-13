import pytest
from unittest.mock import MagicMock
from app.candle_checker import check_recent_target_reached

def test_target_reached_in_recent_candles():
    mock_api = MagicMock()
    # Candle format: [timestamp, open, high, low, close, volume]
    mock_api.getCandleData.return_value = {
        "status": True,
        "data": [
            ["2026-09-24T09:27:00+05:30", 110.0, 118.0, 109.0, 117.0, 1000],
            ["2026-09-24T09:28:00+05:30", 117.0, 136.5, 116.0, 135.0, 2500], # Hit Target 1 (135.0)
            ["2026-09-24T09:29:00+05:30", 135.0, 135.0, 118.0, 119.0, 1200]  # Dropped back to entry range
        ]
    }
    
    # Target 1 is 135.0
    result = check_recent_target_reached(mock_api, "NFO", "73921", 135.0, lookback_minutes=3)
    assert result is True  # Should identify target was reached and flag to ignore

def test_target_not_reached_in_recent_candles():
    mock_api = MagicMock()
    mock_api.getCandleData.return_value = {
        "status": True,
        "data": [
            ["2026-09-24T09:27:00+05:30", 110.0, 118.0, 109.0, 117.0, 1000],
            ["2026-09-24T09:28:00+05:30", 117.0, 122.0, 116.0, 120.0, 1500],
            ["2026-09-24T09:29:00+05:30", 120.0, 121.0, 118.0, 119.0, 1200]
        ]
    }
    
    # Target 1 is 135.0, max high was 122.0
    result = check_recent_target_reached(mock_api, "NFO", "73921", 135.0, lookback_minutes=3)
    assert result is False  # Safe to trade

def test_api_failure_is_unknown_and_must_not_be_treated_as_safe():
    mock_api = MagicMock()
    mock_api.getCandleData.side_effect = Exception("API rate limit or connection timeout")

    # Unknown history must not be treated as evidence that a signal is safe.
    result = check_recent_target_reached(mock_api, "NFO", "73921", 135.0, lookback_minutes=3)
    assert result is None


def test_empty_candle_history_is_unknown():
    mock_api = MagicMock()
    mock_api.getCandleData.return_value = {"status": True, "data": []}
    result = check_recent_target_reached(mock_api, "NFO", "73921", 135.0, lookback_minutes=3)
    assert result is None
