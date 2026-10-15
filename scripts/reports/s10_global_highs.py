"""
策略十:美國、日本族群創新高

用『族群 ETF』當作族群的代表,看前一個交易日誰的還原收盤價創了近 60 個交易日新高:
  美國:SPDR 11 大類股 ETF(科技、金融、能源…)+ 幾檔台灣投資人常看的主題 ETF(半導體、軟體、資安…)
  日本:NEXT FUNDS TOPIX-17 業種別 ETF(17 個業種,代號 1617~1633)
資料來源:Yahoo Finance 的公開行情(還原收盤價,已含配息調整)。抓不到的 ETF 會列在最後,不會用別的資料頂替。

『前一天』的算法:報表日是台灣日期 D,晚上 21:00 美股當天還沒開盤,所以美國取 D 以前(不含 D)最新一個
已收盤的交易日;日本 D 當天 14:00(台灣時間)就收盤了,取 D 當天(含)以前最新一個交易日。
這個策略選的是 ETF 不是台股,所以不進交集榜、也不進成效追蹤。
"""

import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from context import nodata, result  # noqa: E402

TITLE = "【策略十:美日族群創新高】前一個交易日創近60日新高的族群 ETF"

US = [
    ("XLK", "科技"), ("XLF", "金融"), ("XLE", "能源"), ("XLV", "醫療保健"), ("XLY", "非必需消費"),
    ("XLP", "必需消費"), ("XLI", "工業"), ("XLB", "原物料"), ("XLU", "公用事業"), ("XLRE", "房地產"),
    ("XLC", "通訊服務"),
    ("SMH", "半導體"), ("IGV", "軟體"), ("CIBR", "資安"), ("XBI", "生技"), ("KRE", "區域銀行"),
    ("ITA", "航太國防"), ("XHB", "住宅營建"), ("XRT", "零售"), ("IYT", "運輸"), ("GDX", "金礦"),
    ("COPX", "銅礦"), ("URA", "鈾與核能"),
]
JP = [
    ("1617.T", "食品"), ("1618.T", "能源資源"), ("1619.T", "建設資材"), ("1620.T", "素材化學"),
    ("1621.T", "醫藥品"), ("1622.T", "汽車運輸機"), ("1623.T", "鋼鐵非鐵"), ("1624.T", "機械"),
    ("1625.T", "電機精密"), ("1626.T", "資訊通信服務"), ("1627.T", "電力瓦斯"), ("1628.T", "運輸物流"),
    ("1629.T", "商社批發"), ("1630.T", "零售"), ("1631.T", "銀行"), ("1632.T", "金融(不含銀行)"),
    ("1633.T", "不動產"),
]
WINDOW = 60  # 近 60 個交易日(含當天)
YEAR = 250
UA = {"User-Agent": "Mozilla/5.0"}


def _fetch_bars(symbol):
    """回傳 [(日期, 還原收盤價)](由舊到新);失敗丟例外"""
    last = None
    for attempt in range(3):
        try:
            r = requests.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
                params={"range": "1y", "interval": "1d"}, headers=UA, timeout=25,
            )
            r.raise_for_status()
            res = r.json()["chart"]["result"][0]
            off = res["meta"].get("gmtoffset", 0)
            closes = res["indicators"].get("adjclose", [{}])[0].get("adjclose") or res["indicators"]["quote"][0]["close"]
            out = []
            for ts, c in zip(res["timestamp"], closes):
                if c is None:
                    continue
                d = (datetime.fromtimestamp(ts, timezone.utc) + timedelta(seconds=off)).date()
                out.append((d, float(c)))
            return out
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise last


def _analyze(bars, cutoff):
    """bars 只留日期 <= cutoff 的;回傳 dict 或 None(資料不夠)"""
    bars = [b for b in bars if b[0] <= cutoff]
    if len(bars) < WINDOW + 1:
        return None
    d, close = bars[-1]
    prev = bars[-2][1]
    win = [c for _, c in bars[-WINDOW:]]
    yr = [c for _, c in bars[-YEAR:]]
    ret5 = close / bars[-6][1] - 1 if len(bars) >= 6 else None
    return {
        "date": d.isoformat(),
        "close": close,
        "chg": close / prev - 1,
        "ret5": ret5,
        "new60": close >= max(win),
        "new52w": len(yr) >= 200 and close >= max(yr),
        "gap": 1 - close / max(win),  # 離近 60 日高點還差多少(創新高 = 0)
    }


def build(ctx):
    day = date.fromisoformat(ctx.day)
    cutoff = {"美國": day - timedelta(days=1), "日本": day}
    jobs = [("美國", s, n) for s, n in US] + [("日本", s, n) for s, n in JP]

    def run(job):
        mkt, sym, name = job
        try:
            return job, _analyze(_fetch_bars(sym), cutoff[mkt]), None
        except Exception as e:  # noqa: BLE001
            return job, None, str(e)[:60]

    with ThreadPoolExecutor(max_workers=6) as ex:
        outs = list(ex.map(run, jobs))

    if all(a is None for _, a, _ in outs):
        return {"s10": nodata("s10", TITLE, "今日無資料(Yahoo Finance 行情抓不到,沒有用其他資料頂替)")}

    rows, lines, failed, dates = [], [], [], {}
    for mkt in ("美國", "日本"):
        sub = [(j, a) for j, a, _ in outs if j[0] == mkt and a is not None]
        bad = [j for j, a, _ in outs if j[0] == mkt and a is None]
        failed += [f"{n}({s})" for _, s, n in bad]
        if not sub:
            lines.append(f"{mkt}:今日無資料")
            continue
        day_used = max(a["date"] for _, a in sub)
        dates[mkt] = day_used
        hits = sorted([(j, a) for j, a in sub if a["new60"]], key=lambda x: -(x[1]["ret5"] or 0))
        lines.append(f"{mkt}({day_used}收盤):{len(sub)} 個族群中 {len(hits)} 個創60日新高")
        for i, ((_, sym, name), a) in enumerate(hits, 1):
            tag = " ★52週新高" if a["new52w"] else ""
            r5 = f" 近5日{a['ret5'] * 100:+.1f}%" if a["ret5"] is not None else ""
            lines.append(f"{i}. {name}({sym.replace('.T', '')}) 單日{a['chg'] * 100:+.1f}%{r5}{tag}")
            rows.append({"market": mkt, "symbol": sym, "name": name, **a})
        if not hits:
            lines.append("今天沒有族群創60日新高")
    if failed:
        lines.append("(沒抓到資料或資料不足:" + "、".join(failed) + ")")
    lines.append("(族群用 ETF 代表;日期是各市場實際取得的最新收盤日)")
    return {"s10": result("s10", TITLE, lines, [], rows=rows, failed=failed, dates=dates)}
