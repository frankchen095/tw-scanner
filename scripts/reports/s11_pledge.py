"""
策略十一(C):董監事/經理人/大股東質押、解質(月頻版)

【為什麼是月頻】指令要的是『每日申報』,但官方公開資料只有月報(證交所 t187ap11_L / 櫃買 mopsfin_t187ap11_O
『董監事持股餘額明細』,每月一份,逐人列目前持股與設質股數)。逐筆設質/解質的每日申報只在公開資訊觀測站網頁個別公司查詢,
沒有 JSON/CSV。你決定接受月頻:把『這個月』和『上個月』同一個人的設質股數相減,就是當月的設質/解質變化。

資料:fetchers.fetch_pledge_monthly 每次有新的一個月出現就逐人存進 pledge_person_monthly(第一次的那個月標 baseline,
所以要到第二個月的資料出現才能相減)。這段只在『新一個月資料入庫的那一天』推播,其他天不出現。

定義:同一個人(公司代號+職稱+姓名)  Δ = 這個月設質股數 − 上個月設質股數。
  〔設質〕Δ>0 = 偏多(大股東寧可借錢也不賣股);〔解質〕Δ<0 = 偏空(解開才能過戶賣出,視為準備賣股的前置動作)。方向不要寫反。
  只列 |Δ| ÷ 該人目前持股 ≥ 10%(低於 10% 視為小額周轉)。不設市值門檻,但每檔標市值。
  設質排前面、解質排後面;同方向內依「占其持股比」由大到小。
輸出:個股(代號) 市值X億〔設質〕董事長 王○○ 當月設質增加 X 萬股(占其持股 X%),累計質押 X%
接到其他模組:
  交集榜:市值>500億且〔設質〕的加入一類叫「質押」;〔解質〕的加入『訊號衝突』來源(標「同時在解質榜」)。
  風險標記:〔質押比高〕(董監整體質押比>50%)用同一份月報,見 risk_tags.py。
  成效追蹤:追蹤〔設質〕榜(偏多);〔解質〕不追蹤。
  策略十六(內部人申報轉讓):同一家公司、同一個人當月有〔解質〕的,標〔解質後轉讓〕並排最前面。
限制:月報看不到哪一天設質/解質,也分不出雜訊(借款到期還款順便解質、解完換銀行再質押)。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from context import nodata, result  # noqa: E402

T_PLEDGE = "【策略十一:董監/大股東設質】當月設質股數增加≥10%持股(月頻,偏多)"
T_RELEASE = "【策略十一:董監/大股東解質】當月設質股數減少≥10%持股(月頻,偏空)"
RATIO_MIN = 0.10


def latest_two(conn):
    return [r[0] for r in conn.execute("SELECT DISTINCT ym FROM pledge_person_monthly ORDER BY ym DESC LIMIT 2")]


def changes(conn):
    """回傳 (最新月, 上月, [dict]);資料不到兩個月回傳 (最新月 or None, None, [])"""
    yms = latest_two(conn)
    if len(yms) < 2:
        return (yms[0] if yms else None), None, []
    cur, prev = yms
    prev_map = {(s, t, n): p for s, t, n, p in conn.execute(
        "SELECT stock_id, title, name, pledged FROM pledge_person_monthly WHERE ym=?", (prev,))}
    out = []
    for sid, title, name, held, pledged in conn.execute(
            "SELECT stock_id, title, name, held, pledged FROM pledge_person_monthly WHERE ym=?", (cur,)):
        key = (sid, title, name)
        if key not in prev_map or held <= 0:
            continue  # 上個月沒有這個人(新上任/新進榜),不能比較,不列
        delta = pledged - prev_map[key]
        if delta == 0:
            continue
        out.append({"sid": sid, "title": title, "name": name, "held": held, "pledged": pledged,
                    "delta": delta, "ratio": abs(delta) / held, "total_ratio": pledged / held})
    return cur, prev, out


def released_people(conn):
    """給策略十六用:最新月有解質(Δ<0,不管多大)的人 {(stock_id, name)}"""
    cur, prev, ch = changes(conn)
    return {(x["sid"], x["name"]) for x in ch if x["delta"] < 0}


def build(ctx, always=False):
    keys = [("s11", T_PLEDGE), ("s11_release", T_RELEASE)]
    yms = latest_two(ctx.conn)
    if not yms:
        return {}
    cur = yms[0]
    first_seen = ctx.conn.execute("SELECT first_seen FROM pledge_person_monthly WHERE ym=? LIMIT 1", (cur,)).fetchone()[0]
    if first_seen != ctx.day and not always and not getattr(ctx, "show_all", False):
        return {}  # 不是新一個月資料入庫的那天,整段不出現
    if len(yms) < 2:
        why = f"今日無資料(質押月報目前只有 {cur} 一個月,要等下個月的資料出現才能相減;每月約中旬更新)"
        return {k: nodata(k, t, why) for k, t in keys}
    cur, prev, ch = changes(ctx.conn)
    ch = [x for x in ch if x["ratio"] >= RATIO_MIN]
    up = sorted([x for x in ch if x["delta"] > 0], key=lambda x: -x["ratio"])
    down = sorted([x for x in ch if x["delta"] < 0], key=lambda x: -x["ratio"])

    def line(i, x, kind):
        mv = ctx.mv.get(x["sid"]) if ctx.mv else None
        mv_txt = f"市值{mv / 1e8:,.0f}億" if mv else "市值無資料"
        verb = "當月設質增加" if kind == "設質" else "當月設質減少(解質)"
        return (f"{i}. {ctx.label(x['sid'])} {mv_txt}〔{kind}〕{x['title']} {x['name']} {verb} "
                f"{abs(x['delta']) / 10000:,.0f} 萬股(占其持股 {x['ratio'] * 100:.0f}%),累計質押 {x['total_ratio'] * 100:.0f}%")

    def rows(lst, kind):
        return [{**x, "kind": kind, "mv": ctx.mv.get(x["sid"]) if ctx.mv else None} for x in lst]

    note = f"(月頻:{cur} 對比 {prev} 同一個人的設質股數;看不到哪一天,也分不出借款到期還款順便解質等雜訊)"
    lines_up = [line(i, x, "設質") for i, x in enumerate(up, 1)] or ["本月沒有符合條件的設質"]
    lines_down = [line(i, x, "解質") for i, x in enumerate(down, 1)] or ["本月沒有符合條件的解質"]
    lines_up.append(note)
    # picks:設質榜追蹤全部;解質榜不追蹤但交集榜/族群要用
    return {
        "s11": result("s11", T_PLEDGE, lines_up, list(dict.fromkeys(x["sid"] for x in up)), rows=rows(up, "設質"), ym=cur, prev=prev),
        "s11_release": result("s11_release", T_RELEASE, lines_down, list(dict.fromkeys(x["sid"] for x in down)),
                              rows=rows(down, "解質"), ym=cur, prev=prev),
    }
