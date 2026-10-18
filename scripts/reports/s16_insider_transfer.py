"""
策略十六(J):內部人持股轉讓事前申報(證交法 22-2 條:董監事、經理人、10%以上大股東轉讓持股要事前申報)

資料:證交所 OpenAPI t187ap12_L / 櫃買 mopsfin_t187ap12_O『每日內部人持股轉讓事前申報表-持股轉讓日報表』
(每天只提供最近一個營業日那一份,所以 fetchers.fetch_insider_transfer 每天存進 insider_transfer_daily)。
欄位:公司代號、申報人身分、姓名、預定轉讓方式與股數、目前持有股數、預定轉讓後持股、有效轉讓期間。

轉讓方式分三類:
  〔一般交易〕〔鉅額交易〕→ 真的要賣,偏空(進交集榜『訊號衝突』來源,成效追蹤反向計算)
  〔信託〕〔贈與〕→ 多半是節稅或家族安排,不當偏空,仍列出並標明方式(不追蹤、不進訊號衝突)
  其他方式照觀測站原文標
列出門檻(任一):預定轉讓股數 ÷ 申報人目前持股 ≥ 10%;或 預定轉讓股數 × 當日收盤價 ≥ 1 億元。
不設市值門檻,每檔標市值。

【解質後轉讓】同一家公司、同一個人在最新一期質押月報(策略十一)有解質的,標〔解質後轉讓〕並排最前面(最強的偏空確認)。
月報看不出哪一天解質,所以比對的是『當月』而不是『申報日往前30天』。
"""

import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from context import nodata, result  # noqa: E402
from s11_pledge import released_people  # noqa: E402

TITLE = "【策略十六:內部人申報轉讓】董監事/經理人/大股東事前申報,今天出表的申報(偏空,信託/贈與除外)"
RATIO_MIN = 0.10
AMOUNT_MIN = 1e8
SELL_METHODS = ("一般交易", "鉅額")  # 方式文字含這些 → 真的要賣


def _kind(method):
    m = re.sub(r"\s+", "", method or "")
    if any(k in m for k in SELL_METHODS):
        return "sell", ("鉅額交易" if "鉅額" in m else "一般交易")
    if "信託" in m:
        return "other", "信託"
    if "贈與" in m:
        return "other", "贈與"
    return "other", m or "未載明"


def _wan(shares):
    return f"{shares / 10000:,.0f}萬股"


def build(ctx):
    rows = ctx.conn.execute(
        "SELECT stock_id, name, identity, method, shares, held_shares, period FROM insider_transfer_daily WHERE date=?",
        (ctx.day,)).fetchall()
    if not rows:
        last = ctx.conn.execute("SELECT MAX(date) FROM insider_transfer_daily WHERE date <= ?", (ctx.day,)).fetchone()[0]
        why = "今日無資料(今天沒有申報,或官方申報表還沒更新" + (f";資料庫最近一筆出表日期 {last}" if last else "") + ")"
        return {"s16": nodata("s16", TITLE, why)}
    px = pd.read_sql("SELECT stock_id, close FROM daily_price WHERE date=?", ctx.conn, params=[ctx.day]).set_index("stock_id")["close"]
    try:
        released = released_people(ctx.conn)  # 當月(月報)有解質的人:(公司代號, 姓名)
    except Exception:  # noqa: BLE001
        released = set()
    picks, items = [], []
    for sid, name, ident, method, shares, held, period in rows:
        if shares <= 0:
            continue
        ratio = shares / held if held else None
        close = float(px[sid]) if sid in px.index else None
        amount = shares * close if close else None
        if not ((ratio is not None and ratio >= RATIO_MIN) or (amount is not None and amount >= AMOUNT_MIN)):
            continue
        side, label = _kind(method)
        items.append({"sid": sid, "name": name, "identity": ident, "side": side, "kind": label, "shares": shares,
                      "ratio": ratio, "amount": amount, "period": period, "released": (sid, name) in released})
    # 偏空(真的要賣)排前面,各自依占持股比由大到小
    # 〔解質後轉讓〕(同公司同一人當月有解質)最前面,再來是偏空(真的要賣),各自依占持股比由大到小
    items.sort(key=lambda x: (not x["released"], x["side"] != "sell", -(x["ratio"] or 0)))
    lines = []
    for i, x in enumerate(items, 1):
        mv = ctx.mv.get(x["sid"]) if ctx.mv else None
        mv_txt = f"市值{mv / 1e8:,.0f}億" if mv else "市值無資料"
        ratio = "占其持股 -" if x["ratio"] is None else f"占其持股 {x['ratio'] * 100:.1f}%"
        amt = "" if x["amount"] is None else f",約 {x['amount'] / 1e8:,.2f} 億元"
        lines.append(f"{i}. {ctx.label(x['sid'])} {mv_txt}〔{x['kind']}〕{x['identity']} {x['name']} "
                     f"預定轉讓 {_wan(x['shares'])}({ratio}{amt})" + ("〔解質後轉讓〕" if x["released"] else ""))
    if not lines:
        lines = [f"今天有 {len(rows)} 筆申報,但都沒達到門檻(占持股≥10% 或 約1億元)"]
    else:
        lines.append("(事前申報不等於一定會賣,申報股數是上限,申報後 3 日起 1 個月內可賣;信託/贈與多半是節稅或家族安排)")
    sell_picks = list(dict.fromkeys(x["sid"] for x in items if x["side"] == "sell"))
    return {"s16": result("s16", TITLE, lines, sell_picks, rows=items, n_all=len(rows))}
