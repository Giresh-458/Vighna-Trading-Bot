import sqlite3
import json
conn = sqlite3.connect('paper_trading.db')
conn.row_factory = sqlite3.Row
c = conn.cursor()
s = c.execute("SELECT * FROM signals WHERE underlying = 'NG' ORDER BY received_at DESC").fetchone()
print(f"NG Signal Status: {s['status']}")

# Check the margin module logic for NG
with open('scrip_master.json', 'r') as f:
    master = json.load(f)

for inst in master:
    if inst.get('name') == 'NATURALGAS' and inst.get('exch_seg') == 'MCX' and inst.get('instrumenttype') == 'FUTCOM':
        print(inst)
        break
