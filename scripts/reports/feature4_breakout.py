"""
feature4_breakout.py —— 功能4:帶量突破盤整

條件:
  近20個交易日(不含今天)最高價-最低價 / 平均價 <= 10%(盤整區間夠窄)
  今日收盤價 > 這20天的最高價(突破)
  今日成交量 >= 近5日均量 x 1.5倍(帶量)
  今日漲幅 > 3%
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from helpers import get_stock_info, get_valid_stock_ids, format_table  # noqa: E402

RANGE_DAYS = 20
RANGE_PCT = 10.0  # 百分比
VOLUME_MULT = 1.5
VOLUME_AVG_DAYS = 5
MIN_GAIN_PCT = 3.0


def build(conn, report_date):
    hist = pd.read_sql(
        "SELECT date, stock_id, close, high, low, volume FROM daily_price WHERE date <= ? ORDER BY date",
        conn,
        params=[report_date],
    )
    if hist.empty:
        return ["【帶量突破盤整】\n(無資料)"]

    valid_ids = get_valid_stock_ids(conn)
    hist = hist[hist["stock_id"].isin(valid_ids)]
    if hist.empty:
        return ["【帶量突破盤整】\n(無資料)"]

    dates = sorted(hist["date"].unique())
    need = RANGE_DAYS + 2  # 20天盤整 + 今天 + 昨天(算漲幅要用)
    if len(dates) < need:
        return [
            f"【帶量突破盤整】\n(資料庫目前只有 {len(dates)} 個交易日,還不夠 {need} 天,"
            f"再等幾天排程跑完累積歷史資料就會開始出現結果)"
        ]

    today, yesterday = dates[-1], dates[-2]
    prior20 = dates[-(RANGE_DAYS + 1) : -1]
    prior5 = dates[-(VOLUME_AVG_DAYS + 1) : -1]

    today_df = hist[hist["date"] == today].set_index("stock_id")
    yest_close = hist[hist["date"] == yesterday].set_index("stock_id")["close"].rename("prev_close")
    range_stats = (
        hist[hist["date"].isin(prior20)]
        .groupby("stock_id")
        .agg(hi20=("high", "max"), lo20=("low", "min"), avg_close20=("close", "mean"))
    )
    avg_vol5 = (
        hist[hist["date"].isin(prior5)].groupby("stock_id")["volume"].mean().rename("avg_vol5")
    )

    df = today_df.join(range_stats, how="inner").join(avg_vol5, how="inner").join(yest_close, how="inner")
    df = df.reset_index()

    df["range_pct"] = (df["hi20"] - df["lo20"]) / df["avg_close20"] * 100
    df["gain_pct"] = (df["close"] - df["prev_close"]) / df["prev_close"] * 100
    df["vol_mult"] = df["volume"] / df["avg_vol5"]

    hit = df[
        (df["range_pct"] <= RANGE_PCT)
        & (df["close"] > df["hi20"])
        & (df["vol_mult"] >= VOLUME_MULT)
        & (df["gain_pct"] > MIN_GAIN_PCT)
    ].sort_values("gain_pct", ascending=False)

    title = (
        f"【帶量突破盤整({RANGE_DAYS}日盤整≤{RANGE_PCT:.0f}%、突破{RANGE_DAYS}日高、"
        f"量≥{VOLUME_AVG_DAYS}日均量{VOLUME_MULT}倍、漲幅>{MIN_GAIN_PCT:.0f}%)】"
    )

    if hit.empty:
        return [f"{title}\n(今天沒有符合條件的股票)"]

    info = get_stock_info(conn, hit["stock_id"].tolist())
    rows = []
    for r in hit.itertuples():
        name = info.get(r.stock_id, ("", ""))[0]
        rows.append(
            [r.stock_id, name, f"{r.gain_pct:.1f}%", f"{r.vol_mult:.1f}倍", f"{r.range_pct:.1f}%"]
        )
    table = format_table(
        ["代號", "名稱", "漲幅", "量倍數", "盤整幅度"],
        rows,
        aligns=["left", "left", "right", "right", "right"],
    )
    return [f"{title}\n{table}"]
