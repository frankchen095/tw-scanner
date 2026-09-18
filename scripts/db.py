"""
db.py —— 本地資料庫(SQLite)

每天抓到的資料存在這裡(data/scanner.db),這樣「近5日」、「連續3天」這類統計
不用每次都重新打 API,只要讀本地資料庫加總就好。

你不需要動這個檔案。
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "scanner.db"
DB_PATH.parent.mkdir(exist_ok=True)

DAMA_TRADER_ID = "1470"  # 台灣摩根士丹利(大摩)的分點代號


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS daily_price (
            date TEXT, stock_id TEXT,
            open REAL, high REAL, low REAL, close REAL,
            volume REAL, money REAL,
            PRIMARY KEY (date, stock_id)
        );
        CREATE INDEX IF NOT EXISTS ix_price_sd ON daily_price(stock_id, date);

        CREATE TABLE IF NOT EXISTS daily_institutional (
            date TEXT, stock_id TEXT, name TEXT, buy REAL, sell REAL,
            PRIMARY KEY (date, stock_id, name)
        );
        CREATE INDEX IF NOT EXISTS ix_inst_sd ON daily_institutional(stock_id, date);

        CREATE TABLE IF NOT EXISTS daily_dama (
            date TEXT, stock_id TEXT, price REAL, buy REAL, sell REAL,
            PRIMARY KEY (date, stock_id)
        );
        CREATE INDEX IF NOT EXISTS ix_dama_sd ON daily_dama(stock_id, date);

        CREATE TABLE IF NOT EXISTS stock_info (
            stock_id TEXT PRIMARY KEY, name TEXT, industry TEXT, updated TEXT
        );

        CREATE TABLE IF NOT EXISTS shares_outstanding (
            stock_id TEXT PRIMARY KEY, shares REAL, updated TEXT
        );
        """
    )
    conn.commit()
    return conn


KEEP_DAYS = {
    "daily_price": 30,  # 功能4「近20日」要用,「近100日高低點」改成即時查還原股價,不用本地存
    "daily_institutional": 15,  # 最多只需要近5日,留15天當緩衝
    "daily_dama": 15,
}


def prune(conn, keep_days=None):
    """只留最近 N 個『已經有資料的交易日』,避免資料庫一直長大。"""
    keep_days = keep_days or KEEP_DAYS
    for table, n in keep_days.items():
        dates = [
            r[0]
            for r in conn.execute(f"SELECT DISTINCT date FROM {table} ORDER BY date DESC")
        ]
        if len(dates) > n:
            cutoff = dates[n - 1]
            conn.execute(f"DELETE FROM {table} WHERE date < ?", (cutoff,))
    conn.commit()
