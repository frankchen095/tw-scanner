"""
mops_buyback.py —— 公開資訊觀測站「庫藏股統計彙總表」(t35sc09):抓上市 + 上櫃的庫藏股買回計畫

端點:POST https://mopsov.twse.com.tw/mops/web/ajax_t35sc09
  - d1、d2 是『董事會決議日期』的範圍,格式是民國年的 yyymmdd,不能有斜線(例:1120101)
  - TYPEK:sii = 上市、otc = 上櫃;一次請求就回傳這個範圍內『所有公司』,沒有筆數上限
  - 一定要用 mopsov.twse.com.tw 這個主機(mops.twse.com.tw 會被擋);請求之間要等 6 秒以上
欄位單位:股數都是『股』(不是張),金額是元,日期是民國 yyy/mm/dd。

重要限制:『本次已買回股數』要等計畫標示執行完畢(done_flag = Y)才會填,執行中(N)的計畫
是空白,所以只能拿已結束的計畫去推算當時用哪個分點。
"""

import html
import re
import time
from datetime import date, timedelta

import requests

URL = "https://mopsov.twse.com.tw/mops/web/ajax_t35sc09"
HDR = {"User-Agent": "Mozilla/5.0", "Referer": "https://mopsov.twse.com.tw/mops/web/t35sc09"}
COLS = [
    "seq", "code", "name", "board_date", "purpose", "legal_limit_amt", "plan_shares", "price_low",
    "price_high", "period_start", "period_end", "done_flag", "_btn", "bought_shares",
    "cancelled_shares", "pct_of_plan", "bought_amount", "avg_price", "pct_of_issued", "not_done_reason",
]


def _roc8(d):
    return f"{d.year - 1911}{d.month:02d}{d.day:02d}"


def _iso(roc):
    m = re.fullmatch(r"(\d{2,3})/(\d{2})/(\d{2})", str(roc).strip())
    return f"{int(m.group(1)) + 1911}-{m.group(2)}-{m.group(3)}" if m else None


def _num(s):
    s = re.sub(r"[,\s]", "", str(s))
    try:
        return float(s)
    except ValueError:
        return None


def _fetch(typek, d1, d2, retries=5, delay=6):
    data = {"encodeURIComponent": "1", "step": "1", "firstin": "1", "off": "1",
            "TYPEK": typek, "d1": _roc8(d1), "d2": _roc8(d2), "RD": "1"}
    err = ""
    for i in range(retries):
        try:
            r = requests.post(URL, data=data, headers=HDR, timeout=60)
            r.encoding = "utf-8"
            if r.status_code == 200 and ("件數合計" in r.text or "查無" in r.text):
                return r.text
            err = f"status {r.status_code} len {len(r.text)}"
        except requests.RequestException as e:
            err = repr(e)
        time.sleep(delay * (i + 1))
    raise RuntimeError(f"公開資訊觀測站庫藏股資料抓取失敗:{err[:120]}")


def _parse(text):
    out = []
    for tr in re.findall(r"<tr class= '(?:even|odd)'.*?</tr>", text, flags=re.S):
        tds = [html.unescape(re.sub(r"<[^>]+>", "", c)).replace("\xa0", "").strip()
               for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, flags=re.S)]
        if len(tds) == len(COLS):
            out.append(dict(zip(COLS, tds)))
    m = re.search(r"件數合計[:：]\s*([\d,]+)", text)
    if m and int(m.group(1).replace(",", "")) != len(out):
        raise RuntimeError(f"庫藏股資料解析筆數({len(out)})和網頁合計({m.group(1)})對不上,網頁格式可能變了")
    return out


def fetch_programs(start, end):
    """回傳 list[dict],上市 + 上櫃,董事會決議日在 [start, end](date 物件)。"""
    programs = []
    for i, (typek, market) in enumerate([("sii", "上市"), ("otc", "上櫃")]):
        if i:
            time.sleep(6)
        for r in _parse(_fetch(typek, start, end)):
            programs.append({
                "stock_id": r["code"],
                "name": r["name"],
                "market": market,
                "board_date": _iso(r["board_date"]),
                "start_date": _iso(r["period_start"]),
                "end_date": _iso(r["period_end"]),
                "planned_shares": _num(r["plan_shares"]),
                "bought_shares": _num(r["bought_shares"]),
                "done_flag": r["done_flag"].strip().upper(),
            })
    return [p for p in programs if p["start_date"] and p["end_date"]]


def store_programs(conn, programs):
    today = date.today().isoformat()
    conn.executemany(
        "INSERT OR REPLACE INTO buyback_programs "
        "(stock_id, board_date, start_date, end_date, planned_shares, bought_shares, done_flag, market, updated) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        [(p["stock_id"], p["board_date"], p["start_date"], p["end_date"], p["planned_shares"],
          p["bought_shares"], p["done_flag"], p["market"], today) for p in programs],
    )
    conn.commit()


def refresh_programs(conn, days_back=180, max_age_days=1, force=False):
    """每天更新一次最近 days_back 天內董事會決議的計畫(用來標『庫藏股執行中』)。
    抓失敗就沿用舊資料,回傳錯誤訊息(沒有錯誤回傳 None)。"""
    row = conn.execute("SELECT MAX(updated) FROM buyback_programs").fetchone()
    fresh = row and row[0] and row[0] >= (date.today() - timedelta(days=max_age_days)).isoformat()
    if fresh and not force:
        return None
    try:
        store_programs(conn, fetch_programs(date.today() - timedelta(days=days_back), date.today()))
        return None
    except Exception as e:  # noqa: BLE001
        return f"庫藏股買回計畫更新失敗({str(e)[:100]}),『庫藏股執行中』標記沿用上次的資料"
