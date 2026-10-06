"""
策略四:當日營收公布

列出「今天新公布月營收」的股票(市值>500億),依 MoM 由大到小排序。每檔一行:
  個股 X月營收:X億 MoM+幾%(創歷史新高 / 創 N 個月新高 / 創歷史新低 / 創 N 個月新低)

資料:FinMind TaiwanStockMonthRevenue。『當天公布』用每日快照比對:資料庫裡原本沒有、
FinMind 入庫日不早於上一個交易日的,first_seen 記為今天(見 fetchers.fetch_month_revenue)。
晚上才公布的公司會在隔天出現。

新高新低的算法(營收歷史不足 12 個月的公司不標):
  - 本月營收大於(小於)資料庫裡所有更早的月份 → 創歷史新高(新低)
  - 否則 N = 本月 + 往前連續『比本月低(高)』的月數(遇到月份缺口就停),N >= 12 才標「創N個月新高(新低)」
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from context import NO_MV, nodata, result  # noqa: E402

TITLE = "【策略四:當日營收公布】市值>500億,依MoM由大到小"


def _flag(ords, vals):
    """ords:月份序號(年*12+月)由舊到新;vals:營收。回傳 (標示文字, 是否新高)。"""
    n = len(vals)
    if n < 12:
        return "", False
    cur, prior = vals[-1], vals[:-1]
    if cur > max(prior):
        return "創歷史新高", True
    if cur < min(prior):
        return "創歷史新低", False

    def run(better):
        k = 0
        for i in range(n - 2, -1, -1):
            if ords[i] != ords[i + 1] - 1 or not better(vals[i], cur):
                break
            k += 1
        return k + 1

    n_high = run(lambda prev, c: prev < c)
    if n_high >= 12:
        return f"創{n_high}個月新高", True
    n_low = run(lambda prev, c: prev > c)
    if n_low >= 12:
        return f"創{n_low}個月新低", False
    return "", False


def build(ctx):
    if ctx.big is None:
        return {"s4": nodata("s4", TITLE, NO_MV)}

    new = pd.read_sql(
        "SELECT stock_id, revenue_year, revenue_month FROM month_revenue WHERE first_seen=?",
        ctx.conn,
        params=[ctx.day],
    )
    new = new[new["stock_id"].isin(ctx.big)]
    if new.empty:
        return {"s4": result("s4", TITLE, ["今天沒有新公布月營收的公司(市值>500億)"])}

    new["ord"] = new["revenue_year"] * 12 + new["revenue_month"]
    new = new.sort_values("ord").groupby("stock_id").tail(1)  # 同一檔若今天看到多個月份,取最新月份

    qm = ",".join("?" * len(new))
    hist = pd.read_sql(
        f"SELECT stock_id, revenue_year, revenue_month, revenue FROM month_revenue WHERE stock_id IN ({qm})",
        ctx.conn,
        params=new["stock_id"].tolist(),
    )
    hist["ord"] = hist["revenue_year"] * 12 + hist["revenue_month"]

    rows = []
    for r in new.itertuples():
        h = hist[(hist["stock_id"] == r.stock_id) & (hist["ord"] <= r.ord)].sort_values("ord")
        ords, vals = h["ord"].tolist(), h["revenue"].tolist()
        if not vals or ords[-1] != r.ord:
            continue
        mom = (vals[-1] / vals[-2] - 1) * 100 if len(vals) >= 2 and ords[-2] == ords[-1] - 1 and vals[-2] > 0 else None
        flag, is_high = _flag(ords, vals)
        rows.append((r.stock_id, r.revenue_month, vals[-1], mom, flag, is_high))

    rows.sort(key=lambda x: (x[3] is None, -(x[3] or 0)))
    lines = []
    for sid, month, rev, mom, flag, _ in rows:
        mom_txt = f"MoM{mom:+.1f}%" if mom is not None else "MoM無資料"
        flag_txt = f"({flag})" if flag else ""
        lines.append(f"{ctx.label(sid)} {month}月營收:{rev / 1e8:,.2f}億 {mom_txt}{flag_txt}")

    picks_high = [x[0] for x in rows if x[5]]
    return {"s4": result("s4", TITLE, lines, picks_high, all_picks=[x[0] for x in rows])}
