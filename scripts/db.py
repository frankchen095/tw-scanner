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

# ---- 追蹤分點名單 ----
# 由 fenpoint 專案(分點勝率工作台)回測產生,見 data/tracked_brokers.json 和
# fenpoint/scripts/08_export_tracked_brokers.py。載不到就當作沒有追蹤名單
# (策略二會顯示提示,不會讓整支程式當掉)。
TRACKED_BROKERS_PATH = ROOT / "data" / "tracked_brokers.json"


TOP_N_TRACKED_BROKERS = 40  # 策略二只用命中率前 N 名(tracked_brokers.json 已經照命中率高到低排序)


def load_tracked_broker_groups():
    """回傳 tracked_brokers.json 的原始清單(已照命中率由高到低排序)。找不到檔案就回傳空清單。"""
    if not TRACKED_BROKERS_PATH.exists():
        return []
    return json.loads(TRACKED_BROKERS_PATH.read_text(encoding="utf-8"))


def load_tracked_brokers():
    """回傳 (trader_id -> 營業處資訊 dict) 的對照表。"""
    out = {}
    for g in TRACKED_BROKER_GROUPS:
        for tid in g["trader_ids"]:
            out[tid] = g
    return out


TRACKED_BROKER_GROUPS = load_tracked_broker_groups()
TRACKED_BROKERS = load_tracked_brokers()
ALL_WATCHED_TRADER_IDS = {DAMA_TRADER_ID} | set(TRACKED_BROKERS)

# 命中率前 40 名的分點代碼(抓資料階段還是存全部追蹤分點,只有報表這裡篩前40)
TOP40_TRADER_IDS = {
    tid for g in TRACKED_BROKER_GROUPS[:TOP_N_TRACKED_BROKERS] for tid in g["trader_ids"]
}

# ---- 庫藏股分點名單(策略七 B) ----
# 由 scripts/06_build_buyback_brokers.py 用 fenpoint 的分點歷史資料推算,見該檔說明。
BUYBACK_BROKERS_PATH = ROOT / "data" / "buyback_brokers.json"


def load_buyback_brokers():
    """回傳 list[dict]:stock_id, trader_id, trader_name, start, end, error_pct …。沒有檔案就回傳空 list。"""
    if not BUYBACK_BROKERS_PATH.exists():
        return []
    return json.loads(BUYBACK_BROKERS_PATH.read_text(encoding="utf-8"))


def buyback_brokers_by_stock():
    """{stock_id: {trader_id}}"""
    out = {}
    for e in load_buyback_brokers():
        out.setdefault(str(e["stock_id"]), set()).add(str(e["trader_id"]))
    return out


SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_price (
    date TEXT, stock_id TEXT,
    open REAL, high REAL, low REAL, close REAL,
    volume REAL, money REAL, spread REAL,
    PRIMARY KEY (date, stock_id)
);
CREATE INDEX IF NOT EXISTS ix_price_sd ON daily_price(stock_id, date);

CREATE TABLE IF NOT EXISTS daily_institutional (
    date TEXT, stock_id TEXT, name TEXT, buy REAL, sell REAL,
    PRIMARY KEY (date, stock_id, name)
);
CREATE INDEX IF NOT EXISTS ix_inst_sd ON daily_institutional(stock_id, date);

-- 分點日報:FinMind 原始資料是「每個分點每個價位一列」,這裡存的是『每個分點每檔股票
-- 每天』加總後的結果(buy/sell 是股數,buy_amt/sell_amt 是各價位 股數×價格 加總的金額)。
CREATE TABLE IF NOT EXISTS daily_dama (
    date TEXT, stock_id TEXT, trader_id TEXT,
    buy REAL, sell REAL, buy_amt REAL, sell_amt REAL,
    PRIMARY KEY (date, stock_id, trader_id)
);
CREATE INDEX IF NOT EXISTS ix_dama_sd ON daily_dama(stock_id, date);

-- 漲停價成交量(策略三):limit_price 當天漲停價,vol_at_limit 該價位所有分點買進股數加總
CREATE TABLE IF NOT EXISTS daily_limitup (
    date TEXT, stock_id TEXT, limit_price REAL, vol_at_limit REAL, total_vol REAL,
    PRIMARY KEY (date, stock_id)
);

CREATE TABLE IF NOT EXISTS daily_market_value (
    date TEXT, stock_id TEXT, market_value REAL,
    PRIMARY KEY (date, stock_id)
);

-- is_common=1 代表「上市/上櫃普通股」(排除興櫃、ETF、ETN、權證、存託憑證、特別股)
CREATE TABLE IF NOT EXISTS stock_info (
    stock_id TEXT PRIMARY KEY, name TEXT, industry TEXT, updated TEXT,
    type TEXT, is_common INTEGER
);

CREATE TABLE IF NOT EXISTS shares_outstanding (
    stock_id TEXT PRIMARY KEY, shares REAL, updated TEXT
);

CREATE TABLE IF NOT EXISTS news_briefings (
    date TEXT PRIMARY KEY, content TEXT
);

-- 地緣分點(策略七 A):公司總部、券商分點的縣市 + 鄉鎮市區對照表,每月更新一次(見 geo.py)
CREATE TABLE IF NOT EXISTS company_geo (
    stock_id TEXT PRIMARY KEY, name TEXT, address TEXT, city TEXT, district TEXT, updated TEXT
);
CREATE TABLE IF NOT EXISTS broker_geo (
    trader_id TEXT PRIMARY KEY, name TEXT, address TEXT, city TEXT, district TEXT,
    is_foreign INTEGER, updated TEXT
);

-- 庫藏股買回計畫(公開資訊觀測站),用來標「庫藏股執行中」
-- done_flag:Y = 已執行完畢(已買回股數才有值)、N = 執行中
CREATE TABLE IF NOT EXISTS buyback_programs (
    stock_id TEXT, board_date TEXT, start_date TEXT, end_date TEXT, planned_shares REAL,
    bought_shares REAL, done_flag TEXT, market TEXT, updated TEXT,
    PRIMARY KEY (stock_id, board_date, start_date, end_date)
);

-- 哪幾天的報表已經推播成功。晚到的排程或重複觸發看到今天已推過,就不會再推一次。
CREATE TABLE IF NOT EXISTS pushed_reports (
    date TEXT PRIMARY KEY, sent_at TEXT, n_messages INTEGER
);

-- 月營收(單位:元)。first_seen = 我們第一次看到這筆的日期,拿來判斷「當天新公布」。
CREATE TABLE IF NOT EXISTS month_revenue (
    stock_id TEXT, revenue_year INTEGER, revenue_month INTEGER,
    revenue REAL, create_time TEXT, first_seen TEXT,
    PRIMARY KEY (stock_id, revenue_year, revenue_month)
);
"""


def _columns(conn, table):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def _migrate(conn):
    """升級舊版資料庫。每一步都可以重複執行,不會重複破壞資料。"""
    # 1. 舊版 daily_dama 把「同分點不同價位」的多列用主鍵蓋掉,只剩最後一個價位,
    #    金額嚴重少算。資料本來就是錯的,整張表重建,之後每天自動累積。
    if "buy_amt" not in _columns(conn, "daily_dama"):
        conn.executescript("DROP TABLE daily_dama;")
        conn.executescript(
            """
            CREATE TABLE daily_dama (
                date TEXT, stock_id TEXT, trader_id TEXT,
                buy REAL, sell REAL, buy_amt REAL, sell_amt REAL,
                PRIMARY KEY (date, stock_id, trader_id)
            );
            CREATE INDEX IF NOT EXISTS ix_dama_sd ON daily_dama(stock_id, date);
            """
        )
        print("  (daily_dama 已升級成『每分點每股每天加總』的新格式,舊資料因為有價位覆蓋的錯誤,已清除)")

    # 2. daily_price 新增 spread(漲跌價差,算漲停價的參考價用)
    if "spread" not in _columns(conn, "daily_price"):
        conn.execute("ALTER TABLE daily_price ADD COLUMN spread REAL")

    # 3. stock_info 新增 type / is_common,並強迫下次重新抓一次股票基本資料
    cols = _columns(conn, "stock_info")
    if "type" not in cols:
        conn.execute("ALTER TABLE stock_info ADD COLUMN type TEXT")
    if "is_common" not in cols:
        conn.execute("ALTER TABLE stock_info ADD COLUMN is_common INTEGER")
        conn.execute("UPDATE stock_info SET updated='1970-01-01'")

    # 3b. 庫藏股計畫表:舊版(開發中)沒有 done_flag,整張重建(資料都是公開資訊重新抓,沒有損失)
    if "done_flag" not in _columns(conn, "buyback_programs"):
        conn.executescript(
            """
            DROP TABLE buyback_programs;
            CREATE TABLE buyback_programs (
                stock_id TEXT, board_date TEXT, start_date TEXT, end_date TEXT, planned_shares REAL,
                bought_shares REAL, done_flag TEXT, market TEXT, updated TEXT,
                PRIMARY KEY (stock_id, board_date, start_date, end_date)
            );
            """
        )

    # 4. 股本快取有『集保表合計列被重複加總,股數變兩倍』的錯誤,清掉讓它重抓
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version < 1:
        conn.execute("DELETE FROM shares_outstanding")
        conn.execute("PRAGMA user_version = 1")

    conn.commit()


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


KEEP_DAYS = {
    "daily_price": 30,  # 「近20日」、成交金額比例要用;60日高低點改用還原股價即時查詢,不存這裡
    "daily_institutional": 30,  # 法人連續買超最多往回看30個交易日
    "daily_dama": 15,
    "daily_limitup": 30,
    "daily_market_value": 10,
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
