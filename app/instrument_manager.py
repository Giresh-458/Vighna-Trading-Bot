import os
import json
import time
import logging
import urllib.request
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from app.models import ParsedSignal, OptionType

logger = logging.getLogger(__name__)

class InstrumentManager:
    MASTER_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
    CACHE_FILE = "scrip_master.json"

    def __init__(self):
        # Maps underlying -> strike -> option_type -> list of dicts (sorted by expiry)
        self.options_map: Dict[str, Dict[float, Dict[str, List[dict]]]] = {}
        self.token_to_instrument: Dict[str, dict] = {}
        self.is_loaded = False

    def load_instruments(self, force_download=False):
        """
        Downloads or loads the cached instrument master and builds fast lookup tables.
        """
        cache_stale = False
        if os.path.exists(self.CACHE_FILE):
            file_age = time.time() - os.path.getmtime(self.CACHE_FILE)
            cache_stale = file_age > 43200  # 12 hours
            
        if force_download or not os.path.exists(self.CACHE_FILE) or cache_stale:
            logger.info(f"Downloading Scrip Master from {self.MASTER_URL}...")
            urllib.request.urlretrieve(self.MASTER_URL, self.CACHE_FILE)
            logger.info("Download complete.")
        else:
            logger.info(f"Using cached Scrip Master from {self.CACHE_FILE}")

        try:
            with open(self.CACHE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except json.JSONDecodeError:
            logger.warning("Cached JSON is invalid. Redownloading...")
            urllib.request.urlretrieve(self.MASTER_URL, self.CACHE_FILE)
            with open(self.CACHE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)

        self._build_indexes(data)
        self.is_loaded = True
        logger.info(f"Instrument Manager loaded {len(self.token_to_instrument)} tokens.")

    def _build_indexes(self, data: List[dict]):
        now = datetime.now(timezone.utc)
        today = now.date()
        
        # Only parse NFO options, BFO options, and MCX
        nfo_bfo_options = []
        mcx_instruments = []
        
        for item in data:
            exch = item.get("exch_seg")
            inst_type = item.get("instrumenttype")
            
            if exch in ["NFO", "BFO"] and inst_type in ["OPTIDX", "OPTSTK"]:
                symbol = item.get("symbol", "")
                option_type = "CE" if "CE" in symbol[-2:] else "PE" if "PE" in symbol[-2:] else None
                if not option_type:
                    continue
                
                try:
                    expiry_date = datetime.strptime(item.get("expiry"), "%d%b%Y").date()
                except ValueError:
                    continue
                
                if expiry_date < today:
                    continue

                item["_parsed_expiry"] = expiry_date
                item["_option_type"] = option_type
                nfo_bfo_options.append(item)
                
            elif exch == "MCX" and inst_type in ["FUTCOM", "OPTFUT"]:
                symbol = item.get("symbol", "")
                
                try:
                    expiry_date = datetime.strptime(item.get("expiry"), "%d%b%Y").date()
                except ValueError:
                    continue
                    
                if expiry_date < today:
                    continue
                    
                item["_parsed_expiry"] = expiry_date
                
                if inst_type == "OPTFUT":
                    option_type = "CE" if "CE" in symbol[-2:] else "PE" if "PE" in symbol[-2:] else None
                    if not option_type:
                        continue
                    item["_option_type"] = option_type
                else:
                    item["_option_type"] = "FUT"
                    
                mcx_instruments.append(item)

        # Build nested map: underlying -> strike -> option_type -> list
        # For futures, strike is 0.0 or we can just use 0.0
        
        for item in nfo_bfo_options + mcx_instruments:
            underlying = item["name"]
            
            # Angel One strikes are multiplied by 100 for NFO and MCX
            strike = float(item["strike"]) / 100.0
            
            if item.get("instrumenttype") == "FUTCOM":
                strike = 0.0  # Futures don't have strike
                
            opt_type = item["_option_type"]

            if underlying not in self.options_map:
                self.options_map[underlying] = {}
            if strike not in self.options_map[underlying]:
                self.options_map[underlying][strike] = {}
            if opt_type not in self.options_map[underlying][strike]:
                self.options_map[underlying][strike][opt_type] = []
            
            self.options_map[underlying][strike][opt_type].append(item)
            self.token_to_instrument[item["token"]] = item

        # Sort all lists by expiry date ascending
        for underlying in self.options_map.values():
            for strike_dict in underlying.values():
                for opt_list in strike_dict.values():
                    opt_list.sort(key=lambda x: x["_parsed_expiry"])

    def resolve_signal(self, signal: ParsedSignal) -> Tuple[Optional[dict], Optional[str]]:
        """
        Resolves a ParsedSignal to the exact Angel One instrument dict.
        Returns (instrument_dict, error_reason)
        """
        if os.path.exists(self.CACHE_FILE):
            if (time.time() - os.path.getmtime(self.CACHE_FILE)) > 43200:
                logger.info("Scrip master is stale during runtime. Auto-refreshing...")
                self.load_instruments(force_download=True)
                
        if not self.is_loaded:
            return None, "InstrumentManager not loaded"

        underlying = signal.underlying
        
        # Aliases for MCX, NFO, and BFO
        ALIASES = {
            "NG": "NATURALGAS",
            "NATGAS": "NATURALGAS",
            "CRUDE": "CRUDEOIL",
            "CRUDEOIL": "CRUDEOIL",
            "SILVER": "SILVER",
            "SILVERM": "SILVERM",
            "SILVERMIC": "SILVERMIC",
            "GOLD": "GOLD",
            "GOLDM": "GOLDM",
            "COPPER": "COPPER",
            "COPPERM": "COPPERM",
            "ZINC": "ZINC",
            "ZINCMINI": "ZINCMINI",
            "ALUMINIUM": "ALUMINIUM",
            "ALUMINI": "ALUMINI",
            "LEAD": "LEAD",
            "LEADMINI": "LEADMINI",
            "NIFTY": "NIFTY",
            "BANKNIFTY": "BANKNIFTY",
            "FINNIFTY": "FINNIFTY",
            "MIDCPNIFTY": "MIDCPNIFTY",
            "SENSEX": "SENSEX",
            "BANKEX": "BANKEX",
        }
        underlying = ALIASES.get(underlying, underlying)
            
        if underlying not in self.options_map:
            return None, f"Underlying {underlying} not found in instrument master"

        strike = float(signal.strike) if signal.strike is not None else 0.0
        if strike not in self.options_map[underlying]:
            return None, f"Strike {strike} not found for {underlying}"

        opt_type = signal.option_type.value if signal.option_type else "FUT"
        if opt_type not in self.options_map[underlying][strike]:
            return None, f"Option type {opt_type} not found for {underlying} {strike}"

        candidates = self.options_map[underlying][strike][opt_type]
        if not candidates:
            return None, "No active contracts found"

        selected_item = None

        if signal.expiry:
            # Signal specifies an expiry like "SEP" or "OCT"
            month_str = signal.expiry.upper()
            
            # Filter candidates that have this month in their string
            matched_candidates = [c for c in candidates if month_str in c["expiry"].upper()]
            if matched_candidates:
                # pick the nearest one that matches
                selected_item = matched_candidates[0]
            else:
                return None, f"No contract found matching expiry {month_str}"
        else:
            # No explicit expiry, pick the nearest one overall
            selected_item = candidates[0]

        return selected_item, None
