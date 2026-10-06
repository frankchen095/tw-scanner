"""
risk_tags.py —— 風險標記:〔處置〕〔注意〕〔除權息 月/日〕〔法說 月/日〕,策略三另外標能不能放空

資料來源(都是證交所/櫃買中心/公開資訊觀測站的公開資料,2026-10 實測可用):
  處置股  上市 https://www.twse.com.tw/rwd/zh/announcement/punish?response=json
          上櫃 https://www.tpex.org.tw/openapi/v1/tpex_disposal_information
  注意股  上市 https://www.twse.com.tw/rwd/zh/announcement/notice?response=json
          上櫃 https://www.tpex.org.tw/openapi/v1/tpex_trading_warning_information
  除權息  上市 https://openapi.twse.com.tw/v1/exchangeReport/TWT48U_ALL
          上櫃 https://www.tpex.org.tw/openapi/v1/tpex_exright_prepost
  法說會  公開資訊觀測站網頁表格(沒有 JSON/CSV 版本,只能解析 HTML,容易被擋,見下)
          https://mopsov.twse.com.tw/mops/web/t100sb02_1
  融券    上市 https://www.twse.com.tw/rwd/zh/marginTrading/TWT92U (暫停融券賣出)
              https://www.twse.com.tw/rwd/zh/marginTrading/BFI84U (停券預告)
          上櫃 https://www.tpex.org.tw/openapi/v1/tpex_margin_trading_margin_mark
              https://www.tpex.org.tw/openapi/v1/tpex_margin_trading_term

任何一個來源抓不到,都不會編造或略過:會記在 errors 裡,報表最後面明確列出
「哪些標記今天沒有資料」。

找不到的資料(誠實講):
  - 「漲停股被限制平盤下放空」沒有對應的公開資料。證交所 TWT92U 那一欄只對
    「前一交易日收盤跌停」的股票有意義,對漲停股不適用。所以策略三只標
    「可不可以融券」(暫停融券賣出/停券預告/沒有融資融券資格),不會拿處置股當替代。
"""

import html as html_lib
import re
import sys
import time
from datetime import date, timedelta
from html.parser import HTMLParser
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import finmind_query  # noqa: E402

UA = {"User-Agent": "Mozilla/5.0"}
TIMEOUT = 30


def _get_json(url, params=None, retries=2):
    last = None
    for _ in range(retries + 1):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=TIMEOUT)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2)
    raise RuntimeError(f"{url} 連線失敗: {last}")


def _roc_to_date(s):
    """民國日期字串 → date。支援 115/10/12、115.10.12、1151012。解析不出來回傳 None。"""
    if s is None:
        return None
    m = re.search(r"(\d{2,3})\D?(\d{2})\D?(\d{2})", str(s).strip())
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return date(y + 1911, mo, d)
    except ValueError:
        return None


def _iso(d):
    return d.strftime("%Y%m%d")


def _next_trading_days(day, n):
    """day 之後的 n 個交易日。優先用 FinMind 交易日曆,失敗就用週一~週五近似(會在 errors 註明)。"""
    d0 = date.fromisoformat(day)
    df = finmind_query("TaiwanStockTradingDate", None, day, (d0 + timedelta(days=40)).isoformat(), quiet=True)
    if len(df) and "date" in df.columns:
        ds = sorted(x for x in df["date"].astype(str) if x > day)
        if len(ds) >= n:
            return [date.fromisoformat(x) for x in ds[:n]], True
    out, d = [], d0
    while len(out) < n:
        d += timedelta(days=1)
        if d.weekday() < 5:
            out.append(d)
    return out, False


class _TableParser(HTMLParser):
    """把 HTML 裡所有 <tr> 的儲存格文字抽成 list of list,只用標準函式庫。"""

    def __init__(self):
        super().__init__()
        self.rows, self._row, self._cell, self._in_cell = [], None, [], False

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._in_cell, self._cell = True, []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._row is not None and self._in_cell:
            self._row.append(html_lib.unescape("".join(self._cell)).strip())
            self._in_cell = False
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._in_cell:
            self._cell.append(data)


class RiskTags:
    """載入當天所有風險資料。任何一項失敗都只記錄在 errors,不會讓整份報表中斷。"""

    def __init__(self, day):
        self.day = day
        self.today = date.fromisoformat(day)
        self.errors = []
        self.disposition = {}  # sid -> (起, 迄)
        self.attention = set()
        self.exdiv = {}  # sid -> date(未來5個交易日內)
        self.law_conf = {}  # sid -> date(未來5個交易日內)
        self.short_status = {}  # sid -> 融券狀態文字(策略三用)
        self.short_loaded = False
        self.ahead, cal_ok = _next_trading_days(day, 5)
        if not cal_ok:
            self.errors.append("交易日曆(FinMind TaiwanStockTradingDate)抓不到,『未來5個交易日』用週一~週五近似,國定假日可能有誤差")
        self._load_all()

    # ---- 載入 ----
    def _load_all(self):
        for name, fn in [
            ("處置股", self._load_disposition),
            ("注意股", self._load_attention),
            ("除權息", self._load_exdiv),
            ("法說會", self._load_law_conf),
            ("融券可否", self._load_short),
        ]:
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                self.errors.append(f"{name}:{str(e)[:120]}")

    def _load_disposition(self):
        d0 = self.today
        # 上市:證交所網站 JSON,查「今天往前一個月~今天」公告,再用處置起迄判斷今天有沒有生效
        j = _get_json(
            "https://www.twse.com.tw/rwd/zh/announcement/punish",
            {"response": "json", "startDate": _iso(d0 - timedelta(days=45)), "endDate": _iso(d0 + timedelta(days=1))},
        )
        fields = j.get("fields", [])
        i_code = next((i for i, f in enumerate(fields) if "證券代號" in f), None)
        i_per = next((i for i, f in enumerate(fields) if "處置起迄" in f), None)
        if i_code is None or i_per is None:
            raise RuntimeError(f"證交所處置股欄位名稱變了,找不到證券代號/處置起迄時間:{fields}")
        for row in j.get("data", []):
            self._add_disposition(str(row[i_code]).strip(), str(row[i_per]))
        # 上櫃
        for row in _get_json("https://www.tpex.org.tw/openapi/v1/tpex_disposal_information"):
            self._add_disposition(str(row.get("SecuritiesCompanyCode", "")).strip(), str(row.get("DispositionPeriod", "")))

    def _add_disposition(self, sid, period):
        if not sid or not re.fullmatch(r"\d{4}", sid):
            return
        parts = re.split(r"[～~]", period)
        if len(parts) != 2:
            return
        a, b = _roc_to_date(parts[0]), _roc_to_date(parts[1])
        if a and b and a <= self.today <= b:
            self.disposition[sid] = (a, b)

    def _load_attention(self):
        j = _get_json(
            "https://www.twse.com.tw/rwd/zh/announcement/notice",
            {"response": "json", "startDate": _iso(self.today), "endDate": _iso(self.today)},
        )
        fields = j.get("fields", [])
        i_code = next((i for i, f in enumerate(fields) if "證券代號" in f), None)
        if i_code is None:
            raise RuntimeError(f"證交所注意股欄位名稱變了,找不到證券代號:{fields}")
        for row in j.get("data", []):
            sid = str(row[i_code]).strip()
            if re.fullmatch(r"\d{4}", sid):
                self.attention.add(sid)
        for row in _get_json("https://www.tpex.org.tw/openapi/v1/tpex_trading_warning_information"):
            d = _roc_to_date(row.get("Date"))
            sid = str(row.get("SecuritiesCompanyCode", "")).strip()
            if d == self.today and re.fullmatch(r"\d{4}", sid):
                self.attention.add(sid)

    def _load_exdiv(self):
        lo, hi = self.today + timedelta(days=1), self.ahead[-1]
        for row in _get_json("https://openapi.twse.com.tw/v1/exchangeReport/TWT48U_ALL"):
            d, sid = _roc_to_date(row.get("Date")), str(row.get("Code", "")).strip()
            if d and re.fullmatch(r"\d{4}", sid) and lo <= d <= hi:
                self.exdiv[sid] = d
        for row in _get_json("https://www.tpex.org.tw/openapi/v1/tpex_exright_prepost"):
            d, sid = _roc_to_date(row.get("ExRrightsExDividendDate")), str(row.get("SecuritiesCompanyCode", "")).strip()
            if d and re.fullmatch(r"\d{4}", sid) and lo <= d <= hi:
                self.exdiv[sid] = d

    def _load_law_conf(self):
        """法說會:公開資訊觀測站網頁表格。一次一個月、一個市場別,請求之間要等,否則會被斷線。"""
        lo, hi = self.today + timedelta(days=1), self.ahead[-1]
        months = sorted({(lo.year, lo.month), (hi.year, hi.month)})
        failures = 0
        for (y, m) in months:
            for typek in ("sii", "otc"):
                url = "https://mopsov.twse.com.tw/mops/web/t100sb02_1"
                params = {"encodeURIComponent": 1, "step": 1, "firstin": 1, "off": 1,
                          "TYPEK": typek, "year": y - 1911, "month": m, "co_id": ""}
                text = None
                for attempt in range(3):
                    try:
                        r = requests.get(url, params=params, headers=UA, timeout=TIMEOUT)
                        r.raise_for_status()
                        text = r.text
                        break
                    except Exception:  # noqa: BLE001
                        time.sleep(6)
                if text is None:
                    failures += 1
                    continue
                p = _TableParser()
                p.feed(text)
                for row in p.rows:
                    if len(row) < 3 or not re.fullmatch(r"\d{4}", row[0]):
                        continue
                    d = _roc_to_date(row[2])
                    if d and lo <= d <= hi:
                        self.law_conf.setdefault(row[0], d)
                time.sleep(6)
        if failures:
            self.errors.append(f"法說會:公開資訊觀測站 {failures} 次請求失敗(可能被擋或連線逾時),法說會標記可能不完整")

    def _load_short(self):
        """融券狀態,只用在策略三。以報表當天資料為準(隔天的名單通常要到晚上 8:30 以後才公布)。"""
        # 上市:TWT92U 列出有融資融券資格的股票,『暫停融券賣出』欄有 * 代表暫停
        j = _get_json("https://www.twse.com.tw/rwd/zh/marginTrading/TWT92U", {"response": "json", "date": _iso(self.today)})
        fields = j.get("fields", [])
        idx_susp = next((i for i, f in enumerate(fields) if "暫停融券賣出" in f), None)
        eligible = set()
        for row in j.get("data", []):
            sid = str(row[0]).strip()
            if not re.fullmatch(r"\d{4}", sid):
                continue
            eligible.add(sid)
            if idx_susp is not None and str(row[idx_susp]).strip() == "*":
                self.short_status[sid] = "暫停融券賣出"
        # 上市停券預告:停券起日~迄日涵蓋下一個交易日的
        nxt = self.ahead[0]
        try:
            j2 = _get_json("https://www.twse.com.tw/rwd/zh/marginTrading/BFI84U", {"response": "json", "date": _iso(self.today)})
            for row in j2.get("data", []):
                sid = str(row[0]).strip()
                a, b = _roc_to_date(row[1]), _roc_to_date(row[2])
                if re.fullmatch(r"\d{4}", sid) and a and b and a <= nxt <= b and sid not in self.short_status:
                    reason = row[3] if len(row) > 3 else ""
                    self.short_status[sid] = f"停券預告 {a.month}/{a.day}~{b.month}/{b.day}({reason})"
        except Exception as e:  # noqa: BLE001
            self.errors.append(f"上市停券預告抓取失敗:{str(e)[:80]}")
        self._twse_margin_eligible = eligible
        # 上櫃
        otc_eligible = set()
        for row in _get_json("https://www.tpex.org.tw/openapi/v1/tpex_margin_trading_margin_mark"):
            sid = str(row.get("SecuritiesCompanyCode", "")).strip()
            if not re.fullmatch(r"\d{4}", sid):
                continue
            otc_eligible.add(sid)
            if str(row.get("BannedFromMarginShortSelling", "")).strip() == "*":
                self.short_status[sid] = "禁止融券賣出"
        try:
            for row in _get_json("https://www.tpex.org.tw/openapi/v1/tpex_margin_trading_term"):
                sid = str(row.get("SecuritiesCompanyCode", "")).strip()
                a, b = _roc_to_date(row.get("ShortSaleSuspensionStartDate")), _roc_to_date(row.get("ShortSaleSuspensionEndDate"))
                if re.fullmatch(r"\d{4}", sid) and a and b and a <= nxt <= b and sid not in self.short_status:
                    self.short_status[sid] = f"停券預告 {a.month}/{a.day}~{b.month}/{b.day}"
        except Exception as e:  # noqa: BLE001
            self.errors.append(f"上櫃停券預告抓取失敗:{str(e)[:80]}")
        self._otc_margin_eligible = otc_eligible
        self.short_loaded = True

    # ---- 查詢 ----
    def tags(self, sid):
        out = []
        if sid in self.disposition:
            out.append("〔處置〕")
        if sid in self.attention:
            out.append("〔注意〕")
        if sid in self.exdiv:
            d = self.exdiv[sid]
            out.append(f"〔除權息 {d.month}/{d.day}〕")
        if sid in self.law_conf:
            d = self.law_conf[sid]
            out.append(f"〔法說 {d.month}/{d.day}〕")
        return "".join(out)

    def short_label(self, sid):
        """策略三用:這檔能不能融券放空。"""
        if not self.short_loaded:
            return "放空資訊今日無資料"
        if sid in self.short_status:
            return f"不能融券:{self.short_status[sid]}"
        if sid in getattr(self, "_twse_margin_eligible", set()) or sid in getattr(self, "_otc_margin_eligible", set()):
            return "可融券"
        return "無融資融券資格(不能融券)"
