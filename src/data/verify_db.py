import sqlite3
import os

db_path = r'd:\Tradeidesa\Trading\indian_stock_ai\src\data\trading_state.db'
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
tables = cursor.fetchall()
print(f"Tables found: {tables}")

conn.close()
