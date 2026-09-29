"""
feature3_news.py —— 功能3:AI 零組件供應鏈 + 總經新聞,簡短列出利多利空

流程:
  1. 用 Google News RSS 依關鍵字掃過 AI 供應鏈各環節 + 總經,收集最近 LOOKBACK_HOURS
     小時內的新聞標題當「候選清單」。這一步只是廣泛掃描,標題本身不夠深入。
  2. 把候選清單 + 昨天的簡報內容(避免重複)一起交給 Claude,開啟 web_search 工具讓它
     查證重要新聞的細節,只挑出真正重要的幾則,每則寫「新聞是什麼 + 利多還是利空 +
     受影響的公司」三行,不做長篇分析(使用者明確要求簡短,不要長篇深度報告)。
  3. 今天的簡報存進 scanner.db 的 news_briefings 表,給明天的執行拿來對照、避免
     炒同一則新聞的冷飯。

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
    "晶片設計": "GPU OR ASIC OR IC設計 OR 矽智財 OR 輝達 OR AMD OR 客製化晶片",
    "晶圓製造": "先進製程 OR 成熟製程 OR 矽晶圓 OR 半導體設備 OR 特用化學品 OR 晶圓代工產能",
    "記憶體": "HBM記憶體 OR DRAM OR NAND快閃記憶體 OR 企業級SSD OR 記憶體報價",
    "先進封裝測試": "先進封裝 OR CoWoS OR SoIC OR FOPLP OR 探針卡 OR 測試介面 OR 半導體測試設備",
    "載板與PCB": "ABF載板 OR BT載板 OR 高多層板 OR HDI板 OR 銅箔基板 OR 玻纖布 OR 電解銅箔",
    "光通訊與網通": "光模組 OR CPO OR 矽光子 OR 雷射磊晶 OR 資料中心交換器 OR 高速傳輸IC",
    "被動與頻率元件": "MLCC OR 積層陶瓷電容 OR 電感 OR 石英元件",
    "伺服器與機櫃": "伺服器ODM OR AI伺服器機殼 OR 伺服器滑軌 OR 伺服器連接器 OR 伺服器線材",
    "電源與散熱": "電源供應器 OR BBU OR 800V高壓直流 OR 液冷散熱 OR 水冷板 OR CDU散熱 OR 散熱模組",
    "資料中心基建": "資料中心電力設備 OR 變壓器 OR 重電產業 OR 資料中心建置",
    "總經": "Fed利率決策 OR 美債殖利率 OR 台幣匯率 OR 美國通膨 OR 關稅 OR 出口管制 OR hyperscaler資本支出",
}

MAX_ITEMS_PER_QUERY = 15
MAX_CANDIDATES = 100
LOOKBACK_HOURS = 26   # 比 24 小時多一點緩衝,避免排程延遲漏掉

SYSTEM_PROMPT = """\
你是台股 AI 零組件供應鏈的產業分析師,幫使用者整理每日新聞。使用者明確要求「簡短」,\
只要新聞本身和利多利空影響,不要長篇分析、不要十點式報告、不要學術用語。

# 關注範圍
AI 零組件供應鏈全範圍(晶片設計、晶圓製造、記憶體、先進封裝測試、載板/PCB、\
光通訊網通、被動元件、伺服器機櫃、電源散熱、資料中心基建)+ 會影響台股資金面或 \
AI 資本支出的總經新聞(Fed利率、美債殖利率、台幣匯率、關稅與出口管制、\
hyperscaler capex)。

# 篩選
從候選清單裡,只挑出「真的重要」的新聞,通常 5~10 則,依你的判斷,不用硬湊數量:
- 要選:會改變供需、價格、產能、訂單分配的新聞;公司的實質營運資訊(營收、法說\
指引、新客戶、擴產、報價調整)
- 不要選:指數漲跌流水帳、「市場觀望」這類空話、沒有數字沒有具體公司的新聞、\
超過3天且沒有新進展的舊聞、使用者已經知道的常識背景
- 不要重複昨天簡報寫過、且沒有新進展的事件(使用者會附上昨天的簡報全文給你參考)

每則新聞先判斷是利多還是利空(挑你認為影響較大的方向,不要中性、不要「利多利空\
並存」這種模糊判斷,一定要二選一)。

需要確認的細節(公司名稱、數字)可以用 web_search 查一下,但不用每則都查、不用附\
來源連結、不用寫信心標示,這些都會讓內容變長,使用者不需要。

# 輸出格式(條列式,分兩大類,不要按新聞逐則寫)
【利多】
- [產業或公司名稱]:[一行講新聞內容 + 為什麼是利多]
- [產業或公司名稱]:[一行講新聞內容 + 為什麼是利多]
...

【利空】
- [產業或公司名稱]:[一行講新聞內容 + 為什麼是利空]
- [產業或公司名稱]:[一行講新聞內容 + 為什麼是利空]
...

每個項目開頭寫受影響的產業別或公司名稱(例如「記憶體」「CoWoS先進封裝」\
「散熱液冷」「台積電」),後面接一行講清楚新聞內容和影響邏輯,不要分行、不要長篇。
只輸出這兩個【】區塊,不要加其他段落、結論、總經要點或待觀察事件清單。如果某一類\
完全沒有東西可寫,那個標題底下寫「無」就好,不要硬湊。如果候選清單裡完全沒有值得\
寫的新聞,直接寫「今天沒有重大新進展」。"""


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


def _get_previous_briefing(conn, report_date):
    """讀取「早於 report_date」最近一天的簡報內容,找不到(或沒有 conn,例如單獨測試
    功能3 時)就回傳 None。"""
    if conn is None:
        return None
    row = conn.execute(
        "SELECT date, content FROM news_briefings WHERE date < ? ORDER BY date DESC LIMIT 1",
        (report_date,),
    ).fetchone()
    return row if row else None


def _save_briefing(conn, report_date, content):
    if conn is None:
        return
    conn.execute(
        "INSERT OR REPLACE INTO news_briefings VALUES (?,?)", (report_date, content)
    )
    conn.commit()


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
        return ["【今日產業與總經新聞】\n(今天沒抓到相關新聞候選)"]

    news_list = "\n".join(
        f"- [{it['category']}] {it['title']} ({it['link']})" for it in deduped[:MAX_CANDIDATES]
    )

    prev = _get_previous_briefing(conn, report_date)
    if prev:
        prev_date, prev_content = prev
        prev_block = f"# 昨天({prev_date})的簡報全文,避免重複沒有新進展的事件:\n{prev_content}\n\n"
    else:
        prev_block = "# 沒有昨天的簡報紀錄(可能是第一次執行或跳過了一天)。\n\n"

    user_prompt = (
        f"{prev_block}"
        f"# 今天({report_date})的候選新聞標題清單(共 {len(deduped)} 則,Google News RSS "
        f"廣泛掃描,標題可能不夠深入,需要你自己 web_search 查證):\n{news_list}\n\n"
        "請照 system prompt 的規則,產出今天的簡報。"
    )

    print("  [功能3] 呼叫 Claude(web_search)分析中…")
    try:
        analysis = call_claude(
            user_prompt, system=SYSTEM_PROMPT, web_search=True, max_searches=8, max_tokens=4000
        )
    except Exception as e:
        return [f"【今日產業與總經新聞】\n(呼叫 AI 分析失敗: {e})"]

    _save_briefing(conn, report_date, analysis)

    disclaimer = "\n\n(AI 自動生成,公司名稱與數字請自行覆核,非投資建議)"
    return [f"{analysis}{disclaimer}"]
