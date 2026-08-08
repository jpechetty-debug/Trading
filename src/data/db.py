import sqlite3
import os
import json
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), 'trading_state.db')

class Persistence:
    def __init__(self):
        self.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.create_tables()

    def create_tables(self):
        cursor = self.conn.cursor()
        
        # 1. Trade Log
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                ticker TEXT,
                action TEXT,
                price REAL,
                shares INTEGER,
                pnl REAL,
                status TEXT
            )
        ''')

        # 2. Portfolio State (Snapshot)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS portfolio_state (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        
        self.conn.commit()

    def log_trade(self, ticker, action, price, shares, pnl=0, status="OPEN"):
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT INTO trades (timestamp, ticker, action, price, shares, pnl, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (datetime.now().isoformat(), ticker, action, price, shares, pnl, status))
        self.conn.commit()

    def get_portfolio_state(self, key, default=None):
        cursor = self.conn.cursor()
        cursor.execute('SELECT value FROM portfolio_state WHERE key = ?', (key,))
        row = cursor.fetchone()
        if row:
            return json.loads(row[0])
        return default

    def set_portfolio_state(self, key, value):
        cursor = self.conn.cursor()
        value_json = json.dumps(value)
        cursor.execute('''
            INSERT OR REPLACE INTO portfolio_state (key, value)
            VALUES (?, ?)
        ''', (key, value_json))
        self.conn.commit()
    
    def get_open_positions(self):
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM trades WHERE status = "OPEN"')
        cols = [description[0] for description in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]

# Lazy singleton — avoids opening a DB connection on module import
_db = None

def get_db():
    global _db
    if _db is None:
        _db = Persistence()
    return _db
