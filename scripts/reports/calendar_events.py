"""
行事曆:未來 14 天(報表日隔天起算)的重要事件

  台股法說會   公開資訊觀測站法說會一覽表(只列市值>500億,拿不到市值表就全列)
  美股財報     Nasdaq 公開的財報日曆;只列市值≥2500億美元,或台灣投資人常追的半導體/AI/科技權值股
  重大經濟數據 Nasdaq 公開的經濟日曆,只留美國的主要數據(CPI、非農、GDP、PCE、PMI、初領失業金…)
  FOMC        聯準會官網公布的 FOMC 會議日期
抓不到的來源會明確寫出來,不會用別的資料頂替。日期:法說會是台灣日期、財報是美東日期、經濟數據是各國當地日期(Nasdaq 的時間欄位不可靠,所以不列時間)。
台灣自己的經濟數據(出口訂單、主計總處 GDP、央行理監事會)沒有可用的公開 API,所以這份不含。
"""

import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from context import result  # noqa: E402

TITLE = "【行事曆】未來兩週重要事件(法說會、美股財報、經濟數據)"
DAYS = 14
UA = {"User-Agent": "Mozilla/5.0"}
NASDAQ_H = {"User-Agent": "Mozilla/5.0", "Accept": "application/json", "Origin": "https://www.nasdaq.com",
            "Referer": "https://www.nasdaq.com/"}
WD = "一二三四五六日"

# 台灣投資人常追的美股(不到 1000 億美元也列)
US_WATCH = {
    "NVDA", "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "META", "TSLA", "AMD", "AVGO", "MU", "INTC", "QCOM", "TXN",
    "AMAT", "LRCX", "KLAC", "ASML", "TSM", "SMCI", "DELL", "HPQ", "MRVL", "ARM", "ON", "ADI", "NXPI", "CRM",
    "ORCL", "IBM", "CSCO", "ANET", "VRT", "COHR", "LITE", "SNPS", "CDNS", "WDC", "STX", "CLS", "TER", "MPWR",
}

# 經濟數據:國家 → 事件名稱要完全符合其中一個(小寫比對),避免把一堆次要數據全列出來
ECON_KEEP = {
    "United States": {
        "cpi", "core cpi", "ppi", "core ppi", "pce price index", "core pce price index", "pce", "core pce",
        "gdp", "gdp growth rate", "nonfarm payrolls", "non farm payrolls", "unemployment rate", "retail sales",
        "core retail sales", "ism manufacturing pmi", "ism services pmi", "ism non-manufacturing pmi",
        "fomc minutes", "fed interest rate decision", "fomc meeting", "initial jobless claims",
        "consumer confidence", "michigan consumer sentiment", "jolts job openings", "durable goods orders",
        "core durable goods orders", "ny empire state manufacturing index", "philadelphia fed manufacturing index",
        "housing starts", "trade balance", "industrial production", "adp employment change",
        "s&p global manufacturing pmi", "s&p global services pmi",
    },
}
COUNTRY_ZH = {"United States": "美國"}
MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
     "November", "December"], 1)}


def _roc(s):
    m = re.fullmatch(r"\s*(\d{2,3})/(\d{1,2})/(\d{1,2})\s*", s or "")
    return date(int(m.group(1)) + 1911, int(m.group(2)), int(m.group(3))) if m else None


_SUFFIX = re.compile(r"[,\s]+(Inc\.?|Incorporated|Corporation|Corp\.?|Company|Co\.?|Ltd\.?|Limited|plc|PLC|N\.V\.|S\.A\.|SE|AG|"
                     r"LLC|Holdings?|Group|\(The\)|The)$")


def _short_name(n):
    n = re.sub(r"\s*\(The\)", "", n).strip()
    for _ in range(3):
        n = _SUFFIX.sub("", n).strip(" ,&")
    return n


def _md(d):
    return f"{d.month}/{d.day}({WD[d.weekday()]})"


# ---------- 各來源 ----------


def _tw_law(ctx, lo, hi):
    """回傳 ([(日期, 時間, 代號, 名稱)], 錯誤訊息或 None)"""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import risk_tags as rt  # noqa: PLC0415

    out, fails = {}, 0
    for (y, m) in sorted({(lo.year, lo.month), (hi.year, hi.month)}):
        for typek in ("sii", "otc"):
            text = None
            for _ in range(3):
                try:
                    r = requests.get(
                        "https://mopsov.twse.com.tw/mops/web/t100sb02_1",
                        params={"encodeURIComponent": 1, "step": 1, "firstin": 1, "off": 1, "TYPEK": typek,
                                "year": y - 1911, "month": m, "co_id": ""},
                        headers=rt.UA, timeout=40)
                    r.raise_for_status()
                    text = r.text
                    break
                except Exception:  # noqa: BLE001
                    time.sleep(6)
            if text is None:
                fails += 1
                continue
            p = rt._TableParser()
            p.feed(text)
            for row in p.rows:
                if len(row) >= 4 and re.fullmatch(r"\d{4}", row[0]):
                    d = _roc(row[2])
                    if d and lo <= d <= hi:
                        out[(d, row[0])] = (d, row[3].strip(), row[0], row[1].strip())
            time.sleep(4)
    err = f"台股法說會:公開資訊觀測站 {fails} 次請求失敗,名單可能不完整" if fails else None
    rows = sorted(out.values())
    if ctx.big is not None:
        rows = [r for r in rows if r[2] in ctx.big]
    return rows, err


def _us_earnings(lo, hi):
    rows, fails = [], 0
    d = lo
    while d <= hi:
        if d.weekday() < 5:
            try:
                r = requests.get("https://api.nasdaq.com/api/calendar/earnings", params={"date": d.isoformat()},
                                 headers=NASDAQ_H, timeout=25)
                r.raise_for_status()
                for x in (r.json().get("data") or {}).get("rows") or []:
                    cap = re.sub(r"[^\d]", "", x.get("marketCap") or "")
                    cap = int(cap) if cap else 0
                    sym = (x.get("symbol") or "").strip()
                    if cap >= 250_000_000_000 or sym in US_WATCH:
                        t = x.get("time") or ""
                        when = "盤前" if "pre" in t else "盤後" if "after" in t else "未定"
                        rows.append((d, sym, _short_name((x.get("name") or "").strip()), when, x.get("epsForecast") or "-", cap))
            except Exception:  # noqa: BLE001
                fails += 1
            time.sleep(0.4)
        d += timedelta(days=1)
    rows.sort(key=lambda r: (r[0], -r[5]))
    err = f"美股財報:Nasdaq 財報日曆 {fails} 天抓取失敗,名單可能不完整" if fails else None
    return rows, err


ZH = {
    "cpi": "消費者物價指數 CPI", "core cpi": "核心 CPI", "ppi": "生產者物價指數 PPI", "core ppi": "核心 PPI",
    "pce price index": "PCE 物價指數", "core pce price index": "核心 PCE 物價指數", "gdp": "GDP",
    "gdp growth rate": "GDP 成長率", "nonfarm payrolls": "非農就業", "non farm payrolls": "非農就業",
    "unemployment rate": "失業率", "retail sales": "零售銷售", "core retail sales": "核心零售銷售",
    "ism manufacturing pmi": "ISM 製造業 PMI", "ism services pmi": "ISM 服務業 PMI",
    "ism non-manufacturing pmi": "ISM 服務業 PMI", "fomc minutes": "FOMC 會議紀要",
    "initial jobless claims": "初領失業金人數", "consumer confidence": "消費者信心", "michigan consumer sentiment":
    "密大消費者信心", "jolts job openings": "JOLTS 職缺", "durable goods orders": "耐久財訂單",
    "core durable goods orders": "核心耐久財訂單", "ny empire state manufacturing index": "紐約州製造業指數",
    "philadelphia fed manufacturing index": "費城聯儲製造業指數", "housing starts": "新屋開工",
    "trade balance": "貿易收支", "industrial production": "工業生產", "adp employment change": "ADP 就業",
    "s&p global manufacturing pmi": "標普製造業 PMI", "s&p global services pmi": "標普服務業 PMI",
    "boj interest rate decision": "日銀利率決策", "national cpi": "全國 CPI", "national core cpi": "全國核心 CPI",
    "tankan large manufacturers index": "短觀(大型製造業)", "exports": "出口", "manufacturing & services pmi": "製造業/服務業 PMI",
    "pboc loan prime rate": "人行貸款市場報價利率 LPR", "china loan prime rate 5y": "5年期 LPR",
    "nbs manufacturing pmi": "官方製造業 PMI", "caixin manufacturing pmi": "財新製造業 PMI",
    "caixin services pmi": "財新服務業 PMI", "chinese unemployment rate": "城鎮調查失業率",
}
# 聯準會利率決策以聯準會官網的 FOMC 日期為準,Nasdaq 的日期欄位偏差過,這幾項不重複列
SKIP_US = {"fed interest rate decision", "fomc meeting"}


def _econ(lo, hi):
    out, fails = {}, 0
    d = lo
    while d <= hi:
        try:
            r = requests.get("https://api.nasdaq.com/api/calendar/economicevents", params={"date": d.isoformat()},
                             headers=NASDAQ_H, timeout=25)
            r.raise_for_status()
            for x in (r.json().get("data") or {}).get("rows") or []:
                country = x.get("country")
                name = (x.get("eventName") or "").strip()
                low = name.lower()
                if country not in ECON_KEEP or low not in ECON_KEEP[country]:
                    continue
                if country == "United States" and low in SKIP_US:
                    continue
                zh = ZH.get(low, name)
                prev = (x.get("previous") or "").replace("&nbsp;", "").strip()
                cons = (x.get("consensus") or "").replace("&nbsp;", "").strip()
                out[(d, country, zh)] = {"date": d, "country": country, "name": zh, "prev": prev, "cons": cons}
        except Exception:  # noqa: BLE001
            fails += 1
        time.sleep(0.4)
        d += timedelta(days=1)
    err = f"經濟數據:Nasdaq 經濟日曆 {fails} 天抓取失敗,名單可能不完整" if fails else None
    return [out[k] for k in sorted(out)], err


def _fomc(lo, hi):
    r = requests.get("https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm", headers=UA, timeout=25)
    r.raise_for_status()
    text = re.sub(r"<[^>]+>", " ", r.text)
    text = re.sub(r"\s+", " ", text)
    found = []
    for y in {lo.year, hi.year}:
        i = text.find(f"{y} FOMC Meetings")
        if i < 0:
            continue
        seg = text[i:i + 1500]
        nxt = re.search(r"20\d\d FOMC Meetings", seg[10:])
        if nxt:
            seg = seg[:10 + nxt.start()]
        for m in re.finditer(r"(January|February|March|April|May|June|July|August|September|October|November|"
                             r"December)\s+(\d{1,2})(?:\s*-\s*(\d{1,2}))?", seg):
            mon, d1, d2 = MONTHS[m.group(1)], int(m.group(2)), m.group(3)
            end = date(y, mon, int(d2) if d2 else d1)
            if lo <= end <= hi:
                found.append((date(y, mon, d1), end))
    return sorted(set(found))


# ---------- 組裝 ----------


def build(ctx):
    today = date.fromisoformat(ctx.day)
    lo, hi = today + timedelta(days=1), today + timedelta(days=DAYS)
    errors, lines, meta = [], [f"統計區間:{_md(lo)} ~ {_md(hi)}(法說會為台灣日期;美股財報為美東日期;經濟數據為各國當地日期)"], {}

    try:
        law, err = _tw_law(ctx, lo, hi)
        errors += [err] if err else []
    except Exception as e:  # noqa: BLE001
        law, errors = [], errors + [f"台股法說會:{str(e)[:100]}"]
    scope = "市值>500億" if ctx.big is not None else "全部,沒有市值表"
    lines.append(f"\n台股法說會({scope},{len(law)} 場)")
    lines += [f"{_md(d)} {t} {ctx.label(c) if hasattr(ctx, 'label') else n}" for d, t, c, n in law] or ["這段期間沒有查到"]
    meta["law"] = [{"date": d.isoformat(), "time": t, "sid": c, "name": n} for d, t, c, n in law]

    try:
        earn, err = _us_earnings(lo, hi)
        errors += [err] if err else []
    except Exception as e:  # noqa: BLE001
        earn, errors = [], errors + [f"美股財報:{str(e)[:100]}"]
    lines.append(f"\n美股財報(市值≥2500億美元或科技/半導體權值股,{len(earn)} 家)")
    lines += [f"{_md(d)} {name}({sym}) {when} EPS預期{eps}" for d, sym, name, when, eps, _ in earn] or ["這段期間沒有查到"]
    meta["earnings"] = [{"date": d.isoformat(), "symbol": s, "name": n, "when": w, "eps": e}
                        for d, s, n, w, e, _ in earn]

    try:
        econ, err = _econ(lo, hi)
        errors += [err] if err else []
    except Exception as e:  # noqa: BLE001
        econ, errors = [], errors + [f"經濟數據:{str(e)[:100]}"]
    try:
        fomc = _fomc(lo, hi)
    except Exception as e:  # noqa: BLE001
        fomc, errors = [], errors + [f"FOMC 日期:{str(e)[:100]}"]
    lines.append(f"\n重大經濟數據與央行(美國,{len(econ) + len(fomc)} 項)")
    items = [(a, f"{_md(a)}~{_md(b)} 美國 FOMC 利率會議(決議在{_md(b)})") for a, b in fomc]
    for e in econ:
        extra = " ".join(p for p in (f"預期{e['cons']}" if e["cons"] else "", f"前值{e['prev']}" if e["prev"] else "") if p)
        items.append((e["date"], f"{_md(e['date'])} {COUNTRY_ZH[e['country']]} {e['name']}"
                      + (f"({extra})" if extra else "")))
    items.sort(key=lambda x: x[0])
    lines += [t for _, t in items] or ["這段期間沒有查到"]
    meta["econ"] = [{"date": e["date"].isoformat(), "country": COUNTRY_ZH[e["country"]],
                     "name": e["name"], "cons": e["cons"], "prev": e["prev"]} for e in econ]
    meta["fomc"] = [{"start": a.isoformat(), "end": b.isoformat()} for a, b in fomc]

    lines.append("\n(台灣的出口訂單、主計總處 GDP、央行理監事會沒有可用的公開 API,這份不含)")
    if errors:
        lines.append("資料來源問題:" + ";".join(errors))
    meta["errors"] = errors
    return {"calendar": result("calendar", TITLE, lines, [], **meta)}
