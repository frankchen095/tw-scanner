"""
策略一(創新高策略)+ 策略五(創新高)——兩個策略共用同一批還原股價,所以放在一起算

定義(都用「還原收盤價」,避免除權息、減資造成的價格跳空):
  近 60 個交易日 = 含今天往前數 60 個有交易的日子
  近 60 日高點   = 這 60 天『收盤價』的最高值(用收盤價,不是盤中最高價)

策略一:市值>500億,今天收盤 < 近60日高點,且 收盤×1.03 >= 近60日高點
        (再漲 3% 以內就能創 60 日新高),依「距高點」由小到大排序,全部列出
策略五:市值>500億,今天收盤 >= 近60日收盤高點(今天創 60 日新高)

還原股價資料不足 60 個交易日(新上市)或缺今天資料的股票會略過,但會在報表最後明確列出
略過幾檔,不會默默不見。
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from context import NO_MV, nodata, result  # noqa: E402
from helpers import get_adj_history  # noqa: E402

T1 = "【策略一:創新高策略】市值>500億,再漲3%內就創60日新高(距離由近到遠)"
T5 = "【策略五:創新高】市值>500億,今日收盤創近60個交易日新高"


def build(ctx):
    if ctx.big is None:
        return {"s1": nodata("s1", T1, NO_MV), "s5": nodata("s5", T5, NO_MV)}

    ids = sorted(ctx.big)
    hist = get_adj_history(ids, ctx.day, n_rows=60)

    s1, s5, skipped = [], [], []
    for sid in ids:
        df = hist.get(sid)
        if df is None or len(df) < 60 or df["date"].iloc[-1] != ctx.day:
            skipped.append(sid)
            continue
        close, hi = float(df["close"].iloc[-1]), float(df["close"].max())
        if close >= hi:
            s5.append(sid)
        elif close * 1.03 >= hi:
            s1.append((sid, hi / close - 1))

    px = pd.read_sql(
        "SELECT stock_id, close, spread FROM daily_price WHERE date=?", ctx.conn, params=[ctx.day]
    ).set_index("stock_id")

    def real_close(sid):
        return float(px.loc[sid, "close"]) if sid in px.index else float("nan")

    def chg(sid):
        if sid not in px.index or pd.isna(px.loc[sid, "spread"]):
            return ""
        c, s = float(px.loc[sid, "close"]), float(px.loc[sid, "spread"])
        return f" ({s / (c - s) * 100:+.1f}%)" if c - s else ""

    s1.sort(key=lambda x: x[1])
    lines1 = [
        f"{i}. {ctx.label(sid)} 收{real_close(sid):,.2f} 距60日高 {gap * 100:.1f}%"
        for i, (sid, gap) in enumerate(s1, 1)
    ] or ["今天沒有符合條件的股票"]

    s5.sort(key=lambda sid: sid)
    lines5 = [f"{i}. {ctx.label(sid)} 收{real_close(sid):,.2f}{chg(sid)}" for i, sid in enumerate(s5, 1)] or [
        "今天沒有符合條件的股票"
    ]

    if skipped:
        names = "、".join(ctx.name(s) or s for s in skipped[:8])
        more = f"…等{len(skipped)}檔" if len(skipped) > 8 else ""
        note = f"(另有 {len(skipped)} 檔市值>500億的股票因還原股價不足60日或缺今天資料而無法判斷:{names}{more})"
        lines1.append(note)
        lines5.append(note)

    return {
        "s1": result("s1", T1, lines1, [sid for sid, _ in s1]),
        "s5": result("s5", T5, lines5, s5),
    }
