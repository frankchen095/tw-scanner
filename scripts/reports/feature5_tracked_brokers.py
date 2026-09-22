"""
feature5_tracked_brokers.py —— 功能5:追蹤名單分點今日買超

名單來自 fenpoint 專案(分點勝率工作台)的回測結果:2023–2026 年資料裡,
「單日淨買超 ≥5,000萬」之後 20 個交易日內收盤漲超過 20% 的命中率 ≥30%、
且全期間至少 30 次事件的營業處(見 data/tracked_brokers.json)。
命中率是歷史統計,不是保證,只是「這個分點過去比較準」的參考。

這裡只列「今天」單日淨買超 ≥ EVENT_MIN_NET_AMT 的,跟回測時的事件定義一致。
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from db import TRACKED_BROKERS  # noqa: E402
from helpers import get_stock_info, get_adj_close_range, format_table  # noqa: E402

EVENT_MIN_NET_AMT = 50_000_000
TOP_N = 25


def build(conn, report_date):
    if not TRACKED_BROKERS:
        return ["找不到 data/tracked_brokers.json,沒有追蹤名單。"
                "請先在 fenpoint 專案跑 scripts/08_export_tracked_brokers.py 產生。"]

    df = pd.read_sql(
        "SELECT * FROM daily_dama WHERE date = ? AND trader_id IN ({})".format(
            ",".join("?" * len(TRACKED_BROKERS))
        ),
        conn, params=[report_date] + list(TRACKED_BROKERS),
    )
    if df.empty:
        return [f"（{report_date} 沒有查到追蹤名單的分點資料,或今天不是交易日）"]

    df["net_amount"] = (df["buy"] - df["sell"]) * df["price"]
    df["group"] = df["trader_id"].map(lambda t: TRACKED_BROKERS[t]["group"])
    df["hit_rate"] = df["trader_id"].map(lambda t: TRACKED_BROKERS[t]["hit_rate"])
    agg = (
        df.groupby(["stock_id", "group"])
        .agg(net_amount=("net_amount", "sum"), hit_rate=("hit_rate", "first"))
        .reset_index()
    )
    hits = agg[agg["net_amount"] >= EVENT_MIN_NET_AMT].sort_values("net_amount", ascending=False)
    if hits.empty:
        return [f"（{report_date} 追蹤名單裡沒有分點單日買超達 5,000 萬的,今天沒有信號)"]

    info = get_stock_info(conn, hits["stock_id"].tolist())
    ranges = get_adj_close_range(conn, hits["stock_id"].tolist(), report_date, lookback_days=120)

    rows = []
    for i, r in enumerate(hits.head(TOP_N).itertuples()):
        name = info.get(r.stock_id, ("", ""))[0]
        hi, lo, last = ranges.get(r.stock_id, (None, None, None))
        pct120 = f"{(last - lo) / (hi - lo) * 100:.0f}%" if hi and lo and hi > lo and last is not None else "-"
        rows.append([
            i + 1, r.stock_id, name, r.group,
            f"{r.net_amount/1e4:,.0f}", f"{r.hit_rate*100:.0f}%", pct120,
        ])

    table = format_table(
        ["#", "代號", "名稱", "分點", "淨額(萬)", "歷史命中率", "120日位階"],
        rows,
        aligns=["right", "left", "left", "left", "right", "right", "right"],
    )
    n_groups = len(set(v["group"] for v in TRACKED_BROKERS.values()))
    note = (
        f"追蹤 {n_groups} 個分點(全期間命中率≥30%、樣本≥30次)。"
        "命中率是 2023–2026 年歷史統計,不是保證,僅供參考,不構成投資建議。"
        "120日位階:目前收盤價落在近120個交易日高低區間的位置,越接近100%代表越靠近近期高點。"
    )
    return [f"【追蹤分點 今日單日買超 ≥5,000萬】\n{table}\n\n{note}"]
