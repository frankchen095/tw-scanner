"""
feature1_dama_institutional.py —— 功能1:大摩分點 + 投信/外資買賣超排行報表

注意:
  - FinMind 沒有「次產業/題材」這種人工分類資料,報表不會有這欄。
  - 已排除 ETF、ETN、以及查不到名稱的權證等非個股商品。
  - 「離高/低點%」用還原股價(排除除權息/減資的價格跳空)另外查詢,只針對排進榜單的股票查,
    不是每天全市場都查,免得花太多 API 次數。
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from helpers import (  # noqa: E402
    recent_dates,
    get_stock_info,
    get_shares,
    get_valid_stock_ids,
    get_adj_close_range,
    format_table,
)

TOP_N_DAMA = 15
TOP_N_INST = 20


def _dama_table(conn, report_date, n_days, valid_ids):
    dates = recent_dates(conn, "daily_dama", report_date, n_days)
    if not dates:
        return None
    qmarks = ",".join("?" * len(dates))
    df = pd.read_sql(f"SELECT * FROM daily_dama WHERE date IN ({qmarks})", conn, params=dates)
    df = df[df["stock_id"].isin(valid_ids)]
    if df.empty:
        return None

    df["net_shares"] = df["buy"] - df["sell"]
    df["net_amount"] = df["net_shares"] * df["price"]
    agg = (
        df.groupby("stock_id")
        .agg(net_shares=("net_shares", "sum"), net_amount=("net_amount", "sum"))
        .reset_index()
    )

    stock_ids = agg["stock_id"].tolist()
    info = get_stock_info(conn, stock_ids)
    shares = get_shares(conn, stock_ids)
    agg["name"] = agg["stock_id"].map(lambda s: info.get(s, ("", ""))[0])
    agg["shares_pct"] = agg.apply(
        lambda r: (r.net_shares / shares[r.stock_id] * 100) if shares.get(r.stock_id) else None,
        axis=1,
    )
    return agg


def _format_dama(agg, title, ascending):
    if agg is None or agg.empty:
        return f"{title}\n(無資料)"
    d = agg.sort_values("net_amount", ascending=ascending).head(TOP_N_DAMA)
    rows = [
        [
            i + 1,
            r.stock_id,
            r.name,
            f"{r.net_amount/10000:,.0f}",
            f"{r.shares_pct:.2f}%" if pd.notna(r.shares_pct) else "-",
        ]
        for i, r in enumerate(d.itertuples())
    ]
    table = format_table(
        ["#", "代號", "名稱", "淨額(萬)", "股本比"],
        rows,
        aligns=["right", "left", "left", "right", "right"],
    )
    return f"{title}\n{table}"


def _inst_table(conn, report_date, n_days, name_filter, valid_ids):
    dates = recent_dates(conn, "daily_institutional", report_date, n_days)
    if not dates:
        return None
    qmarks = ",".join("?" * len(dates))
    inst = pd.read_sql(
        f"SELECT * FROM daily_institutional WHERE date IN ({qmarks}) AND name = ?",
        conn,
        params=dates + [name_filter],
    )
    inst = inst[inst["stock_id"].isin(valid_ids)]
    if inst.empty:
        return None

    price = pd.read_sql(
        f"SELECT date, stock_id, close FROM daily_price WHERE date IN ({qmarks})",
        conn,
        params=dates,
    )
    df = inst.merge(price, on=["date", "stock_id"], how="left")
    df["net_shares"] = df["buy"] - df["sell"]
    df["net_amount"] = df["net_shares"] * df["close"]

    agg = (
        df.groupby("stock_id")
        .agg(net_shares=("net_shares", "sum"), net_amount=("net_amount", "sum"))
        .reset_index()
    )

    stock_ids = agg["stock_id"].tolist()
    info = get_stock_info(conn, stock_ids)
    shares = get_shares(conn, stock_ids)
    agg["name"] = agg["stock_id"].map(lambda s: info.get(s, ("", ""))[0])
    agg["shares_pct"] = agg.apply(
        lambda r: (r.net_shares / shares[r.stock_id] * 100) if shares.get(r.stock_id) else None,
        axis=1,
    )
    return agg


def _combine(conn, a, b):
    cols = ["stock_id", "name", "net_shares", "net_amount"]
    a = a[cols] if a is not None else pd.DataFrame(columns=cols)
    b = b[cols] if b is not None else pd.DataFrame(columns=cols)
    if a.empty and b.empty:
        return None
    merged = pd.concat([a, b])
    agg = (
        merged.groupby("stock_id")
        .agg(net_shares=("net_shares", "sum"), net_amount=("net_amount", "sum"))
        .reset_index()
    )
    name_map = pd.concat([a[["stock_id", "name"]], b[["stock_id", "name"]]]).drop_duplicates(
        "stock_id"
    ).set_index("stock_id")["name"]
    agg["name"] = agg["stock_id"].map(name_map)

    stock_ids = agg["stock_id"].tolist()
    shares = get_shares(conn, stock_ids)
    agg["shares_pct"] = agg.apply(
        lambda r: (r.net_shares / shares[r.stock_id] * 100) if shares.get(r.stock_id) else None,
        axis=1,
    )
    return agg


def _format_inst(conn, report_date, agg, title, ascending, show_100d=True):
    if agg is None or agg.empty:
        return f"{title}\n(無資料)"
    d = agg.sort_values("net_amount", ascending=ascending).head(TOP_N_INST)
    headers = ["#", "代號", "名稱", "淨額(百萬)", "占股本比"]
    aligns = ["right", "left", "left", "right", "right"]
    if show_100d:
        headers.append("離高/低點%")
        aligns.append("right")
        ranges = get_adj_close_range(conn, d["stock_id"].tolist(), report_date)
    else:
        ranges = {}

    rows = []
    for i, r in enumerate(d.itertuples()):
        row = [
            i + 1,
            r.stock_id,
            r.name,
            f"{r.net_amount/1e6:,.0f}",
            f"{r.shares_pct:.2f}%" if pd.notna(r.shares_pct) else "-",
        ]
        if show_100d:
            hi, lo, last = ranges.get(r.stock_id, (None, None, None))
            dist = None
            if last is not None:
                ref = hi if not ascending else lo
                if ref:
                    dist = (last - ref) / ref * 100
            row.append(f"{dist:.1f}%" if dist is not None else "-")
        rows.append(row)

    table = format_table(headers, rows, aligns=aligns)
    return f"{title}\n{table}"


def build(conn, report_date):
    sections = []
    valid_ids = get_valid_stock_ids(conn)

    print("  [功能1] 大摩單日…")
    dama1 = _dama_table(conn, report_date, 1, valid_ids)
    sections.append(_format_dama(dama1, "【大摩 單日買超 Top15】", ascending=False))
    sections.append(_format_dama(dama1, "【大摩 單日賣超 Top15】", ascending=True))

    print("  [功能1] 大摩近5日…")
    dama5 = _dama_table(conn, report_date, 5, valid_ids)
    sections.append(_format_dama(dama5, "【大摩 近5日買超 Top15】", ascending=False))
    sections.append(_format_dama(dama5, "【大摩 近5日賣超 Top15】", ascending=True))

    for label, key in [("投信", "Investment_Trust"), ("外資", "Foreign_Investor")]:
        print(f"  [功能1] {label} 單日…")
        d1 = _inst_table(conn, report_date, 1, key, valid_ids)
        sections.append(_format_inst(conn, report_date, d1, f"【{label} 單日買超 Top20】", ascending=False))
        sections.append(_format_inst(conn, report_date, d1, f"【{label} 單日賣超 Top20】", ascending=True))
        print(f"  [功能1] {label} 近5日…")
        d5 = _inst_table(conn, report_date, 5, key, valid_ids)
        sections.append(_format_inst(conn, report_date, d5, f"【{label} 近5日買超 Top20】", ascending=False))
        sections.append(_format_inst(conn, report_date, d5, f"【{label} 近5日賣超 Top20】", ascending=True))

    for n_days, tag in [(1, "單日"), (5, "近5日")]:
        print(f"  [功能1] 投信+外資合計 {tag}…")
        dt_ = _inst_table(conn, report_date, n_days, "Investment_Trust", valid_ids)
        fr_ = _inst_table(conn, report_date, n_days, "Foreign_Investor", valid_ids)
        combo = _combine(conn, dt_, fr_)
        sections.append(
            _format_inst(conn, report_date, combo, f"【投信+外資合計 {tag}買超 Top20】", ascending=False, show_100d=False)
        )
        sections.append(
            _format_inst(conn, report_date, combo, f"【投信+外資合計 {tag}賣超 Top20】", ascending=True, show_100d=False)
        )

    return sections
