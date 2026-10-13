import re
from typing import Optional
from app.models import ParsedSignal, OptionType

class SignalParser:
    """
    Parses unstructured Telegram messages into structured ParsedSignal objects.
    """
    
    # Matches: #NIFTY 23450 PE or #GODFRYPHLP 1900 PE SEP or TECHM 1580 CE
    INSTRUMENT_REGEX = re.compile(
        r'#?([A-Za-z]+)\s+(\d+(?:\.\d+)?)\s+(CE|PE)(?:\s+(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\b)?', 
        re.IGNORECASE
    )
    
    # Matches: Entry - 48 - 53 or Entry: 28-31
    ENTRY_REGEX = re.compile(
        r'Entry\s*[:-]?\s*(\d+(?:\.\d+)?)\s*(?:-|to)\s*(\d+(?:\.\d+)?)', 
        re.IGNORECASE
    )
    
    # Matches: Target - 68/84/100 or Target: 35/40 or Target - 18
    TARGET_REGEX = re.compile(
        r'Target\s*[:-]?\s*(\d+(?:\.\d+)?)(?:\s*/\s*(\d+(?:\.\d+)?))?(?:\s*/\s*(\d+(?:\.\d+)?))?', 
        re.IGNORECASE
    )
    
    # Matches: SL - 25 or SL: 24
    SL_REGEX = re.compile(
        r'SL\s*[:-]?\s*(\d+(?:\.\d+)?)', 
        re.IGNORECASE
    )
    
    # Matches Qty and Note
    QTY_REGEX = re.compile(r'Qty\s*[:-]?\s*(.*?Lots?)', re.IGNORECASE)
    NOTE_REGEX = re.compile(r'Note\s*[:-]?\s*(.*)', re.IGNORECASE)

    @classmethod
    def parse(cls, message: str) -> Optional[ParsedSignal]:
        # Normalize the message by collapsing multiple whitespaces and newlines into single spaces
        # But we also want to ensure that "GODFRYPHLP\n1900 PE SEP" becomes "GODFRYPHLP 1900 PE SEP"
        normalized_msg = message.replace('*', ' ')
        normalized_msg = re.sub(r'\s+', ' ', normalized_msg).strip()
        
        # 1. Parse Instrument
        inst_match = cls.INSTRUMENT_REGEX.search(normalized_msg)
        if not inst_match:
            return None
        
        underlying = inst_match.group(1).upper()
        # Clean up common hashtags like INTRADAY that might falsely match if the formatting is weird, 
        # though our regex requires a number to follow it.
        if underlying == 'INTRADAY':
            # Let's try to find the next match instead
            inst_matches = cls.INSTRUMENT_REGEX.findall(normalized_msg)
            # findall returns tuples of groups
            valid_match = None
            for match in inst_matches:
                if match[0].upper() != 'INTRADAY':
                    valid_match = match
                    break
            if not valid_match:
                return None
            underlying = valid_match[0].upper()
            strike = float(valid_match[1])
            option_type = OptionType(valid_match[2].upper())
            expiry = valid_match[3].upper() if valid_match[3] else None
        else:
            strike = float(inst_match.group(2))
            option_type = OptionType(inst_match.group(3).upper())
            expiry = inst_match.group(4).upper() if inst_match.group(4) else None

        # 2. Parse Entry
        entry_match = cls.ENTRY_REGEX.search(normalized_msg)
        if not entry_match:
            return None
        entry_low = float(entry_match.group(1))
        entry_high = float(entry_match.group(2))

        # Ensure low is actually <= high
        if entry_low > entry_high:
            entry_low, entry_high = entry_high, entry_low

        # 3. Parse Target
        target_match = cls.TARGET_REGEX.search(normalized_msg)
        if not target_match:
            return None
        target_1 = float(target_match.group(1))
        target_2 = float(target_match.group(2)) if target_match.group(2) else None
        target_3 = float(target_match.group(3)) if target_match.group(3) else None

        # 4. Parse SL
        sl_match = cls.SL_REGEX.search(normalized_msg)
        if not sl_match:
            return None
        stop_loss = float(sl_match.group(1))

        # 5. Optional metadata
        qty_match = cls.QTY_REGEX.search(normalized_msg)
        requested_lot_range = qty_match.group(1).strip() if qty_match else None

        note_match = cls.NOTE_REGEX.search(normalized_msg)
        note = note_match.group(1).strip() if note_match else None

        return ParsedSignal(
            underlying=underlying,
            strike=strike,
            option_type=option_type,
            expiry=expiry,
            entry_low=entry_low,
            entry_high=entry_high,
            target_1=target_1,
            target_2=target_2,
            target_3=target_3,
            stop_loss=stop_loss,
            requested_lot_range=requested_lot_range,
            note=note
        )
