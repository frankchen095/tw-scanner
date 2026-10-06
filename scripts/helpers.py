"""
helpers.py —— 報表功能共用的小工具:股票基本資料、市值、還原股價、股本快取、
近期日期、台股跳動單位/漲停價、文字排版

你不需要動這個檔案。
"""

import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal, ROUND_FLOOR
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import finmind_query  # noqa: E402

# 這些產業別不是普通股,排除。(名單依 TaiwanStockInfo 的 industry_category 實測)
EXCLUDE_INDUSTRIES = {"ETF", "ETN", "存託憑證"}
BIG_MARKET_VALUE = 5e10  # 市值門檻:500 億


def recent_dates(conn, table, end_date, n):
    """回傳某張表裡,<= end_date 的最近 n 個交易日(由舊到新排序)。"""
    rows = conn.execute(
        f"SELECT DISTINCT date FROM {table} WHERE date <= ? ORDER BY date DESC LIMIT ?",
        (end_date, n),
    ).fetchall()
    return sorted(r[0] for r in rows)


def parallel_map(fn, items, workers=4):
    """用幾條執行緒同時跑 fn(item),結果順序跟 items 一致。fn 出錯的項目回傳 None,不會中斷全部。"""

    def safe(x):
        try:
            return fn(x)
        except Exception as e:  # noqa: BLE001
            print(f"    處理 {x} 時出錯: {e}")
            return None

    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(safe, items))


# ---------- 股票基本資料 ----------


def _refresh_stock_info(conn):
    df = finmind_query("TaiwanStockInfo", quiet=True)
    if df.empty:
        return
    today = date.today().isoformat()
    df["stock_id"] = df["stock_id"].astype(str)
    rows = []
    # 同一檔股票在 TaiwanStockInfo 會出現多列(例如 2330 有「半導體業」「電子工業」兩列),
    # 只要任何一列是 ETF/ETN/存託憑證就整檔排除。
    for sid, g in df.groupby("stock_id", sort=False):
        name = str(g["stock_name"].iloc[0])
        typ = str(g["type"].iloc[0])
        industries = [str(x) for x in g["industry_category"].dropna()]
        is_common = (
            typ in ("twse", "tpex")
            and len(sid) == 4
            and sid.isdigit()
            and sid[0] != "0"
            and not any(i in EXCLUDE_INDUSTRIES for i in industries)
        )
        rows.append((sid, name, industries[0] if industries else "", today, typ, 1 if is_common else 0))
    conn.executemany(
        "INSERT OR REPLACE INTO stock_info (stock_id, name, industry, updated, type, is_common) "
        "VALUES (?,?,?,?,?,?)",
        rows,
    )
    conn.commit()


def _ensure_stock_info(conn, max_age_days=7):
    row = conn.execute("SELECT MAX(updated), SUM(is_common IS NULL) FROM stock_info").fetchone()
    stale = (not row) or (row[0] is None) or row[0] < (date.today() - timedelta(days=max_age_days)).isoformat()
    if stale or (row[1] or 0) > 0:
        _refresh_stock_info(conn)


def get_stock_info(conn, stock_ids=None, max_age_days=7):
    """回傳 {stock_id: (name, industry)}。太久沒更新時,會重新抓一次全部股票基本資料。"""
    _ensure_stock_info(conn, max_age_days)
    if stock_ids is None:
        cur = conn.execute("SELECT stock_id, name, industry FROM stock_info")
    else:
        qmarks = ",".join("?" * len(stock_ids))
        cur = conn.execute(
            f"SELECT stock_id, name, industry FROM stock_info WHERE stock_id IN ({qmarks})",
            list(stock_ids),
        )
    return {r[0]: (r[1], r[2]) for r in cur.fetchall()}


def get_valid_stock_ids(conn):
    """回傳上市/上櫃普通股的代號集合(排除興櫃、ETF、ETN、權證、存託憑證、特別股)。"""
    _ensure_stock_info(conn)
    rows = conn.execute("SELECT stock_id FROM stock_info WHERE is_common=1").fetchall()
    return {r[0] for r in rows}


# ---------- 市值 ----------


def get_market_values(conn, day):
    """回傳 {stock_id: 市值(元)}(只含普通股),該日在資料庫裡沒有市值資料時回傳 None。"""
    rows = conn.execute(
        "SELECT stock_id, market_value FROM daily_market_value WHERE date=?", (day,)
    ).fetchall()
    if not rows:
        return None
    return {r[0]: r[1] for r in rows}


# ---------- 還原股價 ----------

_ADJ_CACHE = {}


def get_adj_history(stock_ids, end_date, n_rows=60, workers=4):
    """回傳 {stock_id: DataFrame(date, close)},最近 n_rows 個交易日的還原收盤價(由舊到新)。

    用 TaiwanStockPriceAdj(還原股價),避免除權息、減資造成的價格跳空讓高低點失真。
    FinMind 不支援這個資料集「全市場+日期區間」一次查,所以一檔一檔查(用幾條執行緒並行)。
    抓不到的股票回傳空 DataFrame。同一次執行內重複要同一檔會直接用快取。
    """
    start = (date.fromisoformat(end_date) - timedelta(days=int(n_rows * 1.7) + 10)).isoformat()
    need = [s for s in stock_ids if (s, end_date, n_rows) not in _ADJ_CACHE]

    def one(sid):
        df = finmind_query("TaiwanStockPriceAdj", sid, start, end_date, quiet=True)
        if df.empty or "close" not in df.columns:
            return sid, None
        df = df[["date", "close"]].sort_values("date").tail(n_rows).reset_index(drop=True)
        return sid, df

    for res in parallel_map(one, need, workers=workers):
        if res is None:
            continue
        sid, df = res
        _ADJ_CACHE[(sid, end_date, n_rows)] = df
    return {s: _ADJ_CACHE.get((s, end_date, n_rows)) for s in stock_ids}


def get_adj_close_range(conn, stock_ids, end_date, lookback_days=100):
    """(功能1舊版用)回傳 {stock_id: (近N日還原收盤最高, 最低, 最新收盤)}。"""
    start = (date.fromisoformat(end_date) - timedelta(days=int(lookback_days * 1.6))).isoformat()
    result = {}
    for sid in stock_ids:
        df = finmind_query("TaiwanStockPriceAdj", sid, start, end_date, quiet=True)
        if len(df) and "close" in df.columns:
            d = df.sort_values("date").tail(lookback_days)
            result[sid] = (d["close"].max(), d["close"].min(), d["close"].iloc[-1])
        else:
            result[sid] = (None, None, None)
        time.sleep(0.3)
    return result


# ---------- 股本(舊版功能1用;市值改用 TaiwanStockMarketValue) ----------


def get_shares(conn, stock_ids, max_age_days=30):
    """回傳 {stock_id: 總股數(股)},用集保股權分散表的『total』列,有本地快取。

    注意:該表除了各持股級距,還有一列 total(合計)和一列「差異數調整」,不能把所有列
    全部加起來(會變兩倍)。
    """
    result = {}
    to_fetch = []
    cutoff = (date.today() - timedelta(days=max_age_days)).isoformat()

    for sid in stock_ids:
        row = conn.execute(
            "SELECT shares, updated FROM shares_outstanding WHERE stock_id=?", (sid,)
        ).fetchone()
        if row and row[1] and row[1] >= cutoff:
            result[sid] = row[0]
        else:
            to_fetch.append(sid)

    for sid in to_fetch:
        start = (date.today() - timedelta(days=90)).isoformat()
        end = date.today().isoformat()
        df = finmind_query("TaiwanStockHoldingSharesPer", sid, start, end, quiet=True)
        total = None
        if len(df) and "unit" in df.columns:
            latest = df[df["date"] == df["date"].max()]
            tot_row = latest[latest["HoldingSharesLevel"].astype(str).str.lower() == "total"]
            if len(tot_row):
                total = float(tot_row["unit"].iloc[0])
            else:
                levels = latest[~latest["HoldingSharesLevel"].astype(str).str.contains("差異|total", case=False)]
                total = float(levels["unit"].astype(float).sum())
        result[sid] = total
        conn.execute(
            "INSERT OR REPLACE INTO shares_outstanding VALUES (?,?,?)",
            (sid, total, date.today().isoformat()),
        )
        conn.commit()
        time.sleep(0.3)

    return result


# ---------- 台股跳動單位與漲停價 ----------


def _tick(price):
    if price < 10:
        return Decimal("0.01")
    if price < 50:
        return Decimal("0.05")
    if price < 100:
        return Decimal("0.1")
    if price < 500:
        return Decimal("0.5")
    if price < 1000:
        return Decimal("1")
    return Decimal("5")


def limit_up_price(ref_price):
    """用參考價(昨收;除權息日是參考價)算漲停價:參考價×1.1,無條件捨去到該價位的最小跳動單位。"""
    raw = Decimal(str(ref_price)) * Decimal("1.1")
    tick = _tick(raw)
    return float((raw / tick).to_integral_value(rounding=ROUND_FLOOR) * tick)


# ---------- 文字格式 ----------


def fmt_yi(amount, signed=False):
    """元 → 『X.XX億』。"""
    v = amount / 1e8
    return f"{v:+,.2f}億" if signed else f"{v:,.2f}億"


def fmt_wan(amount, signed=False):
    """元 → 『X,XXX萬』。"""
    v = amount / 1e4
    return f"{v:+,.0f}萬" if signed else f"{v:,.0f}萬"


def _width(s):
    """粗略估計顯示寬度:中文字算2、其他算1,用來對齊等寬字型表格。"""
    w = 0
    for ch in s:
        w += 2 if ord(ch) > 0x2E80 else 1
    return w


def _pad(s, width, align="left"):
    gap = max(width - _width(s), 0)
    if align == "right":
        return " " * gap + s
    return s + " " * gap


def format_table(headers, rows, aligns=None):
    """把 headers + rows 排成等寬字型的文字表格(給舊版 Telegram 報表用)。"""
    if aligns is None:
        aligns = ["left"] * len(headers)
    str_rows = [[str(c) for c in row] for row in rows]
    all_rows = [list(map(str, headers))] + str_rows
    widths = [max(_width(r[i]) for r in all_rows) for i in range(len(headers))]

    lines = ["  ".join(_pad(h, w) for h, w in zip(map(str, headers), widths))]
    lines.append("-" * (sum(widths) + 2 * (len(widths) - 1)))
    for row in str_rows:
        lines.append("  ".join(_pad(c, w, a) for c, w, a in zip(row, widths, aligns)))
    return "\n".join(lines)
