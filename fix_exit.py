import re

with open('app/position_manager.py', 'r', encoding='utf-8') as f:
    text = f.read()

pattern = re.compile(r'logger\.info\(f"EXITED.*?self\.on_trade_callback\(msg, sig_data if sig_data else \{\}\)', re.DOTALL)

new_code = '''logger.info(f"EXITED {pos['symbol']} at {ltp} (Reason: {reason}) | Net PnL: ₹{net_pnl:.2f}")
        
        if reason == "MANUAL_EXIT":
            msg = (
                f"🛑 **MANUAL EXIT**\\\\n"
                f"#{underlying} {opt_str}\\\\n"
                f"Exit: {ltp}"
            )
            if self.on_trade_callback:
                self.on_trade_callback(msg, sig_data if sig_data else {})'''

text = pattern.sub(new_code, text)

with open('app/position_manager.py', 'w', encoding='utf-8') as f:
    f.write(text)
