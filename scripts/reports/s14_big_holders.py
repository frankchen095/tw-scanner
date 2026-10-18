"""
策略十四(F):大戶持股比變化(集保股權分散,每週)

資料:FinMind TaiwanStockHoldingSharesPer(集保戶股權分散表,每週一份,全市場某一天一次查),
fetchers.fetch_holding_dist 每天檢查有沒有新的一週,存成 holding_dist_weekly(只存加總後的大戶/散戶持股比)。
定義(集保級距,以實際資料的級距名稱為準):
  大戶 = 持股 1,000 張以上 = 級距 'more than 1,000,001'(1,000,001 股以上)的持股比
  散戶 = 持股 400 張以下 = 級距 1-999、1,000-5,000、5,001-10,000、10,001-15,000、15,001-20,000、
         20,001-30,000、30,001-40,000、40,001-50,000、50,001-100,000、100,001-200,000、200,001-400,000 的持股比合計

只在『新一週資料入庫的那一天』推播;其他天這段整個不出現。市值>500億。
正向榜(偏多,成效追蹤):大戶持股比連續 2 週上升(W2<W1<W0)、2 週合計上升 ≥ 0.5 個百分點,且散戶持股比 2 週合計下降;
    依大戶增幅由大到小。
反向榜前五(只顯示):大戶連 2 週下降(W2>W1>W0)、合計 ≥ 0.5 pp,且散戶 2 週合計上升。
週標記:符合條件的股票整週(到下次資料更新前)名稱後加〔大戶增〕/〔大戶減〕(由 risk_tags.py 統一加)。
〔大戶增〕不當交集榜的獨立類別(週頻資料跟日頻訊號混在一起會誤導),只靠標記顯示。
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from context import NO_MV, nodata, result  # noqa: E402
from helpers import BIG_MARKET_VALUE, get_market_values_latest  # noqa: E402

T_UP = "【策略十四:大戶持股比上升】集保週資料,市值>500億(偏多)"
T_DOWN = "【策略十四:大戶持股比下降 Top5】籌碼轉差(只顯示)"
MIN_PP = 0.5


def _latest_three(conn, day):
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT date FROM holding_dist_weekly WHERE date <= ? ORDER BY date DESC LIMIT 3", (day,))]


def _compute(conn, dates, big):
    """回傳 (up 清單, down 清單);每筆 (sid, b2, b1, b0, r2, r0)"""
    d0, d1, d2 = dates
    df = pd.read_sql(
        "SELECT date, stock_id, big_pct, retail_pct FROM holding_dist_weekly WHERE date IN (?,?,?)", conn, params=dates)
    b = df.pivot_table(index="stock_id", columns="date", values="big_pct")
    r = df.pivot_table(index="stock_id", columns="date", values="retail_pct")
    up, down = [], []
    for sid in b.index:
        if sid not in big or b.loc[sid].isna().any() or r.loc[sid].isna().any():
            continue
        b0, b1, b2 = (float(b.loc[sid, d]) for d in (d0, d1, d2))
        r0, r2 = float(r.loc[sid, d0]), float(r.loc[sid, d2])
        if b2 < b1 < b0 and b0 - b2 >= MIN_PP and r0 < r2:
            up.append((sid, b2, b1, b0, r2, r0))
        elif b2 > b1 > b0 and b2 - b0 >= MIN_PP and r0 > r2:
            down.append((sid, b2, b1, b0, r2, r0))
    up.sort(key=lambda x: -(x[3] - x[1 + 0]))
    down.sort(key=lambda x: -(x[1] - x[3]))
    return up, down


def weekly_flags(conn):
    """給 risk_tags 用:最近一週的 (〔大戶增〕集合, 〔大戶減〕集合)。資料不足 3 週回傳兩個空集合。"""
    last = conn.execute("SELECT MAX(date) FROM holding_dist_weekly").fetchone()[0]
    if not last:
        return set(), set()
    dates = _latest_three(conn, last)
    if len(dates) < 3:
        return set(), set()
    row = conn.execute("SELECT MAX(date) FROM daily_market_value").fetchone()
    mv, _ = get_market_values_latest(conn, row[0]) if row and row[0] else (None, None)
    if not mv:
        return set(), set()
    big = {s for s, v in mv.items() if v > BIG_MARKET_VALUE}
    up, down = _compute(conn, dates, big)
    return {x[0] for x in up}, {x[0] for x in down}


def build(ctx, always=False):
    """只在新一週資料入庫的那天回傳結果;其他天回傳 {}。always=True(測試用)不管有沒有新資料都算。"""
    last = ctx.conn.execute("SELECT MAX(date) FROM holding_dist_weekly WHERE date <= ?", (ctx.day,)).fetchone()[0]
    if not last:
        return {}
    first_seen = ctx.conn.execute(
        "SELECT first_seen FROM holding_dist_weekly WHERE date=? LIMIT 1", (last,)).fetchone()[0]
    if first_seen != ctx.day and not always:
        return {}
    if ctx.big is None:
        return {"s14": nodata("s14", T_UP, NO_MV)}
    dates = _latest_three(ctx.conn, ctx.day)
    if len(dates) < 3:
        return {"s14": nodata("s14", T_UP, "今日無資料(集保週資料不足 3 週)")}
    up, down = _compute(ctx.conn, dates, ctx.big)
    lines_up = [
        f"{i}. {ctx.label(sid)} 大戶 {b2:.2f}% → {b1:.2f}% → {b0:.2f}%({b0 - b2:+.2f} pp)"
        f" 散戶 {r2:.2f}% → {r0:.2f}%({r0 - r2:+.2f} pp)"
        for i, (sid, b2, b1, b0, r2, r0) in enumerate(up, 1)
    ] or ["本週沒有符合條件的股票"]
    down = down[:5]
    lines_down = [
        f"{i}. {ctx.label(sid)} 大戶 {b2:.2f}% → {b1:.2f}% → {b0:.2f}%({b0 - b2:+.2f} pp)"
        f" 散戶 {r2:.2f}% → {r0:.2f}%({r0 - r2:+.2f} pp)"
        for i, (sid, b2, b1, b0, r2, r0) in enumerate(down, 1)
    ] or ["本週沒有符合條件的股票"]
    lines_up.insert(0, f"(集保資料日 {dates[0]};大戶=持股1,000張以上,散戶=400張以下)")

    def rows(lst):
        return [{"sid": sid, "b2": b2, "b1": b1, "b0": b0, "r2": r2, "r0": r0} for sid, b2, b1, b0, r2, r0 in lst]

    return {
        "s14": result("s14", T_UP, lines_up, [x[0] for x in up], rows=rows(up), data_date=dates[0]),
        "s14_down": result("s14_down", T_DOWN, lines_down, [], rows=rows(down)),
    }
