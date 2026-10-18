"""
族群共振(K 項):同一族群當天有多檔出現在偏多榜 → 列為『族群訊號』

族群定義(兩層):
  1. 自訂子族群:data/groups.json,格式 {"族群名": ["代號", ...]},我預設放 AI 供應鏈十個環節(只放市值>500億的代表股,
     你可以直接手動改)。一檔股票可以同時屬於多個子族群。
  2. 預設產業別:stock_info.industry(證交所/櫃買產業別,很粗,只當保底)。一檔股票沒有在任何自訂族群裡,才用預設產業別。

偏多榜(各榜出現過就算,標它中了哪些類別):價格位置(策略一〔近高〕/策略五〔創高〕)、策略二今日買超榜〔分點〕、
策略七〔地緣〕、法人(策略八、九)〔法人〕、質押〔質押〕(策略十一月頻〔設質〕)。
共振條件:自訂子族群 ≥ 3 檔;預設產業別 ≥ 5 檔,且 ≥ 該產業『沒有在自訂族群、市值>500億』成員數的 20%。
族群內只要有股票同時在空方榜(策略三漲停放空、借券大增、散戶接刀榜、內部人申報轉讓〔真的要賣〕),整個族群標
『族群內有空方訊號:XX、YY』。
族群連續幾日共振標「連 N 日」(只算正式推播的紀錄,group_signals 表)。
成效追蹤:族群訊號另立一類(strategy='group'),達標族群內所有股票等權。
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from context import NO_MV, nodata, result  # noqa: E402

TITLE = "【族群訊號】同一族群當天多檔出現在偏多榜"
ROOT = Path(__file__).resolve().parent.parent.parent
CUSTOM_MIN, DEFAULT_MIN, DEFAULT_SHARE = 3, 5, 0.20


def load_groups():
    p = ROOT / "data" / "groups.json"
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    return {str(k): [str(x) for x in v] for k, v in data.items()}


def _bull_tags(res):
    """{sid: [類別標籤]}(依固定順序)"""
    tags = {}

    def add(sids, label):
        for sid in sids:
            tags.setdefault(sid, [])
            if label not in tags[sid]:
                tags[sid].append(label)

    s5 = set(res["s5"]["picks"]) if "s5" in res else set()
    s1 = set(res["s1"]["picks"]) if "s1" in res else set()
    add(s5, "創高")
    add(s1 - s5, "近高")
    add(res["s2_buy"]["picks"] if "s2_buy" in res else [], "分點")
    add(res["s7"]["picks"] if "s7" in res else [], "地緣")
    add([x for k in ("s8_1", "s8_5", "s9_trust", "s9_foreign") for x in (res[k]["picks"] if k in res else [])], "法人")
    add(res["s11"]["picks"] if "s11" in res else [], "質押")
    return tags


def _bear_sids(res):
    out = {}
    for key, label in (("s3", "漲停放空"), ("s13", "借券大增"), ("e_knife", "散戶接刀"), ("s16", "申報轉讓"), ("s11_release", "解質")):
        for sid in res[key]["picks"] if key in res else []:
            out.setdefault(sid, label)
    return out


def _industries(conn):
    return {sid: ind for sid, ind in conn.execute("SELECT stock_id, industry FROM stock_info WHERE is_common=1")}


def _streak(conn, day, name, members):
    """這個族群連續第幾個推播日共振(含今天)。昨天以前的紀錄存在 group_signals。"""
    try:
        dates = [r[0] for r in conn.execute(
            "SELECT DISTINCT date FROM signals WHERE date < ? ORDER BY date DESC LIMIT 60", (day,))]
        have = {r[0] for r in conn.execute("SELECT DISTINCT date FROM group_signals WHERE group_name=? AND date < ?",
                                           (name, day))}
    except Exception:  # noqa: BLE001
        return 1
    n = 1
    for d in dates:
        if d in have:
            n += 1
        else:
            break
    return n


def build(ctx, res):
    if ctx.big is None:
        return {"group": nodata("group", TITLE, NO_MV)}
    bull = _bull_tags(res)
    bear = _bear_sids(res)
    groups = load_groups()
    industries = _industries(ctx.conn)
    in_custom = {sid for members in groups.values() for sid in members}

    hits = []  # (組名, 檔數, [(sid, tags)], 空方[(sid, 標籤)], 種類)
    for name, members in groups.items():
        got = [(sid, bull[sid]) for sid in dict.fromkeys(members) if sid in bull]
        if len(got) >= CUSTOM_MIN:
            bears = [(sid, bear[sid]) for sid in dict.fromkeys(members) if sid in bear]
            hits.append((name, got, bears, "自訂"))
    # 預設產業別(只看沒有在任何自訂族群裡的股票)
    ungrouped_big = {sid for sid in ctx.big if sid not in in_custom}
    by_ind = {}
    for sid in ungrouped_big:
        by_ind.setdefault(industries.get(sid) or "其他", []).append(sid)
    for ind, members in by_ind.items():
        got = [(sid, bull[sid]) for sid in sorted(members) if sid in bull]
        if len(got) >= DEFAULT_MIN and len(got) >= DEFAULT_SHARE * len(members):
            bears = [(sid, bear[sid]) for sid in sorted(members) if sid in bear]
            hits.append((f"{ind}(產業別)", got, bears, "預設"))

    rows, lines, picks = [], [], []
    enriched = []
    for name, got, bears, kind in hits:
        n = _streak(ctx.conn, ctx.day, name, [s for s, _ in got])
        enriched.append((name, got, bears, kind, n))
    enriched.sort(key=lambda x: (-len(x[1]), -x[4], x[0]))
    for name, got, bears, kind, n in enriched:
        body = "、".join(f"{ctx.label(sid)}〔{'+'.join(tags)}〕" for sid, tags in got)
        run = f"(連 {n} 日)" if n >= 2 else ""
        line = f"{name} ─ {len(got)} 檔:{body}{run}"
        if bears:
            line += "\n  ⚠族群內有空方訊號:" + "、".join(f"{ctx.name(sid) or sid}〔{lab}〕" for sid, lab in bears)
        lines.append(line)
        for sid, _ in got:
            if sid not in picks:
                picks.append(sid)
        rows.append({"group": name, "kind": kind, "streak": n,
                     "members": [{"sid": sid, "tags": tags} for sid, tags in got],
                     "bears": [{"sid": sid, "label": lab} for sid, lab in bears]})
    if not lines:
        lines = ["今天沒有族群共振"]
    return {"group": result("group", TITLE, lines, picks, rows=rows)}


def record_groups(conn, day, res):
    """正式推播才呼叫:把今天達標的族群和成員存進 group_signals(算連 N 日用)"""
    r = res.get("group")
    if not r:
        return 0
    rows = [(day, g["group"], m["sid"]) for g in r.get("meta", {}).get("rows", []) for m in g["members"]]
    conn.executemany("INSERT OR IGNORE INTO group_signals VALUES (?,?,?)", rows)
    conn.commit()
    return len(rows)
