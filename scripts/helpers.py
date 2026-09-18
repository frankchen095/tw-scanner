"""
helpers.py —— 報表功能共用的小工具:股票基本資料、股本快取、近期日期、文字表格排版

你不需要動這個檔案。
"""

import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import finmind_query  # noqa: E402


def recent_dates(conn, table, end_date, n):
    """回傳某張表裡,<= end_date 的最近 n 個交易日(由舊到新排序)。"""
    rows = conn.execute(
        f"SELECT DISTINCT date FROM {table} WHERE date <= ? ORDER BY date DESC LIMIT ?",
        (end_date, n),
    ).fetchall()
    return sorted(r[0] for r in rows)


def get_stock_info(conn, stock_ids=None, max_age_days=30):
    """回傳 {stock_id: (name, industry)}。第一次呼叫或超過 max_age_days 沒更新時,會重新整批抓取。"""
    row = conn.execute("SELECT MAX(updated) FROM stock_info").fetchone()
    stale = True
    if row and row[0]:
        stale = row[0] < (date.today() - timedelta(days=max_age_days)).isoformat()

    if stale:
        df = finmind_query("TaiwanStockInfo", quiet=True)
        if len(df):
            today = date.today().isoformat()
            has_name = "stock_name" in df.columns
            has_ind = "industry_category" in df.columns
            rows = []
            for r in df.itertuples():
                sid = str(getattr(r, "stock_id", ""))
                name = str(getattr(r, "stock_name", "")) if has_name else ""
                ind = str(getattr(r, "industry_category", "")) if has_ind else ""
                rows.append((sid, name, ind, today))
            conn.executemany(
                "INSERT OR REPLACE INTO stock_info VALUES (?,?,?,?)", rows
            )
            conn.commit()

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
    """回傳普通個股的代號集合(排除 ETF、ETN、以及查不到名稱的權證/債券等衍生商品)。"""
    get_stock_info(conn)  # 確保 stock_info 表格是新的
    rows = conn.execute(
        "SELECT stock_id FROM stock_info "
        "WHERE name != '' AND industry != '' AND industry NOT IN ('ETF', 'ETN')"
    ).fetchall()
    return {r[0] for r in rows}


def get_adj_close_range(conn, stock_ids, end_date, lookback_days=100):
    """回傳 {stock_id: (近N日還原收盤最高, 最低, 最新收盤)}。

    用「還原股價」(TaiwanStockPriceAdj)而不是原始股價,避免除權息、減資造成的
    價格跳空,讓高低點失真。只對少量股票(排進榜單的那幾十檔)即時查詢,不快取。
    """
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


def get_shares(conn, stock_ids, max_age_days=30):
    """回傳 {stock_id: 總股數(股)},用集保股權分散表加總估算,有本地快取。"""
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

    if to_fetch:
        print(f"    查股本(股權分散表):{len(to_fetch)} 檔尚未快取,逐檔查詢中…")
    for i, sid in enumerate(to_fetch, 1):
        start = (date.today() - timedelta(days=90)).isoformat()
        end = date.today().isoformat()
        df = finmind_query("TaiwanStockHoldingSharesPer", sid, start, end, quiet=True)
        total = None
        if len(df) and "unit" in df.columns:
            latest_date = df["date"].max()
            total = df.loc[df["date"] == latest_date, "unit"].astype(float).sum()
        result[sid] = total
        if i % 10 == 0 or i == len(to_fetch):
            print(f"    查股本進度 {i}/{len(to_fetch)}")
        conn.execute(
            "INSERT OR REPLACE INTO shares_outstanding VALUES (?,?,?)",
            (sid, total, date.today().isoformat()),
        )
        conn.commit()
        time.sleep(0.3)

    return result


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
    """把 headers + rows 排成等寬字型的文字表格(配合 Telegram 的 <pre> 使用)。"""
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
