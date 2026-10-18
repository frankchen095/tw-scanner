"""
feature3_news.py —— 策略六:時事分析(固定三段:供需訊號 / 總經大事 / 財報展望解讀)

流程:
  1. 候選新聞來自多個來源(見 scripts/news_sources.py),每則標來源:
       [GoogleNews] Google News RSS 依關鍵字掃 AI 供應鏈各環節 + 供需 + 地緣 + 財報展望
       [金十]       金十數據 7×24 快訊(簡體,關鍵字過濾)
       [WSJ]        華爾街日報公開 RSS(英文標題 + 一句摘要,關鍵字過濾)
       [富途]       富途牛牛快訊(跟金十標題去重,只留金十)
     每個來源都能單獨失敗:抓不到就跳過,最後註明「本日 XX 來源未取得」。
  2. 把候選清單 + 昨天的簡報(避免重複)交給 Claude,開啟 web_search 讓它查證。
  3. 輸出固定三段(◆ 供需訊號 / ◆ 總經大事 / ◆ 財報／展望解讀),段內是『利多/利空 → 個股 → 原因』區塊;
     沒東西的段只寫「無」,不硬湊。
  4. AI 寫的個股名稱用資料庫的股票名單核對:對得上的補上正確代號,對不上的標「(?)」。
  5. 今天的簡報存進 scanner.db 的 news_briefings 表,給明天對照、避免炒冷飯。
  6. 有金十財經日曆時,最後加一行「明日重要事件:…」。

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
import news_sources  # noqa: E402
from common import call_claude  # noqa: E402

TITLE = "【策略六:時事分析】供需訊號 / 總經大事 / 財報展望"

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
    "供需": "交期 OR 漲價 OR 缺貨 OR 缺料 OR 稼動率 OR 庫存天數 OR 長約 OR 預付款 OR 產能滿載",
    "地緣": "中東 OR 俄烏 OR 荷莫茲 OR 伊朗 OR 台海 OR 油價 OR 制裁",
    "財報展望": "蘋果財報 OR NVIDIA財報 OR 輝達財報 OR Micron財報 OR 美光財報 OR 台積電法說 OR 鴻海法說 OR 資本支出 OR capex OR 財測",
}

MAX_ITEMS_PER_QUERY = 15
MAX_CANDIDATES = sum(news_sources.LIMITS.values())  # Google 60 + 金十 40 + WSJ 20 + 富途 20 = 140
MAX_SEARCHES = 15
LOOKBACK_HOURS = 26   # 比 24 小時多一點緩衝,避免排程延遲漏掉
SECTIONS = ("供需訊號", "總經大事", "財報／展望解讀")

SYSTEM_PROMPT = """\
你是台股 AI 零組件供應鏈的產業分析師,幫使用者整理每日時事。使用者要求簡短、固定格式:\
每天固定問三類問題,沒變化的類別明確寫「無」,不要硬湊。

# 資料來源
- 使用者附上的候選清單每一則前面有來源標記:[GoogleNews]、[金十](簡體中文快訊)、[WSJ](英文標題)、\
[富途]。候選只是起點,請用 web_search 查證重要消息的細節。
- 也可以主動搜尋論壇與社群(PTT Stock 版、Dcard、Threads、X、投資社群轉載)當天討論度高的產業消息。\
來自論壇、社群、匿名爆料或轉載、沒有公司公告或主流媒體證實的消息,可以列,但一定要在原因最後標〔未證實〕,\
並附出處(網站名稱 + 網址)。已有公司公告或主流媒體證實的不用附出處。
- 不確定的數字不要寫,不要編造公司、數字或網址。
- 每個區塊的「原因」最後要標這則消息的來源標記,讓使用者能對回候選清單:用候選清單裡的標記 〔金十〕〔WSJ〕〔GoogleNews〕〔富途〕;如果是你自己 web_search 找到的、不在候選清單裡,標〔搜尋〕。同一個區塊用了多個來源就都標。

# 三段內容(順序固定,三段都要出現)

◆ 供需訊號
只收這些類型:交期拉長/縮短、漲價/降價、產能滿載/稼動率下滑、缺料/缺貨、庫存天數異常、長約/預付款。\
每則一定要有「什麼產品 → 變緊還是變鬆」的明確判斷,並寫出證據(誰說的、什麼數字)。

◆ 總經大事
只收:戰爭/地緣(含中東、俄烏、台海、荷莫茲海峽)、油價、Fed 與各國央行、關稅與出口管制、匯率、\
美債殖利率。每則要寫「對台股哪個族群、利多還是利空」。

◆ 財報／展望解讀
只收供應鏈龍頭的財報與法說指引:蘋果、NVIDIA、AMD、Micron、SK Hynix、三星、台積電、鴻海、廣達、\
緯穎、Hyperscaler(Microsoft/Google/Amazon/Meta/Oracle)的 capex。只摘「對台廠的指引」\
(例如:蘋果財報 iPhone 出貨優於預期 → 哪些台廠拉貨),不要重述財報數字。

# 篩選
- 只挑真的重要的消息,不用硬湊數量;某一段沒有值得寫的,就只寫該段標題那一行「◆ 段名」,下一行寫「無」。
- 不要選:指數漲跌流水帳、「市場觀望」這類空話、沒有具體公司/產品的消息、超過3天且沒有新進展的舊聞。
- 不要重複昨天簡報寫過、且沒有新進展的事件(使用者會附上昨天的簡報全文)。
- 每個區塊一定要二選一:利多或利空,不要中性、不要「利多利空並存」。

# 輸出格式
段內每個區塊固定三行,區塊之間空一行,段與段之間也空一行:

◆ 供需訊號
利多:XX產業
個股:個股A、個股B
原因:什麼產品、變緊還是變鬆、證據是什麼

◆ 總經大事
利空:總經:XX
個股:無(或受影響族群的代表個股)
原因:對台股哪個族群、方向

◆ 財報／展望解讀
利多:XX產業
個股:個股A、個股B
原因:對台廠的指引(哪些台廠拉貨/砍單)

規則:
- 「個股」只寫台灣上市櫃公司的中文簡稱,用頓號分隔;不要寫股票代號(代號由程式補上);不要寫外國公司\
(外國公司寫在原因裡)。總經類沒有特定個股就寫「無」。
- 每段內利多區塊排前面,利空區塊排後面。
- 只輸出這三段,不要加標題、前言、結論、待觀察清單。
- 三段都沒有值得寫的消息時,只輸出「今天沒有重大新進展」。"""


def _fetch_rss(query, limit=MAX_ITEMS_PER_QUERY):
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
    for item in root.findall(".//item")[:limit]:
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


def build_name_map(rows):
    """rows = [(stock_id, name)]。除了完整名稱,也收『去掉結尾 * / -創 / -KY』的簡稱
    (例如 國巨* → 國巨、世芯-KY → 世芯),但簡稱在名單裡必須是唯一的才用。"""
    m = {name: sid for sid, name in rows}
    alias = {}
    for sid, name in rows:
        a = re.sub(r"(\*|-創|-KY)$", "", name)
        if a != name:
            alias.setdefault(a, set()).add(sid)
    for a, ids in alias.items():
        if a not in m and len(ids) == 1:
            m[a] = next(iter(ids))
    return m


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


def trim_preamble(analysis):
    """Claude 搜尋前常會先講一句話,被黏在第一個區塊前面。新格式:從第一個 ◆ 開始;
    沒有任何 ◆(沒照格式輸出)就退回舊的『第一個 利多/利空』判斷,都找不到就原樣保留,不讓整段消失。"""
    i = analysis.find("◆")
    if i >= 0:
        return analysis[i:] if i > 0 else analysis
    m = re.search(r"(利多|利空)[:：]", analysis)
    if m and m.start() > 0:
        return analysis[m.start():]
    return analysis


def collect_candidates(conn, report_date, errors):
    """回傳 (候選清單 list, 各來源統計 dict, 金十明日事件 list 或 None)"""
    # Google News
    google = []
    for label, query in QUERIES.items():
        print(f"  [策略六] 抓「{label}」新聞…")
        for it in _fetch_rss(query):
            if it["title"] and _is_recent(it["pub_date"], LOOKBACK_HOURS):
                google.append({"source": "GoogleNews", "title": it["title"], "summary": "",
                               "time": it["pub_date"], "link": it["link"], "category": label})
        time.sleep(0.5)
    seen, g2 = set(), []
    for it in google:
        if it["title"] not in seen:
            seen.add(it["title"])
            g2.append(it)
    if not g2:
        errors.append("Google News 本日沒有取得")

    jin, jin_total = news_sources.fetch_jin10(LOOKBACK_HOURS, errors)
    wsj, wsj_total = news_sources.fetch_wsj(LOOKBACK_HOURS, errors)
    futu, futu_total = news_sources.fetch_futu(LOOKBACK_HOURS, errors)
    futu_dedup = news_sources.dedup_futu_against_jin10(futu, jin)
    lim = news_sources.LIMITS
    items = g2[: lim["GoogleNews"]] + jin[: lim["金十"]] + wsj[: lim["WSJ"]] + futu_dedup[: lim["富途"]]
    items = news_sources.save_candidates(conn, report_date, items)
    used = {k: sum(1 for it in items if it["source"] == k) for k in lim}
    # (來源抓到的則數 → 關鍵字過濾後 → 實際送給 AI 的則數)
    stats = {
        "GoogleNews": (len(google), len(g2), used["GoogleNews"]),
        "金十": (jin_total, len(jin), used["金十"]),
        "WSJ": (wsj_total, len(wsj), used["WSJ"]),
        "富途": (futu_total, len(futu_dedup), used["富途"]),
    }
    tomorrow = news_sources.fetch_jin10_calendar(errors) if news_sources.JIN10_CALENDAR else None
    return items, stats, tomorrow


def format_candidates(items):
    lines = []
    for it in items:
        cat = f"[{it['category']}]" if it.get("category") else ""
        extra = f" — {it['summary']}" if it.get("summary") else ""
        lines.append(f"- [{it['source']}]{cat} {it['title']}{extra} ({it['link']})")
    return "\n".join(lines)


def build(conn, report_date):
    errors = []
    items, stats, tomorrow = collect_candidates(conn, report_date, errors)
    print("  [策略六] 各來源(抓到 → 關鍵字過濾後 → 送給 AI):" + "、".join(f"{k} {a}→{b}→{c}" for k, (a, b, c) in stats.items()))

    if not items:
        note = ("(" + ";".join(errors) + ")") if errors else ""
        return [f"{TITLE}\n(今天沒抓到相關新聞候選){note}"]

    prev = _get_previous_briefing(conn, report_date)
    if prev:
        prev_date, prev_content = prev
        prev_block = f"# 昨天({prev_date})的簡報全文,避免重複沒有新進展的事件:\n{prev_content}\n\n"
    else:
        prev_block = "# 沒有昨天的簡報紀錄(可能是第一次執行或跳過了一天)。\n\n"

    user_prompt = (
        f"{prev_block}"
        f"# 今天({report_date})的候選新聞標題清單(共 {len(items)} 則,來源標記在每則最前面,"
        f"標題可能不夠深入,需要你自己 web_search 查證):\n{format_candidates(items[:MAX_CANDIDATES])}\n\n"
        "請照 system prompt 的規則,產出今天的時事分析(三段固定)。"
    )

    print("  [策略六] 呼叫 Claude(web_search)分析中…")
    try:
        analysis = call_claude(
            user_prompt, system=SYSTEM_PROMPT, web_search=True, max_searches=MAX_SEARCHES, max_tokens=6000
        )
    except Exception as e:  # noqa: BLE001
        return [f"{TITLE}\n(呼叫 AI 分析失敗: {e})"]

    analysis = trim_preamble(analysis)

    if conn is not None:
        rows = conn.execute("SELECT stock_id, name FROM stock_info WHERE is_common=1").fetchall()
        analysis = link_stocks(analysis, build_name_map(rows))

    _save_briefing(conn, report_date, analysis)

    tail = []
    if tomorrow:
        tail.append("明日重要事件:" + ";".join(tomorrow))
    src = "、".join(f"{k} {c}則" for k, (_, _, c) in stats.items())
    tail.append(f"(候選來源:{src})")
    if errors:
        tail.append("(本日來源問題:" + ";".join(errors) + ")")
    tail.append("(AI 自動生成;個股後面標(?)代表名稱對不到上市櫃股票名單;〔未證實〕為論壇/社群消息;非投資建議)")
    return [f"{TITLE}\n{analysis}\n\n" + "\n".join(tail)]
