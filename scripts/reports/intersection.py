"""
交集榜:當天同時出現在兩類以上「偏多」策略的股票

偏多策略的分類(同類只算一次):
  策略一            創新高策略(再漲3%內創60日新高)
  策略二            前40大分點『今日買超榜』(不含近3日版)
  策略五            今日創60日新高
  策略七            地緣分點買進(第二階段才有)
  法人              策略八、策略九合算一類(避免自己跟自己重複)
訊號衝突:交集榜裡的股票如果同時出現在策略三(漲停放空)或策略二的今日賣超榜,另外標出來。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from context import result  # noqa: E402

TITLE = "【交集榜】同時出現在兩類以上偏多策略"


def categories(res):
    """從各策略結果整理出 {類別名稱: 股票代號集合}。res 是 {策略key: result}。"""

    def picks(*keys):
        out = set()
        for k in keys:
            out |= set(res[k]["picks"]) if k in res else set()
        return out

    return {
        "策略一": picks("s1"),
        "策略二": picks("s2_buy"),
        "策略五": picks("s5"),
        "策略七": picks("s7"),
        "法人": picks("s8_1", "s8_5", "s9_trust", "s9_foreign"),
    }


def build(ctx, res):
    cats = categories(res)
    conflict_src = {
        "策略三(漲停放空)": set(res["s3"]["picks"]) if "s3" in res else set(),
        "策略二賣超榜": set(res["s2_sell"]["picks"]) if "s2_sell" in res else set(),
    }
    all_ids = set().union(*cats.values())
    multi = {}
    for sid in all_ids:
        hit = [name for name, ids in cats.items() if sid in ids]
        if len(hit) >= 2:
            multi[sid] = hit

    rows = sorted(multi.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    lines = []
    for i, (sid, hit) in enumerate(rows, 1):
        conflicts = [n for n, ids in conflict_src.items() if sid in ids]
        warn = f" ⚠訊號衝突:同時在{'、'.join(conflicts)}" if conflicts else ""
        lines.append(f"{i}. {ctx.label(sid)} {'+'.join(hit)}{warn}")
    if not lines:
        lines = ["今天沒有同時出現在兩類以上偏多策略的股票"]

    missing = [k for k in ("s1", "s2_buy", "s5", "s8_1") if k not in res]
    if missing:
        lines.append(f"(注意:策略 {', '.join(missing)} 今天沒有結果,交集榜可能不完整)")
    return result("intersection", TITLE, lines, [sid for sid, _ in rows])
