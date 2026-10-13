import os
import sqlite3

from app.config import INITIAL_CAPITAL

db_path = os.path.join(os.path.dirname(__file__), "paper_trading.db")
conn = sqlite3.connect(db_path, timeout=15.0)
try:
    conn.execute("PRAGMA busy_timeout = 10000;")
    cursor = conn.cursor()

    # Explicit destructive reset: remove only the paper-trading ledger, not subscribers.
    cursor.execute("DELETE FROM trades")
    cursor.execute("DELETE FROM positions")
    cursor.execute("DELETE FROM signals")
    cursor.execute("DELETE FROM commands WHERE status IN ('DONE', 'PENDING')")

    # Remove obsolete balances so they cannot be mistaken for distinct segment budgets.
    for segment in ("equity", "commodity_futures", "commodity_options", "hero_zero"):
        cursor.execute("DELETE FROM system_state WHERE key = ?", (f"available_margin_{segment}",))
    cursor.execute(
        """INSERT INTO system_state (key, value) VALUES (?, ?)
           ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
        ("available_margin_total", str(float(INITIAL_CAPITAL))),
    )

    conn.commit()
    trades_count = cursor.execute("SELECT count(*) FROM trades").fetchone()[0]
    positions_count = cursor.execute("SELECT count(*) FROM positions").fetchone()[0]
    signals_count = cursor.execute("SELECT count(*) FROM signals").fetchone()[0]
    cash = cursor.execute(
        "SELECT value FROM system_state WHERE key = 'available_margin_total'"
    ).fetchone()[0]

    print("=" * 60)
    print(" DATABASE RESET SUCCESSFUL - CLEAN PAPER-TRADING SLATE")
    print("=" * 60)
    print(f" Trades: {trades_count}")
    print(f" Positions: {positions_count}")
    print(f" Signals: {signals_count}")
    print(f" Shared Available Cash: Rs. {float(cash):,.2f}")
    print("=" * 60)
finally:
    conn.close()
