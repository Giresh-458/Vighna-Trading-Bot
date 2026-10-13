import sqlite3
import os

db_path = os.path.join(os.path.dirname(__file__), "paper_trading.db")
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# 1. Clear transactional tables (leave subscribers alone)
cursor.execute("DELETE FROM trades")
cursor.execute("DELETE FROM positions")
cursor.execute("DELETE FROM signals")

# 2. Reset capital to 1,00,000.0 for all 4 segments
segments = ["equity", "commodity_futures", "commodity_options", "hero_zero"]
for seg in segments:
    key = f"available_margin_{seg}"
    cursor.execute("DELETE FROM system_state WHERE key = ?", (key,))
    cursor.execute("INSERT INTO system_state (key, value) VALUES (?, ?)", (key, "100000.0"))
    
conn.commit()

# Verify
t_cnt = cursor.execute("SELECT count(*) FROM trades").fetchone()[0]
p_cnt = cursor.execute("SELECT count(*) FROM positions").fetchone()[0]
s_cnt = cursor.execute("SELECT count(*) FROM signals").fetchone()[0]

print("=" * 60)
print(" DATABASE RESET SUCCESSFUL - CLEAN SLATE")
print("=" * 60)
print(f" Trades: {t_cnt}")
print(f" Positions: {p_cnt}")
print(f" Signals: {s_cnt}")
for seg in segments:
    key = f"available_margin_{seg}"
    val = cursor.execute("SELECT value FROM system_state WHERE key = ?", (key,)).fetchone()[0]
    print(f" {seg.replace('_', ' ').title()} Margin: Rs. {float(val):,.2f}")
print("=" * 60)
