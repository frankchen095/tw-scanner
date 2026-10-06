"""
06_build_buyback_brokers.py —— 一次性建表:推算公司過去執行庫藏股時用的分點(策略七 B)

做法(公告不會寫用哪個分點,所以用推算的):
  1. 從公開資訊觀測站 t35sc09 抓 2023-01-01 到今天所有上市櫃公司的庫藏股買回計畫
     (買回期間起迄、本次已買回股數),存進 scanner.db 的 buyback_programs。
  2. 只處理『已執行完畢』且有『實際已買回股數』的計畫(執行中的計畫還沒有這個數字)。
  3. 用 fenpoint 專案的分點歷史資料(chips.db,400 檔股票、2023-01-03 起),在買回期間內,
     對每個分點算 累計淨買超股數 = 買進 − 賣出。條件:
       - |淨買超股數 − 公告已買回股數| ÷ 公告已買回股數 ≤ 5%
       - 持續買超:買回期間內,有淨買超的交易日占這個分點有交易的天數 ≥ 70%(--min-up-days 調整)
       - 不設賣出上限(使用者決定;可用 --max-sell 0.02 加回)。持續買超是用來擋『一般營業處剛好
         淨買超數字碰巧接近』的巧合:真正的庫藏股分點天天買,一般營業處買買賣賣只有約 6 成的天數買超
  4. 第一層『股數對上』:剛好只有 1 個分點符合 → 辨識成功。
  5. 第二層『穩定買超』(第一層沒辨識出來的才用,可用 --no-tier2 關掉):看公布實施庫藏股後,哪個分點
     穩定在買——有淨買超的天數 ≥ 70%、交易天數 ≥ 買回期間的一半、淨買超最多的那個分點,而且淨買超
     ≥ 第二名的 2 倍(或只有它一個),淨買超又落在公告已買回股數的 70%~130% 內 → 辨識成功。
     (驗證:第一層辨識出來的 43 筆,穩定買超第一名有 36 筆是同一個分點。)
  6. 都不符合 → 『無法辨識』,不挑最接近的硬湊。
輸出:
  data/buyback_brokers.json     辨識成功的(股票代號、分點代號、分點名稱、買回期間、比對誤差…)
  data/buyback_match_report.csv 每一筆計畫的處理結果和原因(含沒辨識成功的,方便你檢查)
並印出:有買回紀錄的公司幾家、成功辨識幾家、其中市值>500億的幾家。

用法:
    python scripts/06_build_buyback_brokers.py
    python scripts/06_build_buyback_brokers.py --chips-db D:\\path\\chips.db --tol 0.05 --max-sell 0.02
"""

import argparse
import csv
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pandas as pd  # noqa: E402
from db import BUYBACK_BROKERS_PATH, ROOT, connect  # noqa: E402
from mops_buyback import fetch_programs, store_programs  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("--chips-db", default=str(ROOT.parent / "fenpoint" / "chips.db"))
parser.add_argument("--tol", type=float, default=0.05, help="淨買超股數和公告已買回股數的容許誤差(預設 5%%)")
parser.add_argument("--max-sell", type=float, default=None, help="賣出股數占買進股數的上限(預設不設上限;例:0.02 = 2%%)")
parser.add_argument("--out-dir", default=str(ROOT / "data"), help="輸出 json/csv 的資料夾(預設 data/)")
parser.add_argument("--min-up-days", type=float, default=0.7, help="持續買超:買回期間內有淨買超的交易日,占這個分點有交易的天數至少多少(預設 70%%)")
parser.add_argument("--no-tier2", action="store_true", help="不要用第二層規則(穩定買超且明顯突出)")
parser.add_argument("--stable-days", type=float, default=0.5, help="第二層:這個分點的交易天數至少占買回期間交易日的比例(預設 50%%)")
parser.add_argument("--standout", type=float, default=2.0, help="第二層:最穩定買超的分點,淨買超至少是第二名的幾倍(預設 2 倍)")
parser.add_argument("--t2-lo", type=float, default=0.7, help="第二層:淨買超 ÷ 公告買回股數的下限(預設 70%%)")
parser.add_argument("--t2-hi", type=float, default=1.3, help="第二層:淨買超 ÷ 公告買回股數的上限(預設 130%%)")
parser.add_argument("--no-write", action="store_true", help="只印統計,不覆蓋 json 和 csv(比較不同門檻用)")
parser.add_argument("--from-db", action="store_true", help="不重新抓公開資訊觀測站,用資料庫裡已有的計畫")
args = parser.parse_args()

conn = connect()
if not args.from_db:
    print("抓公開資訊觀測站庫藏股買回計畫(2023-01-01 ~ 今天,上市+上櫃,約 15 秒)…")
    progs = fetch_programs(date(2023, 1, 1), date.today())
    store_programs(conn, progs)
    print(f"  共 {len(progs)} 筆")
rows = conn.execute(
    "SELECT stock_id, board_date, start_date, end_date, planned_shares, bought_shares, done_flag, market "
    "FROM buyback_programs WHERE board_date >= '2023-01-01' ORDER BY stock_id, start_date"
).fetchall()

chips = sqlite3.connect(f"file:{Path(args.chips_db).as_posix()}?mode=ro", uri=True, timeout=10)
universe = {r[0] for r in chips.execute("SELECT DISTINCT stock_id FROM prices")}
chips_min = "2023-01-03"
chips_max = chips.execute("SELECT MAX(date) FROM prices").fetchone()[0]
print(f"分點歷史資料:{len(universe)} 檔股票,{chips_min} ~ {chips_max}")

def up_share(sid, tid, start, end):
    """買回期間內,這個分點『有淨買超(當天買進 > 賣出)的天數』占『它有交易的天數』的比例。"""
    r = chips.execute(
        "SELECT SUM(net > 0), COUNT(*) FROM (SELECT SUM(buy) - SUM(sell) AS net FROM chips "
        "WHERE stock_id=? AND trader_id=? AND date BETWEEN ? AND ? GROUP BY date)",
        (sid, tid, start, end),
    ).fetchone()
    return (r[0] or 0) / r[1] if r[1] else 0.0


report, identified = [], []
for sid, board, start, end, plan, bought, done, market in rows:
    rec = {"stock_id": sid, "market": market, "board_date": board, "start": start, "end": end,
           "planned_shares": plan, "bought_shares": bought, "done_flag": done}

    def skip(status):
        rec.update(status=status)
        report.append(rec)

    if done != "Y" or not bought:
        skip("執行中或尚無實際買回股數(無法推算)")
        continue
    if sid not in universe:
        skip("不在分點歷史資料的 400 檔股票內")
        continue
    if start < chips_min or end > chips_max:
        skip("買回期間超出分點歷史資料的日期範圍")
        continue

    df = pd.read_sql(
        "SELECT trader_id, MAX(trader_name) AS name, date, SUM(buy) AS b, SUM(sell) AS s FROM chips "
        "WHERE stock_id=? AND date BETWEEN ? AND ? GROUP BY trader_id, date",
        chips,
        params=(sid, start, end),
    )
    n_days = df["date"].nunique()
    df["net"] = df["b"] - df["s"]
    g = df.groupby("trader_id").agg(
        name=("name", "max"), b=("b", "sum"), s=("s", "sum"), days=("date", "nunique"),
        up=("net", lambda x: int((x > 0).sum())),
    )
    g = g[g["b"] > 0].copy()
    g["net"] = g["b"] - g["s"]
    g["up_pct"] = g["up"] / g["days"]
    g["err"] = (g["net"] - bought) / bought
    g["sell_ratio"] = g["s"] / g["b"]
    if len(g):
        c0 = g.loc[g["err"].abs().idxmin()]
        rec.update(closest_trader=f"{g['err'].abs().idxmin()} {c0['name']}", closest_error_pct=round(c0["err"] * 100, 1),
                   closest_sell_ratio_pct=round(c0["sell_ratio"] * 100, 1))

    def accept(tid, method, **extra):
        r = g.loc[tid]
        rec.update(status="辨識成功", method=method, trader_id=tid, trader_name=r["name"],
                   error_pct=round(r["err"] * 100, 2), sell_ratio_pct=round(r["sell_ratio"] * 100, 2),
                   up_days_pct=round(r["up_pct"] * 100))
        identified.append({
            "stock_id": sid, "trader_id": tid, "trader_name": r["name"], "start": start, "end": end,
            "bought_shares": bought, "net_shares": float(r["net"]), "error_pct": round(r["err"] * 100, 2),
            "sell_ratio_pct": round(r["sell_ratio"] * 100, 2), "up_days_pct": round(r["up_pct"] * 100),
            "method": method, **extra,
        })

    cond = (g["err"].abs() <= args.tol) & (g["up_pct"] >= args.min_up_days)
    if args.max_sell is not None:
        cond &= g["sell_ratio"] <= args.max_sell
    cands = g[cond]
    if len(cands) == 1:
        accept(cands.index[0], "股數對上")
    else:
        done_t2 = False
        if not args.no_tier2:
            stable = g[(g["net"] > 0) & (g["up_pct"] >= args.min_up_days) & (g["days"] >= args.stable_days * n_days)]
            stable = stable.sort_values("net", ascending=False)
            if len(stable):
                top = stable.iloc[0]
                ratio12 = top["net"] / stable.iloc[1]["net"] if len(stable) > 1 else float("inf")
                over = top["net"] / bought
                if ratio12 >= args.standout and args.t2_lo <= over <= args.t2_hi:
                    accept(stable.index[0], "穩定買超", net_over_announced_pct=round(over * 100),
                           top_over_second=None if ratio12 == float("inf") else round(float(ratio12), 1))
                    done_t2 = True
        if not done_t2:
            rec.update(status=f"無法辨識(有 {len(cands)} 個分點都符合,不挑)" if len(cands) > 1 else "無法辨識(沒有分點符合)")
    report.append(rec)

if not args.no_write:
    (Path(args.out_dir) / "buyback_brokers.json").write_text(json.dumps(identified, ensure_ascii=False, indent=1), encoding="utf-8")
fields = ["stock_id", "market", "board_date", "start", "end", "planned_shares", "bought_shares", "done_flag",
          "status", "method", "trader_id", "trader_name", "error_pct", "sell_ratio_pct", "up_days_pct",
          "closest_trader", "closest_error_pct", "closest_sell_ratio_pct"]
if not args.no_write:
    with open(Path(args.out_dir) / "buyback_match_report.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(report)

mv_day = conn.execute("SELECT MAX(date) FROM daily_market_value").fetchone()[0]
big = {r[0] for r in conn.execute("SELECT stock_id FROM daily_market_value WHERE date=? AND market_value>5e10", (mv_day,))}
companies = {r["stock_id"] for r in report}
ok_companies = {e["stock_id"] for e in identified}
finished = [r for r in report if r["done_flag"] == "Y" and r["bought_shares"]]
print("\n===== 結果 =====")
print(f"買回計畫共 {len(report)} 筆,涉及 {len(companies)} 家公司(2023-01 ~ 今天,上市+上櫃)")
print(f"  已執行完畢且有實際買回股數:{len(finished)} 筆;執行中/尚無數字:{len(report) - len(finished)} 筆")
by_status = {}
for r in report:
    k = r["status"].split("(")[0]
    by_status[k] = by_status.get(k, 0) + 1
for k, v in sorted(by_status.items(), key=lambda kv: -kv[1]):
    print(f"    {k}: {v} 筆")
print(f"辨識成功:{len(identified)} 筆計畫,{len(ok_companies)} 家公司")
for mth in ("股數對上", "穩定買超"):
    sub = [e for e in identified if e.get("method") == mth]
    print(f"    其中「{mth}」:{len(sub)} 筆,{len({e['stock_id'] for e in sub})} 家公司")
print(f"  其中市值>500億(依 {mv_day} 市值):{len(ok_companies & big)} 家 → {sorted(ok_companies & big)}")
print(f"有買回紀錄的公司中,市值>500億的:{len(companies & big)} 家")
print(f"\n已寫入 {BUYBACK_BROKERS_PATH} 和 data/buyback_match_report.csv")
