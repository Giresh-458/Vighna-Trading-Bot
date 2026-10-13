import re
with open('app/position_manager.py', 'r', encoding='utf-8') as f:
    text = f.read()

pattern = re.compile(r'msg = \(\n\s*f"[^"]*\*\*TRADE ENTERED\*\*\\n"\n.*?f"SL: {sig\[\'stop_loss\'\]}"\n\s*\)', re.DOTALL)
new_msg = 'msg = (\n            f"✅ **TRADE ENTERED**\\\\n"\n            f"#{sig[\'underlying\']} {opt_str}\\\\n"\n            f"Entry: {ltp}\\\\n"\n            f"SL: {sig[\'stop_loss\']}"\n        )'

# Sometimes there's a double msg= if it crashed mid-edit
text = re.sub(r'msg = \(\n\s*f"[^"]*\*\*TRADE ENTERED\*\*.*?\n\s*msg = \(\n\s*f"[^"]*\*\*TRADE ENTERED\*\*.*?\n\s*\)', new_msg, text, flags=re.DOTALL)

text = re.sub(r'msg = \(\n\s*f"[^"]*\*\*TRADE ENTERED\*\*.*?\n\s*\)', new_msg, text, flags=re.DOTALL)

with open('app/position_manager.py', 'w', encoding='utf-8') as f:
    f.write(text)
