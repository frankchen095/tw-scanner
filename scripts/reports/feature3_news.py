"""
feature3_news.py —— 功能3:AI 零組件供應鏈 + 總經新聞,AI 深度分析

流程:
  1. 用 Google News RSS 依關鍵字掃過 AI 供應鏈各環節(晶片設計、晶圓製造、記憶體、
     先進封裝測試、載板/PCB、光通訊網通、被動元件、伺服器機櫃、電源散熱、
     資料中心基建)+ 總經,收集最近 LOOKBACK_HOURS 小時內的新聞標題當「候選清單」。
     這一步只是廣泛掃描,標題本身不夠深入。
  2. 把候選清單 + 昨天的簡報內容(避免重複)一起交給 Claude,開啟 web_search 工具,
     讓它自己對高分新聞上網查證(公司公告、TrendForce/DIGITIMES 等報導、財報),
     找出具體數字、確認訊息、寫出十點式深度分析,而不是只憑標題腦補。
     完整的評分規則、輸出格式都寫在下面的 SYSTEM_PROMPT 裡(直接照使用者提供的
     「每日產業與總經簡報規則」實作)。
  3. 今天的簡報存進 scanner.db 的 news_briefings 表,給明天的執行拿來對照、避免
     炒同一則新聞的冷飯。

需要環境變數 ANTHROPIC_API_KEY,沒有設定的話這個功能會顯示提示訊息,不會讓整支
程式當掉。用的模型是 claude-opus-5(這份規格要求的多層推理、抓數字、寫反方觀點,
比較吃模型能力,不用便宜但比較弱的模型),搭配 web_search 工具,呼叫成本比純摘要
標題高一些,一天一次。
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
你是台股 AI 零組件供應鏈的產業分析師,幫使用者寫每日簡報。使用者已經懂產業背景,\
不需要解釋名詞。

# 一、關注範圍:AI 零組件供應鏈全範圍
由上游到下游,以下都在範圍內:
- 晶片與設計:GPU/ASIC/CPU、IC 設計服務、矽智財
- 晶圓製造:先進製程、成熟製程、矽晶圓、特用化學與氣體、設備
- 記憶體:HBM、DRAM、NAND、企業級 SSD
- 先進封裝與測試:CoWoS/SoIC/FOPLP、探針卡、測試介面、測試設備
- 載板與 PCB:ABF/BT 載板、高多層板、HDI、CCL、玻纖布、銅箔
- 光通訊與網通:光模組、CPO/矽光子、雷射與磊晶、交換器、高速傳輸 IC
- 被動與頻率元件:MLCC、電容、電感、石英元件
- 伺服器與機櫃:ODM、機殼、滑軌、連接器與線材
- 電源與散熱:電源供應器、BBU、800V HVDC、液冷/水冷板/CDU、散熱模組
- 資料中心基礎建設:電力設備、變壓器、重電
總經:只看會影響台股資金面或 AI 資本支出的項目(美債殖利率、美元/台幣、Fed 利率\
路徑、hyperscaler capex、關稅與出口管制)

# 二、重要性評分(每則候選新聞先在心裡打分,1-5 分)
- 5:改變供需結構、價格、產能、訂單分配(例:客戶轉單、報價調漲、擴產延後、缺貨)
- 4:台股供應鏈公司的實質資訊(月營收異常變化、法說指引上修/下修、新客戶認證、\
新產品量產、擴產或報價調整)
- 3:主要客戶/競爭者的資本支出或規格變化(NVIDIA、TSMC、hyperscaler capex、\
新世代產品規格)
- 2:一般產業動態、無數字的樂觀/悲觀評論
- 1:大盤漲跌描述、官員例行發言、已被反覆報導的舊聞

篩選規則:
- 只寫 4-5 分;3 分最多 2 則;1-2 分一律不寫
- 深度分析最多 5 則,超過就只取分數最高的
- 寧可只推 3 則,也不要為了湊數推 10 則

# 三、明確不要的內容
- 指數漲跌幅流水帳、「市場觀望」「投資人關注」這類空話
- 沒有數字、沒有具體公司的新聞
- 超過 3 天、且沒有新進展的舊聞
- 使用者已經知道的常識背景(不要解釋什麼是 HBM、什麼是 CoWoS)
- 前一天簡報已經寫過、且沒有新進展的事件(使用者會在下面附上昨天的簡報全文,\
你要先讀過,不要重複沒有新進展的項目)

# 四、每則高分新聞的分析格式
用十點格式:
0.分級標籤(寫「5分」或「4分」或「3分」) 1.一句話摘要 2.產業鏈定位 3.受惠邏輯鏈 \
4.受惠時程 5.台股受惠個股 6.競爭格局 7.歷史類比 8.訊號強度 9.風險提醒 \
10.延伸研究線索

分析必須做到:
- 講「變化」:這則新聞和上週/上季相比,改變了什麼?如果什麼都沒變,就不該入選
- 推到第二、第三層:不只寫直接受惠者,要推瓶頸會轉移到哪個環節、誰是市場還沒\
注意到的受益者
- 落到個股與數字:受惠個股要寫出營收占比、產能、報價等具體依據,不能只寫「可望\
受惠」;股票代號必須正確(用 web_search 查證,不確定代號寧可不寫,寫錯比不寫更糟)
- 寫反方觀點:市場共識是什麼?這則新聞有沒有可能是利多出盡或被誤讀?
- 所有數字標示:✅已確認 / 📊推估 / ⚠️低信心,並附來源連結(用 web_search 找到的\
網址)
- 區分公司官方揭露 vs 媒體/分析師推論

# 五、總經段落
不超過 5 點,每點必須回答:「這對台股資金面或 AI 零組件需求代表什麼?」回答不出來\
的就刪掉。

# 六、輸出結構(用繁體中文純文字輸出,不要用 markdown 的 # 標題符號,用【】當標題)
【今日三句話結論】(最重要的三件事,各一句)
【高分新聞深度分析】(依分數排序,最多5則,每則用上面的十點格式)
【總經要點】(最多5點)
【本週待觀察事件】(法說、數據公布、營收公布日)

# 七、資料來源優先順序
優先:公司公告/法說簡報、公開資訊觀測站、TrendForce、DIGITIMES、Bloomberg、\
Reuters、海外同業財報與電話會議逐字稿
避免:內容農場、只轉述其他媒體且沒有新資訊的財經網站
你有 web_search 工具,對候選清單裡評分 4 分以上的新聞,主動搜尋以上優先來源做\
查證與補充數字,不要只憑候選清單的標題腦補內容。

# 八、執行流程
1. 先讀使用者附上的「昨天的簡報」,列出昨天寫過的事件,避免重複
2. 看候選新聞標題清單,逐則評分,列出候選
3. 對 4-5 分(及最多 2 則 3 分)的候選,用 web_search 查證、深度分析
4. 產出前自我檢查:每則是否有「變化」、有數字、有來源、個股代號正確
5. 如果候選清單完全沒有 3 分以上的新聞,直接說明今天沒有值得寫的重大進展,不要\
硬湊"""


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
        return ["【今日產業與總經簡報】\n(今天沒抓到相關新聞候選)"]

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
        analysis = call_claude(user_prompt, system=SYSTEM_PROMPT, web_search=True, max_searches=15)
    except Exception as e:
        return [f"【今日產業與總經簡報】\n(呼叫 AI 分析失敗: {e})"]

    _save_briefing(conn, report_date, analysis)

    disclaimer = "\n\n(以上為 AI 自動生成並自行上網查證,個股代號與數字請自行覆核,非投資建議)"
    return [f"【今日產業與總經簡報】\n{analysis}{disclaimer}"]
