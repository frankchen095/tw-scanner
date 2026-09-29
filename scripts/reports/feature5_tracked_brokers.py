"""
feature5_tracked_brokers.py —— 功能5:追蹤名單前40大分點,合計買超個股排行

名單來自 fenpoint 專案(分點勝率工作台)的回測結果:2023–2026 年資料裡,
「單日淨買超 ≥5,000萬」之後 20 個交易日內收盤漲超過 20% 的命中率最高的
前 40 個營業處(見 data/tracked_brokers.json,已照命中率排序)。
命中率是歷史統計,不是保證,只是「這個分點過去比較準」的參考。

這裡不是列個別分點的單筆買超,而是把這 40 個分點「當天」和「近3天」買超同一檔
股票的金額全部加總,看哪些股票是這群分點一起在買的:
    單日版:今天的合計淨買超,前 10 名
    近3日版:最近3個交易日的合計淨買超,前 10 名
只列「合計淨買超 > 0」(整體是買超)的股票,賣超的不列。
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from db import TRACKED_BROKERS, TOP40_TRADER_IDS, TOP_N_TRACKED_BROKERS  # noqa: E402
from helpers import recent_dates, get_stock_info, format_table  # noqa: E402

TOP_N_STOCKS = 10


def _agg(conn, dates):
    if not dates:
        return None
    qmarks = ",".join("?" * len(dates))
    tmarks = ",".join("?" * len(TOP40_TRADER_IDS))
    df = pd.read_sql(
        f"SELECT stock_id, trader_id, price, buy, sell FROM daily_dama "
        f"WHERE date IN ({qmarks}) AND trader_id IN ({tmarks})",
        conn, params=list(dates) + list(TOP40_TRADER_IDS),
    )
    if df.empty:
        return None
    df["net_amount"] = (df["buy"] - df["sell"]) * df["price"]
    df["group"] = df["trader_id"].map(lambda t: TRACKED_BROKERS[t]["group"])
    agg = (
        df.groupby("stock_id")
        .agg(net_amount=("net_amount", "sum"), n_brokers=("group", "nunique"))
        .reset_index()
    )
    return agg[agg["net_amount"] > 0].sort_values("net_amount", ascending=False)


def _format(conn, agg, title):
    if agg is None or agg.empty:
        return f"{title}\n(沒有合計買超>0的股票)"
    d = agg.head(TOP_N_STOCKS)
    info = get_stock_info(conn, d["stock_id"].tolist())
    rows = [
        [i + 1, r.stock_id, info.get(r.stock_id, ("", ""))[0],
         f"{r.net_amount/1e4:,.0f}", int(r.n_brokers)]
        for i, r in enumerate(d.itertuples())
    ]
    table = format_table(
        ["#", "代號", "名稱", "合計淨額(萬)", "參與分點數"],
        rows, aligns=["right", "left", "left", "right", "right"],
    )
    return f"{title}\n{table}"


def build(conn, report_date):
    if not TOP40_TRADER_IDS:
        return ["找不到 data/tracked_brokers.json,沒有追蹤名單。"
                "請先在 fenpoint 專案跑 scripts/08_export_tracked_brokers.py 產生。"]

    day1 = recent_dates(conn, "daily_dama", report_date, 1)
    day3 = recent_dates(conn, "daily_dama", report_date, 3)

    sections = [
        _format(conn, _agg(conn, day1), f"【前{TOP_N_TRACKED_BROKERS}大分點 今日合計買超 Top{TOP_N_STOCKS}】"),
        _format(conn, _agg(conn, day3), f"【前{TOP_N_TRACKED_BROKERS}大分點 近3日合計買超 Top{TOP_N_STOCKS}】"),
    ]
    sections.append(
        f"(名單依 2023–2026 年回測命中率排序,取前{TOP_N_TRACKED_BROKERS}名;"
        "命中率是歷史統計,不是保證,僅供參考,不構成投資建議)"
    )
    return sections
