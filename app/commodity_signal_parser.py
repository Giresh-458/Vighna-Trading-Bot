import re
from typing import Optional
from app.models import ParsedSignal, OptionType, TradeType

class CommoditySignalParser:
    """
    Parses unstructured Telegram messages into structured ParsedSignal objects for commodities.
    Handles both futures and options.
    """
    
    # Matches options: #CRUDEOIL 8500 PE OCT or #GOLD 160K CE OCT
    OPTIONS_REGEX = re.compile(
        r'#?([A-Za-z]+)\s+(\d+(?:\.\d+)?K?)\s+(CE|PE)(?:\s+(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\b(?:\s+\d{2,4})?)?', 
        re.IGNORECASE
    )
    
    # Matches futures: #NG 🌍 FUT or #CRUDEOIL 🌍 FUT
    FUTURES_REGEX = re.compile(
        r'#?([A-Za-z]+)\s*(?:[^\w\s]*)\s*FUT', 
        re.IGNORECASE
    )
    
    # Matches: Entry - Above 289 or Entry - Below 287 or Entry - Above 149.7K
    ENTRY_REGEX = re.compile(
        r'Entry\s*[:-]?\s*(Above|Below)\s+(\d+(?:\.\d+)?K?)', 
        re.IGNORECASE
    )
    
    # Matches: Target - 291/293/295 or Target - 150K/150.5K/151K
    TARGET_REGEX = re.compile(
        r'Target\s*[:-]?\s*(\d+(?:\.\d+)?K?)(?:\s*/\s*(\d+(?:\.\d+)?K?))?(?:\s*/\s*(\d+(?:\.\d+)?K?))?', 
        re.IGNORECASE
    )
    
    # Matches: SL - 287 or SL - 149.3K
    SL_REGEX = re.compile(
        r'SL\s*[:-]?\s*(\d+(?:\.\d+)?K?)', 
        re.IGNORECASE
    )
    
    # Matches Note
    NOTE_REGEX = re.compile(r'Note\s*[:-]?\s*(.*)', re.IGNORECASE)

    @classmethod
    def parse_number(cls, value_str: str) -> float:
        if not value_str:
            return 0.0
        value_str = value_str.upper().strip()
        if value_str.endswith('K'):
            return float(value_str[:-1]) * 1000
        return float(value_str)

    @classmethod
    def parse(cls, message: str) -> Optional[ParsedSignal]:
        normalized_msg = message.replace('*', ' ')
        normalized_msg = re.sub(r'\s+', ' ', normalized_msg).strip()
        
        is_futures = False
        is_options = False
        
        underlying = None
        strike = None
        option_type = None
        expiry = None
        
        # 1. Parse Instrument
        opt_match = cls.OPTIONS_REGEX.search(normalized_msg)
        fut_match = cls.FUTURES_REGEX.search(normalized_msg)
        
        if opt_match and (not fut_match or opt_match.start() < fut_match.start()):
            is_options = True
            underlying = opt_match.group(1).upper()
            strike = cls.parse_number(opt_match.group(2))
            option_type = OptionType(opt_match.group(3).upper())
            expiry = opt_match.group(4).upper() if opt_match.group(4) else None
        elif fut_match:
            is_futures = True
            underlying = fut_match.group(1).upper()
        else:
            return None

        # 2. Parse Entry
        entry_match = cls.ENTRY_REGEX.search(normalized_msg)
        if not entry_match:
            return None
            
        entry_direction = entry_match.group(1).upper()
        entry_val = cls.parse_number(entry_match.group(2))
        
        if entry_direction == "ABOVE":
            trade_type = TradeType.LONG
            entry_low = entry_val
            entry_high = 999999999.0  # practically infinity
        else:
            trade_type = TradeType.SHORT
            entry_low = 0.0
            entry_high = entry_val

        # 3. Parse Target
        target_match = cls.TARGET_REGEX.search(normalized_msg)
        if not target_match:
            return None
            
        target_1 = cls.parse_number(target_match.group(1))
        target_2 = cls.parse_number(target_match.group(2)) if target_match.group(2) else None
        target_3 = cls.parse_number(target_match.group(3)) if target_match.group(3) else None

        # 4. Parse SL
        sl_match = cls.SL_REGEX.search(normalized_msg)
        if not sl_match:
            return None
        stop_loss = cls.parse_number(sl_match.group(1))

        # 5. Optional metadata
        note_match = cls.NOTE_REGEX.search(normalized_msg)
        note = note_match.group(1).strip() if note_match else None

        segment = "commodity_options" if (strike and option_type) else "commodity_futures"

        return ParsedSignal(
            underlying=underlying,
            trade_type=trade_type,
            strike=strike,
            option_type=option_type,
            expiry=expiry,
            entry_low=entry_low,
            entry_high=entry_high,
            target_1=target_1,
            target_2=target_2,
            target_3=target_3,
            stop_loss=stop_loss,
            note=note,
            segment=segment
        )

    @classmethod
    def parse_all(cls, message: str) -> list:
        """
        Parse a message that may contain dual-direction signals (Above + Below).
        Returns a list of ParsedSignal objects (1 for single-direction, 2 for dual).
        """
        normalized = message.replace('*', ' ')
        normalized = re.sub(r'\s+', ' ', normalized).strip()
        
        # Find all Entry occurrences
        entry_matches = list(cls.ENTRY_REGEX.finditer(normalized))
        
        if len(entry_matches) <= 1:
            # Single direction — use standard parse
            result = cls.parse(message)
            return [result] if result else []
        
        # Dual direction: split the message at the second Entry
        split_pos = entry_matches[1].start()
        
        # Extract the instrument header (everything before first Entry)
        header = normalized[:entry_matches[0].start()]
        
        part1 = header + normalized[entry_matches[0].start():split_pos]
        part2 = header + normalized[split_pos:]
        
        signals = []
        for part in [part1, part2]:
            result = cls.parse(part)
            if result:
                signals.append(result)
        
        return signals

