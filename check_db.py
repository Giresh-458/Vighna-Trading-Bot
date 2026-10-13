import sqlite3
import sys
from datetime import date

# Default to today's date, or use the date argument if provided (e.g., python check_db.py 2026-10-07)
target_date = sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat()

conn = sqlite3.connect('paper_trading.db')
conn.row_factory = sqlite3.Row
c = conn.cursor()
signals = c.execute('SELECT * FROM signals WHERE received_at LIKE ?', (f'{target_date}%',)).fetchall()
print(f'Total signals for {target_date}: {len(signals)}')
for s in signals:
    print(f"{s['underlying']} {s['trade_type']} | {s['status']} | {s['id']}")
