import math
from typing import Optional, Tuple


def validate_reply_price_update(
    action: str,
    price,
    signal_row: dict,
    position_row: Optional[dict] = None,
) -> Tuple[bool, str]:
    """Validate a target/stop update before it can affect a pending or open paper trade."""
    if action not in {"MODIFY_TARGET", "MODIFY_SL"}:
        return False, "Unsupported price-update action"

    try:
        value = float(price)
    except (TypeError, ValueError):
        return False, "Price must be numeric"
    if not math.isfinite(value) or value <= 0:
        return False, "Price must be a finite positive number"

    trade_type = str(
        (position_row or {}).get("trade_type")
        or signal_row.get("trade_type")
        or "LONG"
    ).upper()
    if trade_type not in {"LONG", "SHORT"}:
        return False, "Unknown trade direction"

    status = str(signal_row.get("status") or "")
    is_pending = status == "WATCHING_ENTRY"
    if position_row is None and not is_pending:
        return False, "Only pending signals or open positions can be modified"

    if position_row:
        try:
            reference = float(position_row["entry_price"])
        except (KeyError, TypeError, ValueError):
            return False, "Open position has no valid entry price"
        current_target = position_row.get("target_1", signal_row.get("target_1"))
        current_stop = position_row.get("stop_loss", signal_row.get("stop_loss"))
    else:
        try:
            low = float(signal_row["entry_low"])
            high = float(signal_row["entry_high"])
        except (KeyError, TypeError, ValueError):
            return False, "Pending signal has no valid entry range"
        # A pending long target must be above its entry range; a short target below it.
        reference = high if trade_type == "LONG" else low
        current_target = signal_row.get("target_1")
        current_stop = signal_row.get("stop_loss")

    try:
        current_target = float(current_target) if current_target is not None else None
        current_stop = float(current_stop) if current_stop is not None else None
    except (TypeError, ValueError):
        return False, "Existing target/stop value is invalid"

    if action == "MODIFY_TARGET":
        if trade_type == "LONG" and value <= reference:
            return False, "Long target must be above the entry price/range"
        if trade_type == "SHORT" and value >= reference:
            return False, "Short target must be below the entry price/range"
        if current_stop is not None:
            if trade_type == "LONG" and value <= current_stop:
                return False, "Long target must remain above the stop loss"
            if trade_type == "SHORT" and value >= current_stop:
                return False, "Short target must remain below the stop loss"
    else:
        if trade_type == "LONG" and value >= reference:
            return False, "Long stop loss must be below the entry price/range"
        if trade_type == "SHORT" and value <= reference:
            return False, "Short stop loss must be above the entry price/range"
        if current_target is not None:
            if trade_type == "LONG" and value >= current_target:
                return False, "Long stop loss must remain below the target"
            if trade_type == "SHORT" and value <= current_target:
                return False, "Short stop loss must remain above the target"

    return True, ""
