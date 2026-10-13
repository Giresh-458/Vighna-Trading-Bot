import sqlite3
conn = sqlite3.connect('paper_trading.db')
print(conn.execute('SELECT name FROM sqlite_master WHERE type="table"').fetchall())
