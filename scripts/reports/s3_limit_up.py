"""
策略三:漲停放空策略

條件:
  1. 漲停價成交量 ÷ 當日總成交量 > 30%
  2. 市值 > 500 億
漲停價 = 參考價(收盤價−漲跌價差)×1.1,依跳動單位無條件捨去;收盤價必須鎖在漲停價。
漲停價成交量 = 分點日報裡,價格等於漲停價的所有分點買進股數加總(跟策略二共用同一次查詢,
不多花額度);當日總成交量 = TaiwanStockPrice 的成交股數。

每檔另外標示能不能融券放空(見 risk_tags.py:暫停融券賣出/停券預告/沒有融資融券資格)。
「平盤以下不得放空」限制找不到對漲停股適用的公開資料,所以沒有標,不用別的資料代替。
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from context import NO_MV, nodata, result  # noqa: E402

TITLE = "【策略三:漲停放空】漲停價成交量占當日>30%,市值>500億"
THRESHOLD = 0.30


def build(ctx):
    if ctx.big is None:
        return {"s3": nodata("s3", TITLE, NO_MV)}

    has_dama = ctx.conn.execute("SELECT 1 FROM daily_dama WHERE date=? LIMIT 1", (ctx.day,)).fetchone()
    if not has_dama:
        return {"s3": nodata("s3", TITLE, "今日無資料(今天的分點日報沒有抓到,算不出漲停價成交量)")}

    df = pd.read_sql(
        "SELECT stock_id, limit_price, vol_at_limit, total_vol FROM daily_limitup WHERE date=?",
        ctx.conn,
        params=[ctx.day],
    )
    df = df[df["stock_id"].isin(ctx.big) & (df["total_vol"] > 0)].copy()
    df["ratio"] = df["vol_at_limit"] / df["total_vol"]
    hit = df[df["ratio"] > THRESHOLD].sort_values("ratio", ascending=False)

    lines = []
    for i, r in enumerate(hit.itertuples(), 1):
        short = ctx.risk.short_label(r.stock_id) if ctx.risk else "放空資訊今日無資料"
        lines.append(
            f"{i}. {ctx.label(r.stock_id)} 漲停價{r.limit_price:,.2f} "
            f"漲停價成交占比{r.ratio * 100:.1f}%({r.vol_at_limit / 1000:,.0f}張/{r.total_vol / 1000:,.0f}張) "
            f"| {short}"
        )
    n_limit = len(df)
    if not lines:
        lines = [f"今天沒有符合條件的股票(漲停且市值>500億共 {n_limit} 檔,成交占比都不到30%)"]
    return {"s3": result("s3", TITLE, lines, hit["stock_id"].tolist())}
