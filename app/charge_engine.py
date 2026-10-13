class ChargeEngine:
    """
    Calculates simulated charges for Angel One F&O Option trades.
    Using default flat rates which can be overridden.
    """
    def __init__(self):
        # Default Angel One rates for Options
        self.brokerage_per_order = 20.0
        self.stt_rate_sell = 0.000625  # STT on sell side only (0.0625% on premium)
        self.exchange_txn_charge_rate = 0.0005  # NSE options (0.05% on premium)
        self.gst_rate = 0.18  # 18% on (Brokerage + Txn Charges + SEBI)
        self.sebi_rate = 0.000001  # ₹10 per crore
        self.stamp_duty_rate = 0.00003  # 0.003% on buy side only

    def calculate_buy_charges(self, entry_price: float, quantity: int, segment: str = 'equity') -> dict:
        # Adjust rates based on segment
        txn_rate = self.exchange_txn_charge_rate
        stt_rate = 0.0
        stamp_duty = self.stamp_duty_rate
        if 'commodity' in segment:
            txn_rate = 0.000026
            stamp_duty = 0.00002
        elif segment == 'hero_zero':
            txn_rate = 0.000375  # BFO options
        premium = entry_price * quantity
        
        brokerage = self.brokerage_per_order
        txn_charges = premium * txn_rate
        sebi_charges = premium * self.sebi_rate
        stamp_duty = premium * stamp_duty
        gst = (brokerage + txn_charges + sebi_charges) * self.gst_rate
        stt = 0.0 # No STT on buy side for options
        
        total = brokerage + txn_charges + sebi_charges + stamp_duty + gst + stt
        
        return {
            "brokerage": brokerage,
            "txn_charges": round(txn_charges, 2),
            "sebi_charges": round(sebi_charges, 2),
            "stamp_duty": round(stamp_duty, 2),
            "gst": round(gst, 2),
            "stt": round(stt, 2),
            "total_buy_charges": round(total, 2)
        }

    def calculate_sell_charges(self, exit_price: float, quantity: int, segment: str = 'equity') -> dict:
        txn_rate = self.exchange_txn_charge_rate
        stt_rate = self.stt_rate_sell
        if 'commodity' in segment:
            txn_rate = 0.000026
            stt_rate = 0.0001
        elif segment == 'hero_zero':
            txn_rate = 0.000375
            stt_rate = 0.000625
        premium = exit_price * quantity
        
        brokerage = self.brokerage_per_order
        txn_charges = premium * txn_rate
        sebi_charges = premium * self.sebi_rate
        stamp_duty = 0.0 # No stamp duty on sell side
        gst = (brokerage + txn_charges + sebi_charges) * self.gst_rate
        stt = round(premium * stt_rate) # STT is rounded to nearest rupee
        
        total = brokerage + txn_charges + sebi_charges + stamp_duty + gst + stt
        
        return {
            "brokerage": brokerage,
            "txn_charges": round(txn_charges, 2),
            "sebi_charges": round(sebi_charges, 2),
            "stamp_duty": round(stamp_duty, 2),
            "gst": round(gst, 2),
            "stt": round(stt, 2),
            "total_sell_charges": round(total, 2)
        }

    def calculate_total_trade_charges(self, entry_price: float, exit_price: float, quantity: int, segment: str = 'equity', trade_type: str = 'LONG') -> dict:
        if trade_type == 'SHORT':
            buy = self.calculate_buy_charges(exit_price, quantity, segment)
            sell = self.calculate_sell_charges(entry_price, quantity, segment)
        else:
            buy = self.calculate_buy_charges(entry_price, quantity, segment)
            sell = self.calculate_sell_charges(exit_price, quantity, segment)
        
        total = buy["total_buy_charges"] + sell["total_sell_charges"]
        return {
            "buy": buy,
            "sell": sell,
            "total_charges": round(total, 2)
        }

