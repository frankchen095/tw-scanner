"""
策略七:地緣分點買進——兩種,輸出時標明是哪一種

(A) 同區分點〔同區〕:券商分點跟公司總部在同一個縣市+鄉鎮市區
    條件:1. 公司總部不在臺北市、新北市  2. 市值>500億
          3. 同區所有地緣分點的合計淨買超金額 > 5,000萬,『單日』或『近5個交易日合計』任一達標
          4. 排除外資券商和沒有地址的自營單位(見 geo.py)
    地址解析不出區的公司(例如只寫「新竹科學園區」)不參與,會列在報表最後面。
(B) 庫藏股分點〔庫藏股〕:公司過去執行庫藏股時用的分點(名單 data/buyback_brokers.json,
    用 fenpoint 的分點歷史資料推算,見 scripts/06_build_buyback_brokers.py)
    條件:1. 不限市值、也不受「排除臺北市、新北市」限制  2. 該分點淨買超金額>5,000萬,單日或近5日任一達標
          3. 公司目前正在執行庫藏股時標〔庫藏股執行中〕

每檔一行,依金額由大到小(單日、5日取較大的那個排序):
  個股(縣市+區)〔同區/庫藏股〕單日 X 萬 / 5 日 X 萬,主要買超分點:分點A、分點B
同一檔如果 A、B 都符合,會各出現一行。
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from context import NO_MV, nodata, result  # noqa: E402
from db import load_buyback_brokers  # noqa: E402
from geo import same_district_brokers  # noqa: E402
from helpers import fmt_wan, recent_dates  # noqa: E402

TITLE = "【策略七:地緣分點買進】分點單日或近5日淨買超>5,000萬(同區:市值>500億;庫藏股:不限市值)"
THRESHOLD = 5e7
TOP_BROKERS = 3


def _targets(ctx):
    """回傳 ({stock_id: {trader_id}} 同區分點, {stock_id: {trader_id}} 庫藏股分點, {trader_id: 名稱})。"""
    names = {r[0]: r[1] for r in ctx.conn.execute("SELECT trader_id, name FROM broker_geo")}
    big = ctx.big or set()
    geo = {s: b for s, b in same_district_brokers(ctx.conn).items() if s in big and b}
    bb, bb_names = {}, {}
    for e in load_buyback_brokers():  # 庫藏股分點:不限市值
        sid = str(e["stock_id"])
        bb.setdefault(sid, set()).add(str(e["trader_id"]))
        bb_names[str(e["trader_id"])] = e.get("trader_name") or ""
    names.update({k: v for k, v in bb_names.items() if v})
    return geo, bb, names


def _lines(ctx, kind, targets, df, d1, d5, names, geo_loc, running):
    rows = []
    for sid, traders in targets.items():
        s = df[(df["stock_id"] == sid) & (df["trader_id"].isin(traders))]
        if s.empty:
            continue
        net_day = float(s[s["date"] == d1]["net"].sum())
        net_5 = float(s["net"].sum())
        if net_day <= THRESHOLD and net_5 <= THRESHOLD:
            continue
        basis = s if net_5 >= net_day else s[s["date"] == d1]
        top = basis.groupby("trader_id")["net"].sum().sort_values(ascending=False)
        top = [t for t, v in top.items() if v > 0][:TOP_BROKERS]
        rows.append((max(net_day, net_5), sid, net_day, net_5, [names.get(t, t) for t in top]))
    rows.sort(key=lambda x: -x[0])

    out = []
    for _, sid, nd, n5, tops in rows:
        city, dist = geo_loc.get(sid, (None, None))
        loc = f"({city or ''}{dist or ''})" if (city or dist) else ""
        tag = f"〔庫藏股執行中〕" if kind == "庫藏股" and sid in running else f"〔{kind}〕"
        out.append(
            f"{ctx.name(sid)}({sid}){loc}{ctx.risk.tags(sid) if ctx.risk else ''}{tag}"
            f"單日 {fmt_wan(nd)} / 5日 {fmt_wan(n5)},主要買超分點:{'、'.join(tops) or '無'}"
        )
    return out, [r[1] for r in rows]


def build(ctx):
    d5 = recent_dates(ctx.conn, "daily_dama", ctx.day, 5)
    if not d5 or d5[-1] != ctx.day:
        return {"s7": nodata("s7", TITLE, "今日無資料(今天的分點日報沒有抓到)")}

    geo, bb, names = _targets(ctx)
    all_ids = sorted(set(geo) | set(bb))
    qm_d, qm_s = ",".join("?" * len(d5)), ",".join("?" * len(all_ids))
    df = pd.read_sql(
        f"SELECT date, stock_id, trader_id, buy_amt - sell_amt AS net FROM daily_dama "
        f"WHERE date IN ({qm_d}) AND stock_id IN ({qm_s})",
        ctx.conn,
        params=list(d5) + all_ids,
    )

    geo_loc = {r[0]: (r[1], r[2]) for r in ctx.conn.execute("SELECT stock_id, city, district FROM company_geo")}
    running = {
        r[0]
        for r in ctx.conn.execute(
            "SELECT DISTINCT stock_id FROM buyback_programs "
            "WHERE done_flag = 'N' AND start_date <= ? AND end_date >= ?",
            (ctx.day, ctx.day),
        )
    }

    lines_a, picks_a = _lines(ctx, "同區", geo, df, ctx.day, d5, names, geo_loc, running)
    lines_b, picks_b = _lines(ctx, "庫藏股", bb, df, ctx.day, d5, names, geo_loc, running)

    lines = lines_a + lines_b
    if not lines:
        lines = ["今天沒有符合條件的股票"]
    if ctx.big is None:
        lines.append("(同區分點今天無資料:" + NO_MV + ")")
    if not bb:
        lines.append("(庫藏股分點名單 data/buyback_brokers.json 尚未建立,B 類今天沒有結果)")
    if len(d5) < 5:
        lines.append(f"(分點資料庫只有 {len(d5)} 個交易日,近5日合計不足5天)")

    # 區沒寫、無法比對的大市值公司,明確列出來(不用猜)
    unparsed = []
    for sid, city, dist in ctx.conn.execute("SELECT stock_id, city, district FROM company_geo"):
        if ctx.big and sid in ctx.big and city not in ("臺北市", "新北市") and not dist:
            unparsed.append(sid)
    if unparsed:
        names_txt = "、".join(ctx.name(s) or s for s in sorted(unparsed)[:12])
        more = f"…等{len(unparsed)}家" if len(unparsed) > 12 else f"共{len(unparsed)}家"
        lines.append(f"(地址沒寫區或無法判斷縣市,無法做同區比對:{names_txt}{more};完整名單在 data/geo_unparsed.txt)")

    return {"s7": result("s7", TITLE, lines, list(dict.fromkeys(picks_a + picks_b)))}
