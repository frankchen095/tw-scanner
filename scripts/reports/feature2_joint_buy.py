"""
feature2_joint_buy.py —— 功能2:外資+投信同買(皆>5000萬)且成交金額前100名

當日版:外資、投信當天買超金額都要 > 5000萬,且當天成交金額排全市場前100名
連續3天版:外資、投信「每一天」買超金額都要 > 5000萬(3天都要達標),且最新一天成交金額前100名
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from helpers import recent_dates, get_stock_info, get_valid_stock_ids, format_table  # noqa: E402

THRESHOLD = 50_000_000  # 5000萬
TOP_N_MONEY = 100


def _net_amount_by_day(conn, day, name_filter):
    inst = pd.read_sql(
        "SELECT stock_id, buy, sell FROM daily_institutional WHERE date=? AND name=?",
        conn,
        params=[day, name_filter],
    )
    price = pd.read_sql(
        "SELECT stock_id, close FROM daily_price WHERE date=?", conn, params=[day]
    )
    df = inst.merge(price, on="stock_id", how="left")
    df["net_amount"] = (df["buy"] - df["sell"]) * df["close"]
    return df


def _top100_money(conn, report_date):
    price = pd.read_sql(
        "SELECT stock_id, money FROM daily_price WHERE date=?", conn, params=[report_date]
    )
    if price.empty:
        return set()
    return set(price.sort_values("money", ascending=False).head(TOP_N_MONEY)["stock_id"])


def _format(conn, stock_ids, title, detail=None):
    if not stock_ids:
        return f"{title}\n(今天沒有符合條件的股票)"
    info = get_stock_info(conn, list(stock_ids))
    rows = []
    for sid in sorted(stock_ids):
        name = info.get(sid, ("", ""))[0]
        rows.append([sid, name, detail.get(sid, "") if detail else ""])
    table = format_table(["代號", "名稱", "買超金額"], rows, aligns=["left", "left", "left"])
    return f"{title}\n{table}"


def build(conn, report_date):
    sections = []
    valid_ids = get_valid_stock_ids(conn)

    top100 = _top100_money(conn, report_date) & valid_ids
    foreign = _net_amount_by_day(conn, report_date, "Foreign_Investor")
    trust = _net_amount_by_day(conn, report_date, "Investment_Trust")

    f_hit = set(foreign.loc[foreign["net_amount"] > THRESHOLD, "stock_id"])
    t_hit = set(trust.loc[trust["net_amount"] > THRESHOLD, "stock_id"])
    day_hit = f_hit & t_hit & top100

    fm = foreign.set_index("stock_id")["net_amount"]
    tm = trust.set_index("stock_id")["net_amount"]
    detail = {
        sid: f"外資{fm[sid]/1e6:,.0f}百萬 / 投信{tm[sid]/1e6:,.0f}百萬" for sid in day_hit
    }
    sections.append(
        _format(conn, day_hit, "【外資+投信 同買(當日,各>5000萬)且成交金額前100】", detail)
    )

    dates3 = recent_dates(conn, "daily_institutional", report_date, 3)
    if len(dates3) < 3:
        sections.append(
            f"【外資+投信 同買(連續3天,各>5000萬)且成交金額前100】\n"
            f"(資料庫裡只有 {len(dates3)} 個交易日的法人資料,還不夠3天,再等幾天排程跑完就有了)"
        )
    else:
        hit3 = None
        for d in dates3:
            fday = _net_amount_by_day(conn, d, "Foreign_Investor")
            tday = _net_amount_by_day(conn, d, "Investment_Trust")
            fset = set(fday.loc[fday["net_amount"] > THRESHOLD, "stock_id"])
            tset = set(tday.loc[tday["net_amount"] > THRESHOLD, "stock_id"])
            both = fset & tset
            hit3 = both if hit3 is None else (hit3 & both)
        hit3 = (hit3 or set()) & top100
        sections.append(
            _format(conn, hit3, "【外資+投信 同買(連續3天,各>5000萬)且成交金額前100】")
        )

    return sections
