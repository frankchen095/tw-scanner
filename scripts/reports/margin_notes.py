"""
融資餘額 vs 股價背離(附註型 + 一個獨立小榜)

資料:FinMind TaiwanStockMarginPurchaseShortSale(全市場一次查),存在 margin_daily。融資餘額單位=張。

1. 籌碼乾淨標記〔融資未增〕:出現在策略一或策略五(價格位置)的股票,近 5 個交易日融資餘額變化 ≤ 0% → 名稱後加標記
   (偏多的補強)。同一標記也會加在交集榜裡的同一檔。
2. 散戶接刀榜(獨立小榜,放在策略三之後,key=e_knife):市值>500億,近 5 日融資餘額增加 ≥ 10%,而且同期股價漲幅 < 2%
   (含下跌;股價用還原收盤價)。依融資增幅由大到小。偏空訊號:進交集榜『訊號衝突』來源,成效追蹤反向計算。
3. 策略三(漲停放空)每一檔補『融資使用率』= 融資餘額 ÷ 融資限額(看還有多少散戶追價空間)。
資料不足(融資表不到 6 個交易日)就顯示今日無資料,不估算。
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from context import NO_MV, nodata, result  # noqa: E402
from helpers import get_adj_history  # noqa: E402

TITLE = "【散戶接刀榜】市值>500億,近5日融資增≥10%但股價漲幅<2%(偏空)"
TAG_CLEAN = "〔融資未增〕"
HORIZON = 5  # 近 5 個交易日
KNIFE_MARGIN_UP = 0.10
KNIFE_PRICE_MAX = 0.02


def _load(ctx):
    """回傳 DataFrame: stock_id, bal(今日融資餘額), bal5(5個交易日前), limit(融資限額)。資料不足回傳 None"""
    dates = [r[0] for r in ctx.conn.execute(
        "SELECT DISTINCT date FROM margin_daily WHERE date <= ? ORDER BY date DESC LIMIT ?", (ctx.day, HORIZON + 1))]
    if len(dates) < HORIZON + 1 or dates[0] != ctx.day:
        return None
    today = pd.read_sql("SELECT stock_id, margin_bal AS bal, margin_limit AS lim FROM margin_daily WHERE date=?",
                        ctx.conn, params=[dates[0]])
    old = pd.read_sql("SELECT stock_id, margin_bal AS bal5 FROM margin_daily WHERE date=?",
                      ctx.conn, params=[dates[HORIZON]])
    df = today.merge(old, on="stock_id")
    df = df[df["bal5"] > 0].copy()
    df["pct"] = df["bal"] / df["bal5"] - 1
    return df


def apply_clean_tags(ctx, res):
    """幫 res 裡的策略一/策略五(以及交集榜)的股票加〔融資未增〕。回傳加了幾檔。"""
    df = _load(ctx)
    if df is None:
        return 0
    clean = set(df[df["pct"] <= 0]["stock_id"])
    n = 0
    for key in ("s1", "s5", "intersection"):
        r = res.get(key)
        if not r:
            continue
        for sid in r["picks"]:
            if sid not in clean:
                continue
            lab = ctx.label(sid)
            for i, line in enumerate(r["lines"]):
                if lab in line and TAG_CLEAN not in line:
                    r["lines"][i] = line.replace(lab, lab + TAG_CLEAN, 1)
                    n += 1 if key != "intersection" else 0
                    break
        for row in r.get("meta", {}).get("rows", []):
            if row.get("sid") in clean:
                row["margin_clean"] = True
    return n


def apply_s3_usage(ctx, res):
    """策略三每一檔補『融資使用率』"""
    r = res.get("s3")
    if not r or not r["picks"]:
        return
    df = pd.read_sql("SELECT stock_id, margin_bal, margin_limit FROM margin_daily WHERE date=?",
                     ctx.conn, params=[ctx.day]).set_index("stock_id")
    rows = r.get("meta", {}).get("rows", [])
    by_sid = {x["sid"]: x for x in rows}
    for i, sid in enumerate(r["picks"]):
        if sid in df.index and df.loc[sid, "margin_limit"] > 0:
            use = float(df.loc[sid, "margin_bal"] / df.loc[sid, "margin_limit"])
            txt = f"融資使用率 {use * 100:.0f}%(餘額{df.loc[sid, 'margin_bal']:,.0f}張/限額{df.loc[sid, 'margin_limit']:,.0f}張)"
        else:
            use, txt = None, "融資使用率 今日無資料"
        if sid in by_sid:
            by_sid[sid]["margin_use"] = use
        if i < len(r["lines"]):
            r["lines"][i] += f" | {txt}"


def build(ctx):
    if ctx.big is None:
        return {"e_knife": nodata("e_knife", TITLE, NO_MV)}
    df = _load(ctx)
    if df is None:
        return {"e_knife": nodata("e_knife", TITLE, "今日無資料(融資融券資料不足 6 個交易日,或今天的沒有抓到)")}
    cand = df[df["stock_id"].isin(ctx.big) & (df["pct"] >= KNIFE_MARGIN_UP)].copy()
    hist = get_adj_history(cand["stock_id"].tolist(), ctx.day, n_rows=HORIZON + 1)
    hits = []
    for r in cand.itertuples():
        h = hist.get(r.stock_id)
        if h is None or len(h) < HORIZON + 1 or h["date"].iloc[-1] != ctx.day:
            continue
        px = float(h["close"].iloc[-1]) / float(h["close"].iloc[0]) - 1
        if px < KNIFE_PRICE_MAX:
            hits.append((r.stock_id, r.pct, r.bal, px))
    hits.sort(key=lambda x: -x[1])
    lines = [f"{i}. {ctx.label(sid)} 融資5日 {pct * 100:+.1f}%(餘額 {bal:,.0f} 張)、股價5日 {px * 100:+.1f}%"
             for i, (sid, pct, bal, px) in enumerate(hits, 1)]
    rows = [{"sid": sid, "margin_pct": float(pct), "bal": float(bal), "px": float(px)} for sid, pct, bal, px in hits]
    return {"e_knife": result("e_knife", TITLE, lines or ["今天沒有符合條件的股票"], [h[0] for h in hits], rows=rows)}
