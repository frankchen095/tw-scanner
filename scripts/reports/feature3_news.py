"""
feature3_news.py —— 功能3:整理當日總經/電子業新聞,用 AI 分析利多利空

新聞來源:Google News RSS(用關鍵字搜尋,涵蓋各家媒體的中文報導,不限單一網站,
不用對付 Bloomberg/CNN 這類付費牆)。只保留最近 LOOKBACK_HOURS 小時內發布的新聞,
交給 Claude 分析出對台股影響最大的幾則、利多還是利空、受影響的公司。

需要環境變數 ANTHROPIC_API_KEY,沒有設定的話這個功能會顯示提示訊息,不會讓整支
程式當掉。
"""

import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import call_claude  # noqa: E402

QUERIES = {
    "總經": "Fed OR 升息 OR 降息 OR 通膨 OR 美國經濟 OR 地緣政治 OR 台幣匯率",
    "電子業": "半導體 OR AI伺服器 OR 台積電 OR 記憶體 OR PCB OR 蘋果供應鏈 OR 輝達",
}

MAX_ITEMS_PER_QUERY = 15
LOOKBACK_HOURS = 20


def _fetch_rss(query):
    url = (
        "https://news.google.com/rss/search?q="
        + urllib.parse.quote(query)
        + "&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
    )
    try:
        resp = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    except requests.RequestException as e:
        print(f"    新聞來源連線失敗: {e}")
        return []
    if resp.status_code != 200:
        print(f"    新聞來源回應異常: {resp.status_code}")
        return []
    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError:
        return []
    items = []
    for item in root.findall(".//item")[:MAX_ITEMS_PER_QUERY]:
        items.append(
            {
                "title": item.findtext("title", "") or "",
                "pub_date": item.findtext("pubDate", "") or "",
                "link": item.findtext("link", "") or "",
            }
        )
    return items


def _is_recent(pub_date_str, hours):
    try:
        cleaned = pub_date_str.replace("GMT", "").strip()
        dt = datetime.strptime(cleaned, "%a, %d %b %Y %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return True  # 解析失敗就不排除,交給後面的AI自己判斷
    return (datetime.now(timezone.utc) - dt) <= timedelta(hours=hours)


def build(conn, report_date):
    all_items = []
    for label, query in QUERIES.items():
        print(f"  [功能3] 抓「{label}」新聞…")
        items = _fetch_rss(query)
        for it in items:
            it["category"] = label
        all_items.extend(items)
        time.sleep(0.5)

    recent = [it for it in all_items if it["title"] and _is_recent(it["pub_date"], LOOKBACK_HOURS)]

    seen = set()
    deduped = []
    for it in recent:
        if it["title"] not in seen:
            seen.add(it["title"])
            deduped.append(it)

    if not deduped:
        return ["【今日總經/電子業新聞分析】\n(今天沒抓到相關新聞)"]

    news_list = "\n".join(f"- [{it['category']}] {it['title']}" for it in deduped[:40])

    prompt = (
        f"你是台股分析師。以下是今天({report_date})的總經和電子業新聞標題列表:\n\n"
        f"{news_list}\n\n"
        "請從中挑出對台股影響最大的3~5則新聞,用條列式寫出:\n"
        "1. 新聞重點(一句話)\n"
        "2. 判斷是利多還是利空\n"
        "3. 可能受影響的台灣上市公司(寫股票名稱,知道代號更好)\n\n"
        "請用繁體中文,精簡扼要,每則不超過3行,不需要開場白或結語。"
    )

    print("  [功能3] 呼叫 Claude 分析中…")
    try:
        analysis = call_claude(prompt)
    except Exception as e:
        return [f"【今日總經/電子業新聞分析】\n(呼叫 AI 分析失敗: {e})"]

    return [f"【今日總經/電子業新聞分析】\n{analysis}"]
