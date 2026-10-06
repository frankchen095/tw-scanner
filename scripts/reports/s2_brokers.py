"""
策略二:籌碼追蹤——前 40 大分點(data/tracked_brokers.json,依 fenpoint 回測命中率排序)

把這 40 家營業處(同一家的多個分點代碼算同一組)「當天」和「近 3 天」對同一檔股票的
淨買賣金額加總(各價位 (買−賣)×價格 加總):
  今日合計買超 前十名、今日合計賣超 前十名、近 3 日合計買超 前十名
命中率是 2023–2026 回測的歷史統計,不是保證,僅供參考。

注意:這份只掃分點日報有查到的股票(成交金額前 300 名 + 當天漲停且市值>500億的股票),
不是全市場。
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from context import nodata, result  # noqa: E402
from db import TOP40_TRADER_IDS, TOP_N_TRACKED_BROKERS, TRACKED_BROKERS  # noqa: E402
from helpers import fmt_wan, recent_dates  # noqa: E402

TOP_N = 10
T_BUY = f"【策略二:前{TOP_N_TRACKED_BROKERS}大分點 今日合計買超 Top{TOP_N}】"
T_SELL = f"【策略二:前{TOP_N_TRACKED_BROKERS}大分點 今日合計賣超 Top{TOP_N}】"
T_BUY3 = f"【策略二:前{TOP_N_TRACKED_BROKERS}大分點 近3日合計買超 Top{TOP_N}】"


def _by_stock(conn, dates):
    """回傳 DataFrame:stock_id, net(合計淨額,元), n_buy(淨買超的分點組數), n_sell(淨賣超的分點組數)"""
    qm = ",".join("?" * len(dates))
    tm = ",".join("?" * len(TOP40_TRADER_IDS))
    df = pd.read_sql(
        f"SELECT stock_id, trader_id, buy_amt, sell_amt FROM daily_dama "
        f"WHERE date IN ({qm}) AND trader_id IN ({tm})",
        conn,
        params=list(dates) + list(TOP40_TRADER_IDS),
    )
    if df.empty:
        return None
    df["net"] = df["buy_amt"] - df["sell_amt"]
    df["group"] = df["trader_id"].map(lambda t: TRACKED_BROKERS[t]["group"])
    per_group = df.groupby(["stock_id", "group"])["net"].sum().reset_index()
    return per_group.groupby("stock_id").agg(
        net=("net", "sum"),
        n_buy=("net", lambda s: int((s > 0).sum())),
        n_sell=("net", lambda s: int((s < 0).sum())),
    ).reset_index()


def _lines(ctx, df, side):
    if df is None or df.empty:
        return [], []
    if side == "buy":
        d = df[df["net"] > 0].sort_values("net", ascending=False).head(TOP_N)
        fmt = lambda r: f"{fmt_wan(r.net, signed=True)} ({r.n_buy}組買超)"  # noqa: E731
    else:
        d = df[df["net"] < 0].sort_values("net").head(TOP_N)
        fmt = lambda r: f"{fmt_wan(r.net, signed=True)} ({r.n_sell}組賣超)"  # noqa: E731
    lines = [f"{i}. {ctx.label(r.stock_id)} {fmt(r)}" for i, r in enumerate(d.itertuples(), 1)]
    return lines, d["stock_id"].tolist()


def build(ctx):
    if not TOP40_TRADER_IDS:
        msg = "找不到 data/tracked_brokers.json,沒有追蹤名單(請在 fenpoint 專案跑 scripts/08_export_tracked_brokers.py)"
        return {k: nodata(k, t, msg) for k, t in [("s2_buy", T_BUY), ("s2_sell", T_SELL), ("s2_buy3", T_BUY3)]}

    d1 = recent_dates(ctx.conn, "daily_dama", ctx.day, 1)
    if not d1 or d1[-1] != ctx.day:
        msg = "今日無資料(今天的分點日報沒有抓到)"
        return {k: nodata(k, t, msg) for k, t in [("s2_buy", T_BUY), ("s2_sell", T_SELL), ("s2_buy3", T_BUY3)]}
    d3 = recent_dates(ctx.conn, "daily_dama", ctx.day, 3)

    today = _by_stock(ctx.conn, d1)
    three = _by_stock(ctx.conn, d3)

    buy_lines, buy_picks = _lines(ctx, today, "buy")
    sell_lines, sell_picks = _lines(ctx, today, "sell")
    buy3_lines, buy3_picks = _lines(ctx, three, "buy")

    note3 = f"(資料庫只有 {len(d3)} 個交易日的分點資料,近3日版不足3天)" if len(d3) < 3 else None
    if note3:
        buy3_lines.append(note3)
    return {
        "s2_buy": result("s2_buy", T_BUY, buy_lines or ["今天沒有合計買超的股票"], buy_picks),
        "s2_sell": result("s2_sell", T_SELL, sell_lines or ["今天沒有合計賣超的股票"], sell_picks),
        "s2_buy3": result("s2_buy3", T_BUY3, buy3_lines or ["沒有合計買超的股票"], buy3_picks),
    }
