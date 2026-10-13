import sys
from app.commodity_signal_parser import CommoditySignalParser

# Ensure console output handles emojis on Windows
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

messages = [
    '''TSM - COMMODITIES
#NG 🌍 FUT 📈

Entry - Above 289 ⬆️
Target - 291/293/295 🎯
SL - 287 ⚠️''',
    '''TSM - COMMODITIES
#NG 🌍 FUT 📈

Entry - Below 287 ⬇️
Target - 285/283/281 🎯
SL - 289 ⚠️''',
    '''TSM - COMMODITIES
#GOLD 🌍 FUT 📈

Entry - Above 149.7K ⬆️
Target - 150K/150.5K/151K 🎯
SL - 149.3K ⚠️''',
    '''TSM - COMMODITIES
#CRUDEOIL 8500 PE OCT 📈

Entry - Above 370 📈
Target - 390/410/430/450 🎯
SL - 330 ⚠️

Note - Wait for the level to come and let the price sustain. Don't enter at current price ⏳ 🛑'''
]

for i, msg in enumerate(messages):
    print(f"--- Test {i+1} ---")
    parsed = CommoditySignalParser.parse(msg)
    if parsed:
        print(parsed.model_dump_json(indent=2))
    else:
        print("FAILED TO PARSE")
    print()
