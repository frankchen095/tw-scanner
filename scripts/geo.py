"""
geo.py —— 地緣分點:把公司總部地址和券商分點地址解析成「縣市 + 鄉鎮市區」,找出同區的分點

資料來源(免費公開資料,存成本地對照表,每月更新一次):
  公司地址  證交所 https://mopsfin.twse.com.tw/opendata/t187ap03_L.csv
            櫃買   https://mopsfin.twse.com.tw/opendata/t187ap03_O.csv   (欄位「住址」)
  分點地址  https://openapi.twse.com.tw/v1/opendata/OpenData_BRK02        (欄位「地址」,
            證券商代號和 FinMind 的 securities_trader_id 是同一套)

解析規則:台/臺統一成「臺」;去掉開頭的郵遞區號;開頭必須是 22 個縣市名之一,後面緊接著
「XX區/鄉/鎮/市」才算有區。有些舊式地址沒寫區(例如「台北市中山北路2段113號」),這種
解析不出區,就不參與同區比對,而且會列在 unparsed 清單裡,絕不用猜的。
"""

import io
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

CITIES = [
    "臺北市", "新北市", "桃園市", "臺中市", "臺南市", "高雄市", "基隆市", "新竹市", "嘉義市",
    "新竹縣", "苗栗縣", "彰化縣", "南投縣", "雲林縣", "嘉義縣", "屏東縣", "宜蘭縣", "花蓮縣",
    "臺東縣", "澎湖縣", "金門縣", "連江縣",
]
_CITY_RE = re.compile("(" + "|".join(CITIES) + ")")
_DIST_RE = re.compile(r"([一-鿿]{1,3}?[區鄉鎮市])")
_FULL2HALF = str.maketrans("０１２３４５６７８９－（）", "0123456789-()")

# 外資券商(名稱關鍵字)。外資券商總部都在台北市,本來就不會跟「總部不在台北市、新北市」的公司同區,
# 這裡明確排除是為了符合規格,而且會把被排除的名單印出來讓你檢查。
FOREIGN_KEYWORDS = [
    "摩根", "美林", "高盛", "瑞銀", "花旗", "野村", "麥格理", "法銀", "匯豐", "德意志", "巴克萊",
    "里昂", "渣打", "星展", "港商", "美商", "新加坡商", "日商", "英商", "法商", "德商", "瑞士商",
    "大和國泰", "香港上海", "台灣匯立", "法興", "法國興業", "瑞穗", "三菱", "SMBC", "UBS", "法巴",
]

COMPANY_URLS = [
    "https://mopsfin.twse.com.tw/opendata/t187ap03_L.csv",
    "https://mopsfin.twse.com.tw/opendata/t187ap03_O.csv",
]
BROKER_URL = "https://openapi.twse.com.tw/v1/opendata/OpenData_BRK02"
H = {"User-Agent": "Mozilla/5.0"}


# 14 個縣轄市:地址常常只寫「彰化市…」不寫縣名。這是固定的行政區對照,不是猜的。
COUNTY_CITIES = {
    "宜蘭市": "宜蘭縣", "竹北市": "新竹縣", "頭份市": "苗栗縣", "苗栗市": "苗栗縣", "彰化市": "彰化縣",
    "員林市": "彰化縣", "南投市": "南投縣", "斗六市": "雲林縣", "太保市": "嘉義縣", "朴子市": "嘉義縣",
    "屏東市": "屏東縣", "花蓮市": "花蓮縣", "臺東市": "臺東縣", "馬公市": "澎湖縣",
}
_PARK_RE = re.compile(r"^(?:新竹)?科學(?:工業)?園區")
# 人工補登:data/geo_overrides.json,格式 {"companies": {"2330": {"city": "新竹市", "district": "東區"}},
#                                      "brokers": {"1234": {"city": "...", "district": "..."}}}
OVERRIDES_PATH = Path(__file__).resolve().parent.parent / "data" / "geo_overrides.json"


def normalize(addr):
    s = str(addr or "").strip().translate(_FULL2HALF)
    s = re.sub(r"^\(?\d{3,6}\)?\s*", "", s)  # 郵遞區號
    s = re.sub(r"\s+", "", s).replace("台", "臺").replace("巿", "市")  # 「巿」是長得像「市」的異體字
    s = _PARK_RE.sub("", s)  # 「新竹科學園區」只是園區名,後面才是縣市或路名
    if s.startswith("北市"):
        s = "臺" + s  # 「北市…」= 臺北市的簡寫
    return s


def parse_address(addr):
    """回傳 (縣市, 鄉鎮市區)。縣市對不上回傳 (None, None);縣市有但沒寫區回傳 (縣市, None)。"""
    a = normalize(addr)
    m = _CITY_RE.match(a)
    if not m:
        for cc, county in COUNTY_CITIES.items():
            if a.startswith(cc):
                return county, cc
        return None, None
    d = _DIST_RE.match(a[m.end():])
    dist = d.group(1) if d else None
    if dist and (dist == "園區" or any(w in dist for w in NOT_A_DISTRICT)):
        dist = None  # 「科學園區」「工業區」不是行政區
    return m.group(1), dist


NOT_A_DISTRICT = ("科學", "工業", "加工", "出口", "科技", "產業", "經貿", "軟體")


def is_foreign(name, city=None):
    """名稱含外資關鍵字,而且(沒地址 或 在臺北市)才算外資。真正的外資券商都在臺北市,
    像『群益金鼎-高盛』這種在高雄的國內分點不能誤判。"""
    return any(k in str(name) for k in FOREIGN_KEYWORDS) and city in (None, "臺北市")


def load_overrides():
    import json

    if not OVERRIDES_PATH.exists():
        return {"companies": {}, "brokers": {}}
    return json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))


def _get(url):
    try:
        return requests.get(url, headers=H, timeout=60)
    except requests.exceptions.SSLError:
        print("    (SSL 憑證檢查失敗,改用不驗證憑證抓取公開資料)")
        return requests.get(url, headers=H, timeout=60, verify=False)


def fetch_companies():
    """回傳 DataFrame(stock_id, name, address)。上市 + 上櫃。"""
    frames = []
    for url in COMPANY_URLS:
        r = _get(url)
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.content.decode("utf-8-sig")), dtype=str)
        frames.append(df[["公司代號", "公司名稱", "住址"]].rename(
            columns={"公司代號": "stock_id", "公司名稱": "name", "住址": "address"}))
    return pd.concat(frames, ignore_index=True)


def fetch_brokers():
    """回傳 DataFrame(trader_id, name, address)。"""
    r = _get(BROKER_URL)
    r.raise_for_status()
    df = pd.DataFrame(r.json())
    return df[["證券商代號", "證券商名稱", "地址"]].rename(
        columns={"證券商代號": "trader_id", "證券商名稱": "name", "地址": "address"})


def refresh_geo(conn, max_age_days=30, force=False):
    """每月更新一次地址對照表。抓失敗就沿用舊的,回傳錯誤訊息(沒有錯誤回傳 None)。"""
    row = conn.execute("SELECT MAX(updated) FROM company_geo").fetchone()
    fresh = row and row[0] and row[0] >= (date.today() - timedelta(days=max_age_days)).isoformat()
    if fresh and not force:
        return None
    try:
        today = date.today().isoformat()
        comp = fetch_companies()
        brk = fetch_brokers()
        ov = load_overrides()
        crow = []
        for r in comp.itertuples():
            sid = str(r.stock_id).strip()
            city, dist = parse_address(r.address)
            if sid in ov["companies"]:
                city, dist = ov["companies"][sid]["city"], ov["companies"][sid]["district"]
            crow.append((sid, r.name, r.address, city, dist, today))
        brow = []
        for r in brk.itertuples():
            tid = str(r.trader_id).strip()
            city, dist = parse_address(r.address)
            if tid in ov["brokers"]:
                city, dist = ov["brokers"][tid]["city"], ov["brokers"][tid]["district"]
            brow.append((tid, r.name, r.address, city, dist, 1 if is_foreign(r.name, city) else 0, today))
        conn.execute("DELETE FROM company_geo")
        conn.execute("DELETE FROM broker_geo")
        conn.executemany("INSERT OR REPLACE INTO company_geo VALUES (?,?,?,?,?,?)", crow)
        conn.executemany("INSERT OR REPLACE INTO broker_geo VALUES (?,?,?,?,?,?,?)", brow)
        conn.commit()
        return None
    except Exception as e:  # noqa: BLE001
        return f"地緣分點的公司/分點地址更新失敗({str(e)[:100]}),沿用上次的對照表"


def same_district_brokers(conn):
    """回傳 {stock_id: {trader_id…}}:同縣市同區、非外資、有地址的分點。公司總部在臺北市/新北市的不納入。"""
    brokers = {}
    for tid, city, dist in conn.execute(
        "SELECT trader_id, city, district FROM broker_geo WHERE is_foreign=0 AND city IS NOT NULL AND district IS NOT NULL"
    ):
        brokers.setdefault((city, dist), set()).add(tid)
    out = {}
    for sid, city, dist in conn.execute(
        "SELECT stock_id, city, district FROM company_geo WHERE city IS NOT NULL AND district IS NOT NULL "
        "AND city NOT IN ('臺北市','新北市')"
    ):
        out[sid] = brokers.get((city, dist), set())
    return out
