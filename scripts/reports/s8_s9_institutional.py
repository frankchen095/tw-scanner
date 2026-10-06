"""
策略八:法人買超排行 + 策略九:法人連續買超

外資 = TaiwanStockInstitutionalInvestorsBuySell 的 Foreign_Investor(外資及陸資,不含外資自營商),
投信 = Investment_Trust。金額 = 買賣超股數 × 當天收盤價(= 張數×1000×價格),
5 日合計是『每一天各用當天收盤價算金額』再加總。都只看市值>500億。資料用每天本來就在抓的
daily_institutional,不多花 API 額度。

策略八:外資+投信合計買超 單日前十 / 近5個交易日合計前十,每檔分開列外資、投信金額
策略九:投信、外資各一張榜,各取前十:連續3個交易日以上買超(每天買賣超股數>0),且
        連買期間合計買超金額>1億;列出連買天數、合計金額、占期間成交金額比例;
        依連買天數多到少,天數相同看金額。資料庫最多保留 30 個交易日,連買天數超過就標「N天以上」。
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from context import NO_MV, nodata, result  # noqa: E402
from helpers import fmt_yi, recent_dates  # noqa: E402

TOP_N = 10
FOREIGN, TRUST = "Foreign_Investor", "Investment_Trust"
T8_1 = "【策略八:法人買超排行】外資+投信合計 單日買超 Top10(市值>500億)"
T8_5 = "【策略八:法人買超排行】外資+投信合計 近5日買超 Top10(市值>500億)"
T9_TRUST = "【策略九:法人連續買超】投信 連買3天以上且合計>1億(市值>500億)"
T9_FOREIGN = "【策略九:法人連續買超】外資 連買3天以上且合計>1億(市值>500億)"


def _load(conn, dates, big):
    qm = ",".join("?" * len(dates))
    inst = pd.read_sql(
        f"SELECT date, stock_id, name, buy, sell FROM daily_institutional "
        f"WHERE date IN ({qm}) AND name IN (?, ?)",
        conn,
        params=list(dates) + [FOREIGN, TRUST],
    )
    px = pd.read_sql(
        f"SELECT date, stock_id, close, money FROM daily_price WHERE date IN ({qm})", conn, params=list(dates)
    )
    df = inst.merge(px, on=["date", "stock_id"], how="inner")
    df = df[df["stock_id"].isin(big)].copy()
    df["net_shares"] = df["buy"] - df["sell"]
    df["amt"] = df["net_shares"] * df["close"]
    return df


def _rank8(ctx, df, title):
    p = df.pivot_table(index="stock_id", columns="name", values="amt", aggfunc="sum", fill_value=0.0)
    for c in (FOREIGN, TRUST):
        if c not in p.columns:
            p[c] = 0.0
    p["total"] = p[FOREIGN] + p[TRUST]
    top = p[p["total"] > 0].sort_values("total", ascending=False).head(TOP_N)
    lines = [
        f"{i}. {ctx.label(sid)} 合計{fmt_yi(r['total'], True)} (外資{fmt_yi(r[FOREIGN], True)} / 投信{fmt_yi(r[TRUST], True)})"
        for i, (sid, r) in enumerate(top.iterrows(), 1)
    ]
    return lines or ["今天沒有合計買超的股票"], top.index.tolist()


def _streaks(ctx, df, investor):
    """回傳 [(stock_id, 連買天數, 合計金額, 占成交金額比例, 是否碰到資料上限)]"""
    d = df[df["name"] == investor].sort_values("date")
    n_dates = df["date"].nunique()
    out = []
    for sid, g in d.groupby("stock_id"):
        g = g.sort_values("date", ascending=False)
        streak = 0
        for r in g.itertuples():
            if r.net_shares > 0:
                streak += 1
            else:
                break
        # 最新一天必須是報表日,否則不算『連買到今天』
        if streak < 3 or g.iloc[0]["date"] != ctx.day:
            continue
        sel = g.head(streak)
        amt = float(sel["amt"].sum())
        if amt <= 1e8:
            continue
        money = float(sel["money"].sum())
        out.append((sid, streak, amt, amt / money if money else None, streak >= n_dates))
    out.sort(key=lambda x: (-x[1], -x[2]))
    return out[:TOP_N]


def _lines9(ctx, rows):
    lines = []
    for i, (sid, streak, amt, ratio, capped) in enumerate(rows, 1):
        days = f"連買{streak}天以上" if capped else f"連買{streak}天"
        ratio_txt = f"占期間成交金額{ratio * 100:.1f}%" if ratio is not None else "占比無資料"
        lines.append(f"{i}. {ctx.label(sid)} {days} 合計{fmt_yi(amt, True)} {ratio_txt}")
    return lines or ["今天沒有符合條件的股票"]


def build(ctx):
    keys = [("s8_1", T8_1), ("s8_5", T8_5), ("s9_trust", T9_TRUST), ("s9_foreign", T9_FOREIGN)]
    if ctx.big is None:
        return {k: nodata(k, t, NO_MV) for k, t in keys}

    d30 = recent_dates(ctx.conn, "daily_institutional", ctx.day, 30)
    if not d30 or d30[-1] != ctx.day:
        return {k: nodata(k, t, "今日無資料(今天的法人買賣超沒有抓到)") for k, t in keys}

    df = _load(ctx.conn, d30, ctx.big)
    d1, d5 = d30[-1:], d30[-5:]

    lines1, picks1 = _rank8(ctx, df[df["date"].isin(d1)], T8_1)
    lines5, picks5 = _rank8(ctx, df[df["date"].isin(d5)], T8_5)
    if len(d5) < 5:
        lines5.append(f"(資料庫只有 {len(d5)} 個交易日的法人資料,近5日版不足5天)")

    trust = _streaks(ctx, df, TRUST)
    foreign = _streaks(ctx, df, FOREIGN)
    return {
        "s8_1": result("s8_1", T8_1, lines1, picks1),
        "s8_5": result("s8_5", T8_5, lines5, picks5),
        "s9_trust": result("s9_trust", T9_TRUST, _lines9(ctx, trust), [x[0] for x in trust]),
        "s9_foreign": result("s9_foreign", T9_FOREIGN, _lines9(ctx, foreign), [x[0] for x in foreign]),
    }
