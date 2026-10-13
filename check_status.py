import datetime
import sqlite3
import subprocess

print("=== CHECKING RUNNING PROCESSES ===")
try:
    import psutil
    found = False
    for p in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            cmd = " ".join(p.info['cmdline'] or [])
            if "app.main" in cmd or "streamlit" in cmd:
                print(f"  [RUNNING] PID {p.info['pid']}: {cmd}")
                found = True
        except Exception:
            pass
    if not found:
        print("  [NOT RUNNING] Neither app.main nor streamlit were found in running processes.")
except ImportError:
    # fallback to tasklist
    res = subprocess.run(["tasklist"], capture_output=True, text=True)
    print(res.stdout[:500])

print("\n=== CHECKING TODAY'S DATABASE ENTRIES ===")
today_str = datetime.date.today().isoformat()
print(f"Current Date: {today_str}")

conn = sqlite3.connect("paper_trading.db")
conn.row_factory = sqlite3.Row
c = conn.cursor()

signals = c.execute("SELECT * FROM signals WHERE received_at LIKE ? ORDER BY received_at DESC", (f"{today_str}%",)).fetchall()
print(f"Signals recorded today: {len(signals)}")
for s in signals:
    print(f"  Signal: {s['underlying']} {s['strike']} {s['option_type']} | Status: {s['status']} | Time: {s['received_at']}")

positions = c.execute("SELECT * FROM positions WHERE entry_time LIKE ? ORDER BY entry_time DESC", (f"{today_str}%",)).fetchall()
print(f"\nPositions recorded today: {len(positions)}")
for pos in positions:
    print(f"  Position: {pos['symbol']} | Status: {pos['status']} | Entry: {pos['entry_price']} | Exit: {pos['exit_price']} ({pos['exit_reason']})")

trades = c.execute("SELECT * FROM trades WHERE timestamp LIKE ? ORDER BY timestamp DESC", (f"{today_str}%",)).fetchall()
print(f"\nTrades settled today: {len(trades)}")
for t in trades:
    print(f"  Trade #{t['id']}: Gross PnL: Rs.{t['gross_pnl']:.2f} | Net PnL: Rs.{t['net_pnl']:.2f}")

state = c.execute("SELECT * FROM system_state").fetchall()
print("\nSystem State:")
for row in state:
    print(f"  {row['key']}: {row['value']}")
