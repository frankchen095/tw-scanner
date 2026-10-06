"""
07_geo_report.py —— 地緣分點(策略七 A)的對照表檢查:列出解析不出區的公司、符合條件的家數

會做:
  1. (必要時)更新公司/分點地址對照表
  2. 印出:市值>500億、總部不在臺北市/新北市(條件1、2)的公司有幾家;其中同區完全沒有分點的幾家
  3. 把「市值>500億、但地址沒寫區或縣市不明」的公司寫到 data/geo_unparsed.txt
     (這些公司不會參與同區比對,我不會猜。你確認後可以填進 data/geo_overrides.json)

用法:
    python scripts/07_geo_report.py [--date 2026-10-06]
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from db import ROOT, connect  # noqa: E402
from geo import refresh_geo, same_district_brokers  # noqa: E402
from helpers import BIG_MARKET_VALUE  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("--date", default=None)
args = parser.parse_args()

conn = connect()
err = refresh_geo(conn)
if err:
    print(err)
day = args.date or conn.execute("SELECT MAX(date) FROM daily_market_value").fetchone()[0]
big = {r[0] for r in conn.execute("SELECT stock_id FROM daily_market_value WHERE date=? AND market_value>?", (day, BIG_MARKET_VALUE))}
comp = {r[0]: r for r in conn.execute("SELECT stock_id, name, address, city, district FROM company_geo")}
sd = same_district_brokers(conn)

tp = [s for s in big if comp.get(s, (0, 0, 0, None))[3] in ("臺北市", "新北市")]
unknown = [s for s in big if s in comp and comp[s][3] is None]
cond12 = [s for s in big if s in comp and comp[s][3] not in (None, "臺北市", "新北市")]
with_dist = [s for s in cond12 if comp[s][4]]
no_branch = [s for s in with_dist if not sd.get(s)]
no_dist = [s for s in cond12 if not comp[s][4]]

print(f"報表日期 {day},市值>500億的普通股 {len(big)} 家")
print(f"  總部在臺北市/新北市(排除):{len(tp)} 家")
print(f"  縣市無法判斷(外國公司或竹科只寫園區):{len(unknown)} 家")
print(f"  符合條件1、2(市值>500億、總部在其他縣市):{len(cond12)} 家")
print(f"    其中地址有寫區、可做同區比對:{len(with_dist)} 家")
print(f"      同區完全沒有分點:{len(no_branch)} 家 → " + "、".join(f"{comp[s][1][:5]}({comp[s][3]}{comp[s][4]})" for s in sorted(no_branch)))
print(f"      同區有分點:{len(with_dist) - len(no_branch)} 家")
print(f"    其中地址沒寫區、無法比對:{len(no_dist)} 家")

lines = [f"市值>500億、但地址沒寫區或縣市不明的公司(報表日期 {day});這些公司不參與同區比對。",
         "如果你確認它們的縣市+區,填進 data/geo_overrides.json,下次更新對照表就會生效。", ""]
for title, ids in [("縣市無法判斷", unknown), ("縣市有、區沒寫", no_dist)]:
    lines.append(f"== {title}({len(ids)} 家) ==")
    for s in sorted(ids):
        lines.append(f"{s} {comp[s][1]}  |  {comp[s][2]}  |  解析結果:{comp[s][3]} {comp[s][4]}")
    lines.append("")
out = ROOT / "data" / "geo_unparsed.txt"
out.write_text("\n".join(lines), encoding="utf-8")
print(f"\n已寫入 {out}")

bu = conn.execute("SELECT COUNT(*) FROM broker_geo WHERE city IS NULL OR district IS NULL").fetchone()[0]
nb = conn.execute("SELECT COUNT(*) FROM broker_geo").fetchone()[0]
fo = conn.execute("SELECT COUNT(*) FROM broker_geo WHERE is_foreign=1").fetchone()[0]
print(f"分點 {nb} 個:地址解析不出區或沒地址 {bu} 個(含自營單位),判定為外資 {fo} 個")
