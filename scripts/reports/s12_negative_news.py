"""
策略十二:負面關鍵字掃描(純規則,不呼叫 AI)

沿用策略六的 Google News RSS 抓法和 26 小時回溯:每個負面關鍵字搜一次,標題同時含
『關鍵字』和『上市櫃公司名稱』(用股票名單比對)才列;一家公司當天多則只列最早那則。
不設市值門檻;不進交集榜、不進訊號衝突、不追蹤成效。只是提醒,要自己點進去看。
"""

import re
import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from context import nodata, result  # noqa: E402
import feature3_news as news  # noqa: E402

TITLE = "【負面新聞關鍵字掃描】近26小時標題含負面關鍵字的上市櫃公司"

# ---- 關鍵字清單(放在最上面,方便以後改)----
NEGATIVE_KEYWORDS = [
    "下修", "砍單", "裁員", "停工", "火災", "氣爆", "違約", "跳票", "掏空", "調查", "搜索", "約談", "處分",
    "罰款", "解約", "召回", "訴訟", "禁令", "斷供", "退出市場", "財報不實", "更正財報", "會計師保留意見",
    "董事長辭任", "總經理辭任", "財務長辭任",
]
PER_QUERY_LIMIT = 60


def _split_source(title):
    """Google News 標題格式『標題 - 媒體名』"""
    m = re.match(r"^(.*)\s+-\s+([^-]+)$", title)
    return (m.group(1).strip(), m.group(2).strip()) if m else (title.strip(), "")


def build(ctx):
    rows = ctx.conn.execute("SELECT stock_id, name FROM stock_info WHERE is_common=1").fetchall()
    name_to_id = news.build_name_map(rows)
    # 名稱長的先比對,避免短名稱吃掉長名稱的一部分
    names = sorted(name_to_id, key=len, reverse=True)
    pattern = re.compile("|".join(re.escape(n) for n in names)) if names else None

    first = {}  # sid -> (時間, 關鍵字, 標題, 媒體)
    fetched, failed = 0, 0
    for kw in NEGATIVE_KEYWORDS:
        items = news._fetch_rss(f"{kw} 公司", limit=PER_QUERY_LIMIT)
        if not items:
            failed += 1
        fetched += len(items)
        for it in items:
            if not it["title"] or not news._is_recent(it["pub_date"], news.LOOKBACK_HOURS):
                continue
            title, media = _split_source(it["title"])
            if kw not in title or pattern is None:
                continue
            try:
                t = parsedate_to_datetime(it["pub_date"])
            except Exception:  # noqa: BLE001
                t = datetime.now(timezone.utc)
            for nm in set(pattern.findall(title)):
                sid = name_to_id[nm]
                if sid not in first or t < first[sid][0]:
                    first[sid] = (t, kw, title, media)
        time.sleep(0.4)

    if fetched == 0:
        return {"s12": nodata("s12", TITLE, "今日無資料(Google News 沒有抓到任何新聞)")}
    lines, out = [], []
    for i, (sid, (t, kw, title, media)) in enumerate(sorted(first.items(), key=lambda kv: kv[1][0]), 1):
        lines.append(f"{i}. {ctx.label(sid)} {kw}〔{title}〕{media}")
        out.append({"sid": sid, "keyword": kw, "title": title, "media": media})
    if not lines:
        lines = ["今天沒有標題同時含負面關鍵字和上市櫃公司名稱的新聞"]
    else:
        lines.append("(標題含公司名不代表那家公司是當事人,例如「XX 控告 YY」兩家都會被抓到,只是提醒,請自己點進去看)")
    if failed:
        lines.append(f"(有 {failed} 個關鍵字的搜尋沒有結果或連線失敗)")
    return {"s12": result("s12", TITLE, lines, [], rows=out)}
