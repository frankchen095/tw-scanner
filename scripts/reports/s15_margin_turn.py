"""
策略十五(G):財報毛利率轉折(事件榜) + 基本面動能榜(營收年增 + 毛利率 + 營益率連續季增)

資料:FinMind TaiwanStockFinancialStatements(季報損益表,date = 財季季底日,單季值)的營收 Revenue、
營業毛利 GrossProfit、營業利益 OperatingIncome;月營收用 TaiwanStockMonthRevenue(全市場近 15 個月,
存在 month_revenue_all)。毛利率 = 毛利÷營收,營益率 = 營業利益÷營收。
FinMind 季報沒有『入庫日』欄位,所以自己記:fetchers.fetch_financials 每天重查最近季度,資料庫還沒有的
(股票, 季度) 就是『今天新入庫』(financials_quarterly.first_seen = 今天)。金融股等沒有毛利欄位的算不出來,直接略過
(報表最後列出略過幾檔)。

【事件榜 s15】只在有新季報入庫的日子出現,市值>500億,今天新入庫的最新一季符合任一:
  (a) 毛利率連續 2 季上升(Q−2<Q−1<Q),且本季高於去年同季(Q−4)
  (b) 營業利益由負轉正(上季<0,本季>0)
  (c) 毛利率單季上升 ≥ 3 個百分點
  依本季毛利率升幅由大到小;單日超過 20 檔只列前 20、註明總數。
  反向榜〔毛利轉弱〕(只顯示):毛利率和營益率都連 2 季下降。
  不納入交集榜(事件型),追蹤成效。

【基本面動能榜 s15_mom / s15_mom2】市值>500億,三個條件同時成立才上榜:
  1. 營收年增:最新一個月 YoY > 0,且近 3 個月累計 YoY > 0
  2. 毛利率連續 2 季上升(最近 3 季 Q−2<Q−1<Q)
  3. 營益率連續 2 季上升(最近 3 季)
  〔三升〕= 三個全中;〔二升〕= 毛利率和營益率連續季增,但營收 YoY 沒過(另列一小段)。
  加碼標記:最新一季毛利率高於去年同季〔年增〕;營收 YoY > 30%〔高成長〕。
  只在兩種日子出現:(a) 每月 10 日之後第一個有新月營收入庫的交易日;(b) 有新季報入庫的日子。
  新進榜、掉出榜排前面,續留的標「連 N 期」(N = 連續幾次推播都在榜上)。不納入交集榜,追蹤成效,
  並在週五彙總跟策略四(月營收 MoM)並列。
  不限市值的完整名單另存 data/fundamental_momentum_<日期>.csv,不推播。
"""

import csv
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from context import NO_MV, nodata, result  # noqa: E402

T_EVENT = "【策略十五:財報毛利率轉折】今天新公布的季報,市值>500億(偏多)"
T_WEAK = "【策略十五:毛利轉弱】毛利率和營益率都連2季下降(只顯示)"
T_MOM = "【基本面動能榜〔三升〕】營收年增 + 毛利率 + 營益率連續季增,市值>500億(偏多)"
T_MOM2 = "【基本面動能榜〔二升〕】毛利率、營益率連續季增但營收年增沒過(偏多)"
MAX_EVENT = 20
ROOT = Path(__file__).resolve().parent.parent.parent


def _q_shift(iso, n):
    """季底日往前/往後 n 季(n<0 往前)"""
    y, m = int(iso[:4]), int(iso[5:7])
    idx = y * 4 + (m // 3 - 1) + n
    y2, q2 = divmod(idx, 4)
    mm = (q2 + 1) * 3
    dd = 31 if mm in (3, 12) else 30
    return f"{y2}-{mm:02d}-{dd}"


def _load_quarters(conn):
    q = {}
    for sid, d, rev, gp, oi, fs in conn.execute(
            "SELECT stock_id, date, revenue, gross_profit, op_income, first_seen FROM financials_quarterly"):
        q.setdefault(sid, {})[d] = (rev, gp, oi, fs)
    return q


def _gm(rec):
    return rec[1] / rec[0] if rec and rec[0] and rec[0] > 0 else None


def _om(rec):
    return rec[2] / rec[0] if rec and rec[0] and rec[0] > 0 and rec[2] is not None else None


def _revenue_growth(conn, day):
    """{sid: (最新月YoY, 近3月YoY)};缺任何一個月就不算"""
    rows = {}
    for sid, ym, rev in conn.execute("SELECT stock_id, ym, revenue FROM month_revenue_all"):
        rows.setdefault(sid, {})[ym] = rev
    out = {}
    for sid, d in rows.items():
        L = max(d)
        y, m = divmod(L, 100)

        def sub(ym, k):  # ym 往前 k 個月
            yy, mm = divmod(ym, 100)
            t = yy * 12 + (mm - 1) - k
            return (t // 12) * 100 + t % 12 + 1

        cur = [d.get(sub(L, i)) for i in range(3)]
        old = [d.get(sub(L, i + 12)) for i in range(3)]
        if None in cur or None in old or not old[0] or not sum(old):
            continue
        out[sid] = (cur[0] / old[0] - 1, sum(cur) / sum(old) - 1, L)
    return out


def _series(q, sid):
    """回傳 (最新季底日, {k: (gm, om)}) k=0,-1,-2,-3,-4;缺的季是 None"""
    d = q.get(sid)
    if not d:
        return None
    L = max(d)
    out = {}
    for k in (0, -1, -2, -3, -4):
        rec = d.get(_q_shift(L, k))
        out[k] = (_gm(rec), _om(rec), rec)
    return L, out


def _trigger(ctx):
    """回傳觸發原因(文字)或 None。"""
    reasons = []
    n_fin = ctx.conn.execute(
        "SELECT COUNT(*) FROM financials_quarterly WHERE first_seen=?", (ctx.day,)).fetchone()[0]
    if n_fin:
        reasons.append("今天有新季報入庫")
    d = date.fromisoformat(ctx.day)
    py, pm = (d.year, d.month - 1) if d.month > 1 else (d.year - 1, 12)
    first = ctx.conn.execute(
        "SELECT MIN(first_seen) FROM month_revenue WHERE revenue_year=? AND revenue_month=? AND first_seen >= ?",
        (py, pm, f"{d.year}-{d.month:02d}-10")).fetchone()[0]
    if first == ctx.day:
        reasons.append("月營收公布後第一個交易日")
    return "、".join(reasons) or None


def _fmt_series(vals):
    return "→".join("-" if v is None else f"{v * 100:.1f}%" for v in vals)


def build(ctx):
    keys = [("s15", T_EVENT), ("s15_weak", T_WEAK), ("s15_mom", T_MOM), ("s15_mom2", T_MOM2)]
    if ctx.big is None:
        return {k: nodata(k, t, NO_MV) for k, t in keys}
    q = _load_quarters(ctx.conn)
    if not q:
        return {k: nodata(k, t, "今日無資料(季報損益表資料庫是空的)") for k, t in keys}
    out = {}

    # ---------- 事件榜 ----------
    ev, weak, skipped = [], [], []
    for sid in sorted(ctx.big):
        s = _series(q, sid)
        if s is None:
            skipped.append(sid)
            continue
        L, v = s
        rec0 = v[0][2]
        if rec0 is None or rec0[3] != ctx.day:
            continue
        g0, g1, g2, g4 = v[0][0], v[-1][0], v[-2][0], v[-4][0]
        o0, o1, o2 = v[0][1], v[-1][1], v[-2][1]
        tags = []
        if None not in (g0, g1, g2, g4) and g2 < g1 < g0 and g0 > g4:
            tags.append("a")
        if v[-1][2] is not None and v[-1][2][2] is not None and rec0[2] is not None and v[-1][2][2] < 0 < rec0[2]:
            tags.append("b")
        if None not in (g0, g1) and g0 - g1 >= 0.03:
            tags.append("c")
        if tags:
            ev.append((sid, L, [v[-3][0], g2, g1, g0], o0, g0 - (g1 or 0), "/".join(tags)))
        if None not in (g0, g1, g2, o0, o1, o2) and g2 > g1 > g0 and o2 > o1 > o0:
            weak.append((sid, L, [g2, g1, g0], o0))
    ev.sort(key=lambda x: -x[4])
    total_ev = len(ev)
    ev = ev[:MAX_EVENT]
    ev_lines = [f"{i}. {ctx.label(sid)} 毛利率 近4季 {_fmt_series(gms)}〔{tag}〕營益率 "
                + ("-" if o0 is None else f"{o0 * 100:.1f}%")
                for i, (sid, L, gms, o0, _, tag) in enumerate(ev, 1)]
    if not ev_lines:
        ev_lines = ["今天沒有符合條件的新季報"]
    elif total_ev > MAX_EVENT:
        ev_lines.append(f"(今天共 {total_ev} 檔符合,只列前 {MAX_EVENT} 檔)")
    if skipped:
        ev_lines.append(f"(略過 {len(skipped)} 檔:市值>500億但沒有毛利資料,例如金融股)")
    out["s15"] = result("s15", T_EVENT, ev_lines, [x[0] for x in ev], total=total_ev, skipped=len(skipped),
                        rows=[{"sid": sid, "q": L, "gms": gms, "om": o0, "tag": tag} for sid, L, gms, o0, _, tag in ev])
    wk_lines = [f"{i}. {ctx.label(sid)} 毛利率 {_fmt_series(gms)} 營益率 " + ("-" if o0 is None else f"{o0 * 100:.1f}%")
                for i, (sid, L, gms, o0) in enumerate(weak[:5], 1)] or ["今天沒有符合條件的新季報"]
    out["s15_weak"] = result("s15_weak", T_WEAK, wk_lines, [],
                             rows=[{"sid": sid, "q": L, "gms": gms, "om": o0} for sid, L, gms, o0 in weak[:5]])

    # ---------- 基本面動能榜(週期性出現) ----------
    reason = _trigger(ctx)
    if reason is None and not getattr(ctx, "show_all", False):
        return out
    growth = _revenue_growth(ctx.conn, ctx.day)
    cand = []  # (sid, tier, ...)
    for sid in q:
        s = _series(q, sid)
        if s is None:
            continue
        L, v = s
        g0, g1, g2, g4 = v[0][0], v[-1][0], v[-2][0], v[-4][0]
        o0, o1, o2 = v[0][1], v[-1][1], v[-2][1]
        if None in (g0, g1, g2, o0, o1, o2) or not (g2 < g1 < g0 and o2 < o1 < o0):
            continue
        gr = growth.get(sid)
        rev_ok = gr is not None and gr[0] > 0 and gr[1] > 0
        cand.append({"sid": sid, "tier": "三升" if rev_ok else "二升", "q": L, "gms": [g2, g1, g0],
                     "oms": [o2, o1, o0], "yoy": gr[0] if gr else None, "yoy3": gr[1] if gr else None,
                     "yearly": g4 is not None and g0 > g4, "high": gr is not None and gr[0] > 0.30,
                     "om_up": o0 - o1})
    # 不限市值完整名單 → CSV(不推播)
    try:
        path = ROOT / "data" / f"fundamental_momentum_{ctx.day}.csv"
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["代號", "名稱", "分級", "最新季", "營收YoY", "近3月YoY", "毛利率Q-2", "毛利率Q-1", "毛利率Q",
                        "營益率Q-2", "營益率Q-1", "營益率Q", "毛利率高於去年同季", "市值(億)"])
            for c in sorted(cand, key=lambda c: (c["tier"] != "三升", -c["om_up"])):
                mv = (ctx.mv or {}).get(c["sid"])
                w.writerow([c["sid"], ctx.name(c["sid"]), c["tier"], c["q"],
                            "" if c["yoy"] is None else f"{c['yoy']:.4f}", "" if c["yoy3"] is None else f"{c['yoy3']:.4f}",
                            *[f"{x:.4f}" for x in c["gms"]], *[f"{x:.4f}" for x in c["oms"]],
                            "是" if c["yearly"] else "否", "" if mv is None else f"{mv / 1e8:.0f}"])
    except Exception as e:  # noqa: BLE001
        print(f"  (動能榜完整名單 CSV 寫入失敗:{str(e)[:80]})")

    big = [c for c in cand if c["sid"] in ctx.big]
    snaps = [r[0] for r in ctx.conn.execute(
        "SELECT DISTINCT date FROM momentum_snapshots WHERE date < ? ORDER BY date DESC LIMIT 20", (ctx.day,))]
    prev_members = {}
    for tier in ("三升", "二升"):
        prev_members[tier] = [
            {r[0] for r in ctx.conn.execute(
                "SELECT stock_id FROM momentum_snapshots WHERE date=? AND tier=?", (d, tier))} for d in snaps]

    def render(tier, title, key):
        lst = sorted([c for c in big if c["tier"] == tier], key=lambda c: -c["om_up"])
        prevs = prev_members[tier]
        last = prevs[0] if prevs else set()
        lines, rows = [], []
        new = [c for c in lst if c["sid"] not in last] if prevs else []
        old = [c for c in lst if c["sid"] in last] if prevs else lst
        exited = sorted(last - {c["sid"] for c in lst})
        for group, label in ((new, "新進"), (old, "")):
            for c in group:
                n = 1
                for pm in prevs:
                    if c["sid"] in pm:
                        n += 1
                    else:
                        break
                flags = (f"〔{tier}〕" + ("〔年增〕" if c["yearly"] else "") + ("〔高成長〕" if c["high"] else ""))
                yoy = ("營收YoY -" if c["yoy"] is None else
                       f"營收YoY {c['yoy'] * 100:+.0f}%(3M {c['yoy3'] * 100:+.0f}%)")
                head = f"〔新進〕" if label == "新進" else (f"〔連 {n} 期〕" if n >= 2 else "")
                lines.append(f"{len(lines) + 1}. {ctx.label(c['sid'])}{head}{flags}{yoy} 毛利率 {_fmt_series(c['gms'])}"
                             f" 營益率 {_fmt_series(c['oms'])}")
                rows.append({**c, "label": label, "streak": n})
        if exited:
            lines.append("掉出榜:" + "、".join(f"{ctx.name(s) or s}({s})" for s in exited))
        if not lst:
            lines.insert(0, "本期沒有符合條件的股票")
        return result(key, title, lines, [c["sid"] for c in lst], rows=rows, reason=reason or "(手動顯示)",
                      exited=exited)

    out["s15_mom"] = render("三升", T_MOM, "s15_mom")
    out["s15_mom2"] = render("二升", T_MOM2, "s15_mom2")
    if not getattr(ctx, "dry_run", False):
        ctx.conn.executemany("INSERT OR IGNORE INTO momentum_snapshots VALUES (?,?,?)",
                             [(ctx.day, c["tier"], c["sid"]) for c in big])
        ctx.conn.commit()
    return out
