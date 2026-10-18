"""
news_sources.py —— 策略六的新聞候選來源(只抓標題和摘要,不抓全文、不繞付費牆、不用 cookie 登入)

每個來源一個 fetcher,統一回傳 [{"source","title","summary","time","link"}];
任何一個來源失敗都只會回傳空清單並把原因記在 errors,不會讓策略六整段掛掉。

來源(2026-10-18 實測):
  Google News RSS    公開 RSS,依關鍵字搜尋(在 feature3_news.py 裡)
  金十數據快訊       https://flash-api.jin10.com/get_flash_list?channel=-8200&vip=1
                     這是金十網頁版快訊頁自己載入資料用的請求,需要網頁自己帶的公開 header
                     (x-app-id / x-version,沒帶會回 502);不是官方開放 API,沒有 SLA,隨時可能改。
                     內容是簡體中文;VIP 專屬快訊 content 是空的(鎖住),跳過。
  金十財經日曆       https://cdn-rili.jin10.com/web_data/<年>/week/<週>/economics.json(網頁自己用的)
  華爾街日報 RSS     https://feeds.content.dowjones.io/public/rss/* (公開 RSS,只有標題+一句摘要)
  富途牛牛快訊       https://news.futunn.com/news-site-api/main/get-flash-list(網頁自己用的 JSON)
                     內容大多轉載金十等來源;跟金十標題去重,重複只留金十
"""

import html
import re
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
TAIPEI = timezone(timedelta(hours=8))

# 各來源候選上限(避免某個來源洗版)
LIMITS = {"GoogleNews": 60, "金十": 40, "WSJ": 20, "富途": 20}

# ---- 關鍵字(放在最上面,方便以後改)----
# 金十/富途(簡體)只留跟台股有關的;同時放簡繁,因為金十是簡體、其他地方可能是繁體
JIN10_KEYWORDS = [
    "Fed", "美联储", "央行", "利率", "降息", "加息", "关税", "出口管制", "芯片", "晶片", "半导体", "AI", "人工智能",
    "台湾", "台海", "中东", "俄罗斯", "伊朗", "霍尔木兹", "荷莫兹", "原油", "油价", "汇率", "美债", "收益率",
    "苹果", "英伟达", "台积电", "三星", "SK海力士", "海力士", "美光", "微软", "谷歌", "亚马逊", "Meta", "甲骨文",
    "存储", "DRAM", "HBM", "CoWoS", "资本开支", "财报", "指引", "制裁",
]
WSJ_KEYWORDS = [
    "chip", "semiconductor", "AI", "Nvidia", "Apple", "TSMC", "Taiwan", "tariff", "Fed", "China", "Micron",
    "data center", "Samsung", "earnings", "guidance", "oil", "Iran", "Russia", "Ukraine",
]
WSJ_FEEDS = {
    "Markets": "https://feeds.content.dowjones.io/public/rss/RSSMarketsMain",
    "Technology": "https://feeds.content.dowjones.io/public/rss/RSSWSJD",
    "Business": "https://feeds.content.dowjones.io/public/rss/WSJcomUSBusiness",
    "World": "https://feeds.content.dowjones.io/public/rss/RSSWorldNews",
}
# 富途:只抓這幾檔個股相關(relatedStocks 比對)+ 重要總經關鍵字(沿用金十的清單)
FUTU_STOCKS = ["AAPL", "NVDA", "TSM", "MU", "AMD"]

JIN10_MAX_PAGES = 40
FUTU_MAX_PAGES = 8
JIN10_HEADERS = dict(UA, **{"x-app-id": "bVBF4FyRTn5NJF5n", "x-version": "1.0.0",
                            "Origin": "https://www.jin10.com", "Referer": "https://www.jin10.com/"})


def _has_kw(text, kws):
    low = text.lower()
    return any(k.lower() in low for k in kws)


def _clean(s):
    s = re.sub(r"<[^>]+>", "", s or "")
    return html.unescape(s).strip()


def fetch_jin10(hours=26, errors=None):
    """金十快訊,關鍵字過濾後的 [{...}]。快訊只有『【標題】內文』一段,標題取【】內、摘要取內文前 160 字。"""
    errors = errors if errors is not None else []
    # 一頁只有約 20 則,用 max_time 往前翻頁,翻到超過回溯時間或 MAX_PAGES 為止
    rows, max_time = [], None
    cutoff0 = datetime.now(TAIPEI) - timedelta(hours=hours)
    for _ in range(JIN10_MAX_PAGES):
        params = {"channel": "-8200", "vip": "1"}
        if max_time:
            params["max_time"] = max_time
        try:
            r = requests.get("https://flash-api.jin10.com/get_flash_list", params=params,
                             headers=JIN10_HEADERS, timeout=25)
            r.raise_for_status()
            page = r.json().get("data") or []
        except Exception as e:  # noqa: BLE001
            errors.append(f"金十快訊:{str(e)[:80]}")
            break
        if not page:
            break
        rows += page
        max_time = page[-1].get("time")
        try:
            if datetime.strptime(max_time, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TAIPEI) < cutoff0:
                break
        except Exception:  # noqa: BLE001
            break
        time.sleep(0.2)
    if not rows:
        return [], 0
    out, total = [], 0
    cutoff = datetime.now(TAIPEI) - timedelta(hours=hours)
    for x in rows:
        d = x.get("data") or {}
        content = _clean(d.get("content"))
        if not content or x.get("type") not in (0, None) or d.get("lock"):
            continue
        total += 1
        try:
            t = datetime.strptime(x["time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=TAIPEI)
        except Exception:  # noqa: BLE001
            t = None
        if t and t < cutoff:
            continue
        m = re.match(r"^【(.+?)】(.*)$", content, re.S)
        title, summary = (m.group(1), m.group(2)) if m else (content[:40], content)
        if not _has_kw(title + summary, JIN10_KEYWORDS):
            continue
        out.append({"source": "金十", "title": title.strip(), "summary": summary.strip()[:160],
                    "time": x.get("time", ""), "link": "https://www.jin10.com/"})
    return out, total


def fetch_jin10_calendar(errors=None, days=2):
    """金十財經日曆:回傳明天(台灣時間)的重要經濟數據/事件文字列表;抓不到回傳 None。
    資料是網頁自己用的 economics.json(整週),欄位以實測為準,對不上就回傳 None 並記錄。"""
    errors = errors if errors is not None else []
    tomorrow = datetime.now(TAIPEI).date() + timedelta(days=1)
    y, w, _ = tomorrow.isocalendar()
    url = f"https://cdn-rili.jin10.com/web_data/{y}/week/{w}/economics.json"
    try:
        r = requests.get(url, headers=dict(UA, Referer="https://rili.jin10.com/"), timeout=25)
        r.raise_for_status()
        rows = r.json()
    except Exception as e:  # noqa: BLE001
        errors.append(f"金十財經日曆:{str(e)[:80]}")
        return None
    if isinstance(rows, dict):
        rows = rows.get("data") or []
    items = []
    for x in rows:
        try:
            if int(x.get("star", 0)) < 3:
                continue
            ts = x.get("pub_time") or x.get("time") or ""
            d = datetime.strptime(ts[:10], "%Y-%m-%d").date()
            if d != tomorrow:
                continue
            items.append(f"{ts[11:16]} {x.get('country', '')} {x.get('name', '')}")
        except Exception:  # noqa: BLE001
            continue
    return items


def _parse_rss(text):
    root = ET.fromstring(text)
    for it in root.findall(".//item"):
        yield (it.findtext("title", ""), it.findtext("description", ""), it.findtext("pubDate", ""),
               it.findtext("link", ""))


def fetch_wsj(hours=26, errors=None):
    errors = errors if errors is not None else []
    out, total = [], 0
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    seen = set()
    for name, url in WSJ_FEEDS.items():
        try:
            r = requests.get(url, headers=UA, timeout=25)
            r.raise_for_status()
            for title, desc, pub, link in _parse_rss(r.content):
                title = _clean(title)
                if not title or title in seen:
                    continue
                seen.add(title)
                total += 1
                try:
                    dt = parsedate_to_datetime(pub)
                    if dt < cutoff:
                        continue
                except Exception:  # noqa: BLE001
                    pass
                summary = _clean(desc)
                if not _has_kw(title + " " + summary, WSJ_KEYWORDS):
                    continue
                out.append({"source": "WSJ", "title": title, "summary": summary[:160], "time": pub, "link": link})
        except Exception as e:  # noqa: BLE001
            errors.append(f"WSJ RSS {name}:{str(e)[:60]}")
        time.sleep(0.3)
    return out, total


def fetch_futu(hours=26, errors=None):
    """富途 7×24 快訊:只留相關個股是 FUTU_STOCKS 的,或標題命中金十那份總經關鍵字的。"""
    errors = errors if errors is not None else []
    news, seq = [], None
    cutoff0 = datetime.now(TAIPEI) - timedelta(hours=hours)
    for _ in range(FUTU_MAX_PAGES):
        params = {"pageSize": 50}
        if seq:
            params["seqMark"] = seq
        try:
            r = requests.get("https://news.futunn.com/news-site-api/main/get-flash-list", params=params,
                             headers=dict(UA, Referer="https://news.futunn.com/", Accept="application/json"), timeout=25)
            r.raise_for_status()
            d = r.json()["data"]["data"]
            page = d["news"]
        except Exception as e:  # noqa: BLE001
            errors.append(f"富途快訊:{str(e)[:80]}")
            break
        news += page
        seq = d.get("seqMark")
        if not page or not d.get("hasMore") or not seq:
            break
        try:
            if datetime.fromtimestamp(int(page[-1]["time"]), TAIPEI) < cutoff0:
                break
        except Exception:  # noqa: BLE001
            break
        time.sleep(0.2)
    if not news:
        return [], 0
    out = []
    cutoff = datetime.now(TAIPEI) - timedelta(hours=hours)
    for x in news:
        content = _clean(x.get("content"))
        title = _clean(x.get("title")) or content[:40]
        try:
            t = datetime.fromtimestamp(int(x["time"]), TAIPEI)
            if t < cutoff:
                continue
        except Exception:  # noqa: BLE001
            t = None
        # relatedStocks 只是數字 id,股票代號在 quote[].code(例如 "NVDA.US")
        codes = {str(q.get("code", "")).split(".")[0].upper() for q in (x.get("quote") or []) if isinstance(q, dict)}
        stock_hit = bool(codes & set(FUTU_STOCKS))
        if not (stock_hit or _has_kw(title + content, JIN10_KEYWORDS)):
            continue
        out.append({"source": "富途", "title": title, "summary": content[:160],
                    "time": t.strftime("%Y-%m-%d %H:%M:%S") if t else "", "link": x.get("detailUrl", "")})
    return out, len(news)


def _norm_title(t):
    return re.sub(r"[\s【】\[\]()（）:：,，。.!！?？\-—]", "", t)[:24]


def dedup_futu_against_jin10(futu, jin10):
    """富途大多轉載金十:標題(去標點取前 24 字)或摘要開頭相同的,富途那則丟掉"""
    keys = {_norm_title(x["title"]) for x in jin10} | {_norm_title(x["summary"]) for x in jin10}
    return [x for x in futu if _norm_title(x["title"]) not in keys and _norm_title(x["summary"]) not in keys]


def save_candidates(conn, report_date, items):
    """候選存進 news_candidates(日期、來源、標題、連結),回傳『昨天以前沒送過』的 items:
    連續兩天同標題不重複送給 Claude(Google News 的舊聞常連兩天出現)。"""
    if conn is None:
        return items
    fresh = []
    prev = {r[0] for r in conn.execute(
        "SELECT title FROM news_candidates WHERE date < ? AND date >= ?",
        (report_date, (date.fromisoformat(report_date) - timedelta(days=2)).isoformat()))}
    for it in items:
        if it["title"] in prev:
            continue
        fresh.append(it)
    for it in items:
        conn.execute("INSERT OR REPLACE INTO news_candidates VALUES (?,?,?,?)",
                     (report_date, it["source"], it["title"], it["link"]))
    conn.execute("DELETE FROM news_candidates WHERE date < ?",
                 ((date.fromisoformat(report_date) - timedelta(days=10)).isoformat(),))
    conn.commit()
    return fresh
