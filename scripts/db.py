"""
db.py —— 本地資料庫(SQLite)

每天抓到的資料存在這裡(data/scanner.db),這樣「近5日」、「連續3天」這類統計
不用每次都重新打 API,只要讀本地資料庫加總就好。

你不需要動這個檔案。
"""

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "scanner.db"
DB_PATH.parent.mkdir(exist_ok=True)

DAMA_TRADER_ID = "1470"  # 台灣摩根士丹利(大摩)的分點代號,功能1專用

# ---- 功能5用的追蹤分點名單 ----
# 由 fenpoint 專案(分點勝率工作台)回測產生,見 data/tracked_brokers.json 和
# fenpoint/scripts/08_export_tracked_brokers.py。載不到就當作沒有追蹤名單
# (功能5會顯示提示,不會讓整支程式當掉)。
TRACKED_BROKERS_PATH = ROOT / "data" / "tracked_brokers.json"


def load_tracked_brokers():
    """回傳 (trader_id -> 營業處資訊 dict) 的對照表。找不到檔案就回傳空 dict。"""
    if not TRACKED_BROKERS_PATH.exists():
        return {}
    groups = json.loads(TRACKED_BROKERS_PATH.read_text(encoding="utf-8"))
    out = {}
    for g in groups:
        for tid in g["trader_ids"]:
            out[tid] = g
    return out


TRACKED_BROKERS = load_tracked_brokers()
ALL_WATCHED_TRADER_IDS = {DAMA_TRADER_ID} | set(TRACKED_BROKERS)


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
            date TEXT, stock_id TEXT, trader_id TEXT, price REAL, buy REAL, sell REAL,
            PRIMARY KEY (date, stock_id, trader_id)
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
    # 舊版 daily_dama 沒有 trader_id 欄位(只追蹤大摩一家);偵測到舊表就整個重建,
    # 反正大摩分點資料本來就不回補歷史,重來也只是少個幾天份,幾天內會自動補齊。
    cols = [r[1] for r in conn.execute("PRAGMA table_info(daily_dama)")]
    if "trader_id" not in cols:
        conn.executescript("DROP TABLE daily_dama;")
        conn.executescript(
            """
            CREATE TABLE daily_dama (
                date TEXT, stock_id TEXT, trader_id TEXT, price REAL, buy REAL, sell REAL,
                PRIMARY KEY (date, stock_id, trader_id)
            );
            CREATE INDEX IF NOT EXISTS ix_dama_sd ON daily_dama(stock_id, date);
            """
        )
        conn.commit()
        print("  (偵測到舊版 daily_dama 表格,已升級成可追蹤多個分點的新格式)")
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
