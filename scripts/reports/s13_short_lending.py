"""
策略十三(D):借券賣出餘額異動(空方籌碼)

資料:FinMind TaiwanDailyShortSaleBalances(全市場一次查,借券賣出餘額 SBLShortSalesCurrentDayBalance,單位=股),
存在 short_balance_daily。市值>500億。流通股數 = 市值 ÷ 收盤價(FinMind 市值表本來就等於 收盤價×已發行股數,
所以跟市值用同一張表)。

借券大增榜(偏空;進交集榜『訊號衝突』來源;成效追蹤反向計算),任一成立就列:
  (a) 單日借券賣出餘額增加 ≥ 流通股數 0.5%
  (b) 連續 3 個交易日借券賣出餘額增加,且 3 日合計增加 ≥ 流通股數 0.3%
  依單日增加量由大到小。
借券大減榜前五(只顯示,不當偏多類別:回補不等於看多):單日減少 ≥ 流通股數 0.3%。
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from context import NO_MV, nodata, result  # noqa: E402

T_UP = "【策略十三:借券賣出餘額大增】市值>500億(偏空)"
T_DOWN = "【策略十三:借券賣出餘額大減 Top5】回補=空方退場(只顯示,不當偏多類別)"
DAY_UP = 0.005
THREE_UP = 0.003
DAY_DOWN = 0.003


def _shares(ctx):
    """流通股數 = 市值 ÷ 收盤價(用市值表那一天的收盤價)"""
    px = pd.read_sql("SELECT stock_id, close FROM daily_price WHERE date=?", ctx.conn,
                     params=[ctx.mv_date]).set_index("stock_id")["close"]
    return {sid: ctx.mv[sid] / float(px[sid]) for sid in ctx.big if sid in px.index and px[sid] > 0}


def _fmt_wanzhang(shares, signed=False):
    v = shares / 1000 / 10000  # 股 → 張 → 萬張
    return f"{v:+,.2f}萬張" if signed else f"{v:,.2f}萬張"


def build(ctx):
    keys = [("s13", T_UP), ("s13_cover", T_DOWN)]
    if ctx.big is None:
        return {k: nodata(k, t, NO_MV) for k, t in keys}
    dates = [r[0] for r in ctx.conn.execute(
        "SELECT DISTINCT date FROM short_balance_daily WHERE date <= ? ORDER BY date DESC LIMIT 4", (ctx.day,))]
    if len(dates) < 4 or dates[0] != ctx.day:
        return {k: nodata(k, t, "今日無資料(借券賣出餘額不足 4 個交易日,或今天的沒有抓到)") for k, t in keys}
    shares = _shares(ctx)
    qm = ",".join("?" * len(dates))
    df = pd.read_sql(f"SELECT date, stock_id, sbl_bal FROM short_balance_daily WHERE date IN ({qm})",
                     ctx.conn, params=dates)
    piv = df.pivot_table(index="stock_id", columns="date", values="sbl_bal")
    d0, d1, d2, d3 = dates  # 新 → 舊

    up, down = [], []
    for sid, sh in shares.items():
        if sid not in piv.index or piv.loc[sid].isna().any():
            continue
        b0, b1, b2, b3 = (float(piv.loc[sid, d]) for d in dates)
        day_chg, three_chg = b0 - b1, b0 - b3
        tags = []
        if day_chg >= DAY_UP * sh:
            tags.append("a")
        if b0 > b1 > b2 > b3 and three_chg >= THREE_UP * sh:
            tags.append("b")
        if tags:
            up.append((sid, b0, sh, day_chg, three_chg, "/".join(tags)))
        if -day_chg >= DAY_DOWN * sh:
            down.append((sid, b0, sh, day_chg))
    up.sort(key=lambda x: -x[3])
    down.sort(key=lambda x: x[3])
    down = down[:5]

    lines_up = [
        f"{i}. {ctx.label(sid)} 借券餘額 {_fmt_wanzhang(b0)}(占流通 {b0 / sh * 100:.2f}%)"
        f"單日 {_fmt_wanzhang(dc, True)} / 3日 {_fmt_wanzhang(tc, True)}〔{tag}〕"
        for i, (sid, b0, sh, dc, tc, tag) in enumerate(up, 1)
    ] or ["今天沒有符合條件的股票"]
    lines_down = [
        f"{i}. {ctx.label(sid)} 借券餘額 {_fmt_wanzhang(b0)}(占流通 {b0 / sh * 100:.2f}%)單日 {_fmt_wanzhang(dc, True)}"
        for i, (sid, b0, sh, dc) in enumerate(down, 1)
    ] or ["今天沒有符合條件的股票"]
    rows_up = [{"sid": sid, "bal": b0, "ratio": b0 / sh, "day": dc, "three": tc, "tag": tag}
               for sid, b0, sh, dc, tc, tag in up]
    rows_down = [{"sid": sid, "bal": b0, "ratio": b0 / sh, "day": dc} for sid, b0, sh, dc in down]
    return {
        "s13": result("s13", T_UP, lines_up, [x[0] for x in up], rows=rows_up),
        "s13_cover": result("s13_cover", T_DOWN, lines_down, [], rows=rows_down),
    }
