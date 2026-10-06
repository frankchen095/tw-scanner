"""
feature3_news.py —— 策略六:時事分析(AI 零組件供應鏈 + 總經)

流程:
  1. 用 Google News RSS 依關鍵字掃過 AI 供應鏈各環節 + 總經,收集最近 LOOKBACK_HOURS
     小時內的新聞標題當「候選清單」(只是起點,標題本身不夠深入)。
  2. 把候選清單 + 昨天的簡報(避免重複)交給 Claude,開啟 web_search 讓它查證,並主動搜尋
     論壇與社群(PTT、Dcard、Threads、X…)當天的熱門消息。論壇/社群/匿名消息沒有公司公告或
     主流媒體證實時可以列,但必須標〔未證實〕並附出處。
  3. 輸出格式固定:『利多/利空:產業 → 個股 → 原因』。
  4. AI 寫的個股名稱用資料庫的股票名單核對:對得上的補上正確代號,對不上的標「(?)」,
     不相信 AI 自己寫的代號(它會編)。
  5. 今天的簡報存進 scanner.db 的 news_briefings 表,給明天對照、避免炒冷飯。

需要環境變數 ANTHROPIC_API_KEY,沒有設定的話這個策略會顯示提示訊息,不會讓整支程式當掉。
"""

import re
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import call_claude  # noqa: E402

TITLE = "【策略六:時事分析】AI零組件供應鏈+總經"

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
你是台股 AI 零組件供應鏈的產業分析師,幫使用者整理每日時事。使用者要求簡短:只要『哪個產業\
和個股受影響、利多還是利空、原因』,不要長篇分析、不要學術用語。

# 關注範圍
AI 零組件供應鏈全範圍(晶片設計、晶圓製造、記憶體、先進封裝測試、載板/PCB、\
光通訊網通、被動元件、伺服器機櫃、電源散熱、資料中心基建)+ 會影響台股資金面或 \
AI 資本支出的總經新聞(Fed利率、美債殖利率、台幣匯率、關稅與出口管制、\
hyperscaler capex)。

# 資料來源
- 使用者附上的候選清單是 Google News 的標題,只是起點。
- 請用 web_search 查證重要消息的細節,並主動搜尋論壇與社群(例如 PTT Stock 版、Dcard、\
Threads、X、投資社群的轉載)裡當天討論度高的產業消息。
- 來自論壇、社群、匿名爆料或轉載的消息,如果沒有公司公告或主流媒體證實,仍然可以列,\
但一定要在原因的最後標〔未證實〕,並附出處(網站名稱 + 網址)。已有公司公告或主流媒體\
證實的消息不用附出處。
- 不確定的數字不要寫,不要編造公司、數字或網址。

# 篩選
只挑「真的重要」的消息,通常 5~10 個區塊,不用硬湊數量:
- 要選:會改變供需、價格、產能、訂單分配的消息;公司的實質營運資訊(營收、法說指引、\
新客戶、擴產、報價調整)
- 不要選:指數漲跌流水帳、「市場觀望」這類空話、沒有數字沒有具體公司的消息、\
超過3天且沒有新進展的舊聞
- 不要重複昨天簡報寫過、且沒有新進展的事件(使用者會附上昨天的簡報全文給你參考)
每個區塊一定要二選一:利多或利空,不要中性、不要「利多利空並存」。

# 輸出格式
每個區塊固定三行,區塊之間空一行:

利多:XX產業
個股:個股A、個股B
原因:一兩句話講清楚消息內容和影響邏輯

利空:XX產業
個股:個股A、個股B
原因:一兩句話講清楚消息內容和影響邏輯

規則:
- 「XX產業」寫產業或主題,例如「CoWoS先進封裝」「記憶體」「散熱液冷」「總經:Fed利率」。
- 「個股」只寫台灣上市櫃公司的中文簡稱,用頓號分隔;不要寫股票代號(代號由程式補上);\
不要寫外國公司(外國公司寫在原因裡)。總經類沒有特定個股就寫「無」。
- 利多區塊排前面,利空區塊排後面。
- 只輸出這些區塊,不要加標題、前言、結論、待觀察清單。如果完全沒有值得寫的消息,\
只輸出「今天沒有重大新進展」。"""


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
    時)就回傳 None。"""
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


def link_stocks(text, name_to_id):
    """把『個股:』那一行的公司名稱用股票名單核對:對得上補正確代號,對不上標 (?)。"""

    def fix_line(m):
        body = m.group(2).strip()
        if not body or body in ("無", "无"):
            return m.group(0)
        out = []
        for tok in re.split(r"[、,，]", body):
            name = re.sub(r"[（(].*?[）)]", "", tok).strip()
            if not name:
                continue
            sid = name_to_id.get(name)
            out.append(f"{name}({sid})" if sid else f"{name}(?)")
        return f"{m.group(1)}{'、'.join(out)}"

    return re.sub(r"^(個股[:：]\s*)(.*)$", fix_line, text, flags=re.MULTILINE)


def build(conn, report_date):
    all_items = []
    for label, query in QUERIES.items():
        print(f"  [策略六] 抓「{label}」新聞…")
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
        return [f"{TITLE}\n(今天沒抓到相關新聞候選)"]

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
        "請照 system prompt 的規則,產出今天的時事分析。"
    )

    print("  [策略六] 呼叫 Claude(web_search)分析中…")
    try:
        analysis = call_claude(
            user_prompt, system=SYSTEM_PROMPT, web_search=True, max_searches=10, max_tokens=6000
        )
    except Exception as e:  # noqa: BLE001
        return [f"{TITLE}\n(呼叫 AI 分析失敗: {e})"]

    if conn is not None:
        rows = conn.execute("SELECT stock_id, name FROM stock_info WHERE is_common=1").fetchall()
        analysis = link_stocks(analysis, {name: sid for sid, name in rows})

    _save_briefing(conn, report_date, analysis)

    disclaimer = "(AI 自動生成;個股後面標(?)代表名稱對不到上市櫃股票名單;〔未證實〕為論壇/社群消息;非投資建議)"
    return [f"{TITLE}\n{analysis}\n\n{disclaimer}"]
