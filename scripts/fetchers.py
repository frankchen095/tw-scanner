"""
fetchers.py —— 抓資料的共用函式,每天收盤後由 main.py / 02_fetch_daily.py 呼叫

只存「普通股」(排除 ETF、ETN、權證、興櫃、存託憑證),避免資料庫塞進一堆用不到的
資料、檔案爆大(GitHub 單檔上限 100MB)。

你不需要動這個檔案。
"""

import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import finmind_query  # noqa: E402
from db import ALL_WATCHED_TRADER_IDS, buyback_brokers_by_stock, prune  # noqa: E402
from geo import same_district_brokers  # noqa: E402
from helpers import (  # noqa: E402
    BIG_MARKET_VALUE,
    get_market_values_latest,
    get_valid_stock_ids,
    limit_up_price,
    parallel_map,
)

TOP_N_BY_TURNOVER = 300  # 分點日報要逐檔查,只查成交金額前 N 名(加上漲停候選股)
WORKERS = 4  # 同時幾條執行緒打 FinMind(方案額度每小時 6000 次以上,綽綽有餘)


# ---------- 每日行情 / 市值 / 法人 ----------


def fetch_price(conn, day):
    df = finmind_query("TaiwanStockPrice", None, day, day, quiet=True)
    if len(df) == 0:
        print(f"  {day} 沒有股價資料(可能不是交易日),跳過")
        return None

    valid = get_valid_stock_ids(conn)
    df = df[df["stock_id"].astype(str).isin(valid)]

    conn.executemany(
        "INSERT OR REPLACE INTO daily_price "
        "(date, stock_id, open, high, low, close, volume, money, spread) VALUES (?,?,?,?,?,?,?,?,?)",
        [
            (day, str(r.stock_id), r.open, r.max, r.min, r.close, r.Trading_Volume, r.Trading_money, r.spread)
            for r in df.itertuples()
        ],
    )
    conn.commit()
    print(f"  {day} 股價 {len(df)} 筆(只存上市櫃普通股)")
    return df


def fetch_market_value(conn, day):
    """全市場市值(FinMind 台灣股價市值表,= 收盤價 × 已發行股數),一次查全部。"""
    df = finmind_query("TaiwanStockMarketValue", None, day, day, quiet=True)
    if len(df) == 0:
        print(f"  {day} 沒有市值資料")
        return False
    valid = get_valid_stock_ids(conn)
    df = df[df["stock_id"].astype(str).isin(valid)]
    conn.executemany(
        "INSERT OR REPLACE INTO daily_market_value VALUES (?,?,?)",
        [(day, str(r.stock_id), float(r.market_value)) for r in df.itertuples()],
    )
    conn.commit()
    print(f"  {day} 市值 {len(df)} 筆")
    return True


def fetch_institutional(conn, day):
    df = finmind_query("TaiwanStockInstitutionalInvestorsBuySell", None, day, day, quiet=True)
    if len(df) == 0:
        print(f"  {day} 沒有法人資料,跳過")
        return

    valid = get_valid_stock_ids(conn)
    df = df[df["stock_id"].astype(str).isin(valid)]

    conn.executemany(
        "INSERT OR REPLACE INTO daily_institutional VALUES (?,?,?,?,?)",
        [(day, str(r.stock_id), r.name, r.buy, r.sell) for r in df.itertuples()],
    )
    conn.commit()
    print(f"  {day} 法人買賣超 {len(df)} 筆(只存上市櫃普通股)")


# ---------- 漲停候選 ----------


def limit_up_candidates(price_df):
    """找出當天收盤鎖在漲停價的股票。回傳 {stock_id: (漲停價, 當日總成交股數)}。

    參考價 = 收盤價 − 漲跌價差(spread),除權息日也正確;漲停價 = 參考價×1.1 依跳動單位
    無條件捨去。再用「收盤價 = 當日最高價」交叉檢查。
    """
    out = {}
    for r in price_df.itertuples():
        if pd.isna(r.spread) or pd.isna(r.close) or pd.isna(r.max):
            continue
        ref = round(float(r.close) - float(r.spread), 2)
        if ref <= 0:
            continue
        lp = limit_up_price(ref)
        if abs(float(r.close) - lp) < 1e-6 and abs(float(r.max) - float(r.close)) < 1e-6:
            out[str(r.stock_id)] = (lp, float(r.Trading_Volume))
    return out


# ---------- 分點日報 ----------


def _one_report(args):
    sid, day = args
    try:
        return sid, finmind_query("TaiwanStockTradingDailyReport", sid, day, day, quiet=True)
    except Exception as e:  # noqa: BLE001
        print(f"    {sid} 分點日報失敗: {e}")
        return sid, None


def fetch_broker_reports(conn, day, universe, limit_up=None, extra_by_stock=None, workers=WORKERS):
    """逐檔查分點日報,存追蹤分點的『每分點每股每天』加總,以及漲停價成交量。

    FinMind 的原始資料是「每個分點、每個價位一列」(同一分點同一檔一天可能有 5~10 列),
    所以金額必須先 買進股數×價格 逐列算出來再加總,不能用主鍵蓋掉。
    一次查詢會回傳這檔股票所有分點的明細,追蹤幾個分點都不會多花額度。

    limit_up: {stock_id: (漲停價, 總成交股數)},這些股票要另外算漲停價位的成交量(策略三)。
    extra_by_stock: {stock_id: {額外要存的分點代號}},地緣分點/庫藏股分點用(第二階段)。
    """
    limit_up = limit_up or {}
    extra_by_stock = extra_by_stock or {}
    n_rows = n_empty = 0
    jobs = [(sid, day) for sid in universe]

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, (sid, df) in enumerate(ex.map(_one_report, jobs), 1):
            if df is None or df.empty or "securities_trader_id" not in df.columns:
                n_empty += 1
                continue
            df = df.copy()
            for c in ("price", "buy", "sell"):
                df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

            watched = ALL_WATCHED_TRADER_IDS | extra_by_stock.get(sid, set())
            w = df[df["securities_trader_id"].isin(watched)]
            if len(w):
                g = (
                    w.assign(buy_amt=w["buy"] * w["price"], sell_amt=w["sell"] * w["price"])
                    .groupby("securities_trader_id")[["buy", "sell", "buy_amt", "sell_amt"]]
                    .sum()
                )
                conn.executemany(
                    "INSERT OR REPLACE INTO daily_dama VALUES (?,?,?,?,?,?,?)",
                    [
                        (day, sid, tid, float(r["buy"]), float(r["sell"]), float(r["buy_amt"]), float(r["sell_amt"]))
                        for tid, r in g.iterrows()
                    ],
                )
                n_rows += len(g)

            if sid in limit_up:
                lp, total_vol = limit_up[sid]
                vol = float(df.loc[(df["price"] - lp).abs() < 1e-6, "buy"].sum())
                conn.execute(
                    "INSERT OR REPLACE INTO daily_limitup VALUES (?,?,?,?,?)",
                    (day, sid, lp, vol, total_vol),
                )

            if i % 50 == 0:
                conn.commit()
                print(f"  分點日報進度 {i}/{len(jobs)}")
    conn.commit()
    print(
        f"  {day} 分點日報:查了 {len(jobs)} 檔({n_empty} 檔沒資料),存了 {n_rows} 筆"
        f"『分點×股票』加總,追蹤 {len(ALL_WATCHED_TRADER_IDS)} 個分點"
    )


# ---------- 月營收 ----------


def _previous_trading_day(conn, day):
    row = conn.execute("SELECT MAX(date) FROM daily_price WHERE date < ?", (day,)).fetchone()
    return row[0] if row and row[0] else None


def ensure_revenue_history(conn, stock_ids):
    """資料庫裡還沒有月營收歷史的股票,一次補抓全部歷史(FinMind 有資料以來)。"""
    have = {r[0] for r in conn.execute("SELECT DISTINCT stock_id FROM month_revenue")}
    need = [s for s in stock_ids if s not in have]
    if not need:
        return
    print(f"  月營收歷史:{len(need)} 檔尚未建立,補抓中…")
    end = date.today().isoformat()

    def one(sid):
        return sid, finmind_query("TaiwanStockMonthRevenue", sid, "2000-01-01", end, quiet=True)

    rows = []
    for res in parallel_map(one, need, workers=WORKERS):
        if res is None:
            continue
        sid, df = res
        for r in df.itertuples():
            ct = str(getattr(r, "create_time", "") or "")[:10] or str(r.date)[:10]
            rows.append((sid, int(r.revenue_year), int(r.revenue_month), float(r.revenue), ct, ct))
    conn.executemany("INSERT OR IGNORE INTO month_revenue VALUES (?,?,?,?,?,?)", rows)
    conn.commit()
    print(f"  月營收歷史:新增 {len(rows)} 筆")


def fetch_month_revenue(conn, day, big_ids):
    """每日檢查月營收有沒有新公布。

    FinMind 月營收的 date 欄是『營收月份的下個月 1 號』(例如 8 月營收 date=9/1),
    create_time 是 FinMind 入庫日(約等於公布日,但不是精確的公布時間)。
    所以用「每日快照比對」:資料庫裡原本沒有、而且入庫日不早於上一個交易日的,
    就當作『我們今天才看到』(first_seen = 今天)。這樣晚上才公布的公司,隔天也不會漏掉。
    """
    ensure_revenue_history(conn, sorted(big_ids))
    d = date.fromisoformat(day)
    this_key = d.replace(day=1)
    last_key = (this_key - timedelta(days=1)).replace(day=1)
    prev_day = _previous_trading_day(conn, day) or day

    existing = {
        (r[0], r[1], r[2])
        for r in conn.execute("SELECT stock_id, revenue_year, revenue_month FROM month_revenue")
    }
    n_new = 0
    for key in (last_key, this_key):
        df = finmind_query("TaiwanStockMonthRevenue", None, key.isoformat(), key.isoformat(), quiet=True)
        if df.empty:
            continue
        df = df[df["stock_id"].astype(str).isin(big_ids)]
        for r in df.itertuples():
            k = (str(r.stock_id), int(r.revenue_year), int(r.revenue_month))
            if k in existing:
                continue
            ct = str(getattr(r, "create_time", "") or "")[:10] or day
            first_seen = day if ct >= prev_day else ct
            conn.execute(
                "INSERT OR IGNORE INTO month_revenue VALUES (?,?,?,?,?,?)",
                (k[0], k[1], k[2], float(r.revenue), ct, first_seen),
            )
            n_new += 1
    conn.commit()
    print(f"  月營收:新看到 {n_new} 筆(市值>500億的公司)")


# ---------- 整體流程 ----------


def _extra_by_stock(conn, big):
    """策略七需要額外存的分點:{stock_id: {trader_id}}(同區分點只含市值>500億的公司;庫藏股分點不限市值)。"""
    out = {}
    for sid, traders in same_district_brokers(conn).items():
        if sid in big and traders:
            out.setdefault(sid, set()).update(traders)
    for sid, traders in buyback_brokers_by_stock().items():  # 庫藏股分點:不限市值
        out.setdefault(sid, set()).update(traders)
    return out


def fetch_extra_only(conn, day):
    """只補抓策略七(同區/庫藏股分點)涉及的股票的分點日報,不重抓行情、法人、營收。
    用在『新增了要追蹤的分點後,補存過去幾天的資料』。"""
    mv, _ = get_market_values_latest(conn, day)
    if not mv:
        print(f"  {day} 資料庫裡沒有市值,無法判斷範圍")
        return False
    big = {s for s, v in mv.items() if v > BIG_MARKET_VALUE}
    extra = _extra_by_stock(conn, big)
    print(f"=== 補抓 {day}:策略七涉及 {len(extra)} 檔 ===")
    fetch_broker_reports(conn, day, sorted(extra), extra_by_stock=extra)
    return True


# ---------- 融資餘額 / 借券賣出餘額(策略 D、E)----------


def _weekdays_back(day, n_calendar=14):
    d0 = date.fromisoformat(day)
    return [(d0 - timedelta(days=i)).isoformat() for i in range(n_calendar, -1, -1)
            if (d0 - timedelta(days=i)).weekday() < 5]


def fetch_margin(conn, day):
    """融資融券日資料(全市場一次查)。順便補最近兩週缺的交易日(近5日融資變化要用)。"""
    valid = get_valid_stock_ids(conn)
    have = {r[0] for r in conn.execute("SELECT DISTINCT date FROM margin_daily")}
    got = 0
    for d in _weekdays_back(day):
        if d in have and d != day:
            continue
        df = finmind_query("TaiwanStockMarginPurchaseShortSale", None, d, d, quiet=True)
        if len(df) == 0:
            continue
        df = df[df["stock_id"].astype(str).isin(valid)]
        conn.executemany(
            "INSERT OR REPLACE INTO margin_daily VALUES (?,?,?,?,?,?)",
            [(d, str(r.stock_id), float(r.MarginPurchaseTodayBalance), float(r.MarginPurchaseYesterdayBalance),
              float(r.MarginPurchaseLimit), float(r.ShortSaleTodayBalance)) for r in df.itertuples()],
        )
        conn.commit()
        got += 1
    ok = conn.execute("SELECT 1 FROM margin_daily WHERE date=? LIMIT 1", (day,)).fetchone() is not None
    print(f"  融資融券:新補 {got} 天;{day} {'有' if ok else '沒有'}資料")
    return ok


def fetch_short_balance(conn, day):
    """借券賣出餘額日資料(全市場一次查)。"""
    valid = get_valid_stock_ids(conn)
    have = {r[0] for r in conn.execute("SELECT DISTINCT date FROM short_balance_daily")}
    got = 0
    for d in _weekdays_back(day):
        if d in have and d != day:
            continue
        df = finmind_query("TaiwanDailyShortSaleBalances", None, d, d, quiet=True)
        if len(df) == 0:
            continue
        df = df[df["stock_id"].astype(str).isin(valid)]
        conn.executemany(
            "INSERT OR REPLACE INTO short_balance_daily VALUES (?,?,?,?,?)",
            [(d, str(r.stock_id), float(r.SBLShortSalesCurrentDayBalance), float(r.SBLShortSalesPreviousDayBalance),
              float(r.SBLShortSalesQuota)) for r in df.itertuples()],
        )
        conn.commit()
        got += 1
    ok = conn.execute("SELECT 1 FROM short_balance_daily WHERE date=? LIMIT 1", (day,)).fetchone() is not None
    print(f"  借券賣出餘額:新補 {got} 天;{day} {'有' if ok else '沒有'}資料")
    return ok


# ---------- 集保股權分散(策略 F,每週)----------

# 集保級距(股)。大戶 = 1,000 張以上 = 'more than 1,000,001' 這一級;散戶 = 400 張以下 = 前 11 級
RETAIL_LEVELS = {"1-999", "1,000-5,000", "5,001-10,000", "10,001-15,000", "15,001-20,000", "20,001-30,000",
                 "30,001-40,000", "40,001-50,000", "50,001-100,000", "100,001-200,000", "200,001-400,000"}
BIG_LEVELS = {"more than 1,000,001"}


def fetch_holding_dist(conn, day):
    """檢查有沒有新的一週集保資料。FinMind 一次查全市場某一天(沒資料的日期回傳空),所以從上次存的日期
    往後逐日查到今天;第一次執行(資料庫是空的)往前查 35 天補歷史,這批歷史標 first_seen='baseline',
    不算『今天新入庫』。回傳這次新入庫的資料日期清單。"""
    valid = get_valid_stock_ids(conn)
    last = conn.execute("SELECT MAX(date) FROM holding_dist_weekly").fetchone()[0]
    first_run = last is None
    d0 = date.fromisoformat(day)
    d = (date.fromisoformat(last) + timedelta(days=1)) if last else d0 - timedelta(days=35)
    new = []
    while d <= d0:
        if d.weekday() < 5:
            df = finmind_query("TaiwanStockHoldingSharesPer", None, d.isoformat(), d.isoformat(), quiet=True)
            if len(df):
                df = df[df["stock_id"].astype(str).isin(valid)]
                big = df[df["HoldingSharesLevel"].isin(BIG_LEVELS)].groupby("stock_id")["percent"].sum()
                retail = df[df["HoldingSharesLevel"].isin(RETAIL_LEVELS)].groupby("stock_id")["percent"].sum()
                both = pd.concat([big.rename("big"), retail.rename("retail")], axis=1).dropna()
                tag = "baseline" if first_run else day
                conn.executemany(
                    "INSERT OR REPLACE INTO holding_dist_weekly VALUES (?,?,?,?,?)",
                    [(d.isoformat(), str(sid), float(r.big), float(r.retail), tag) for sid, r in both.iterrows()],
                )
                conn.commit()
                new.append(d.isoformat())
        d += timedelta(days=1)
    print(f"  集保股權分散:新入庫的資料日 {new or '無'}")
    return new


# ---------- 季報損益表(策略 G)----------


def _quarter_ends(day, n=6):
    d = date.fromisoformat(day)
    cands = []
    for yy in (d.year, d.year - 1, d.year - 2):
        for mm, dd in [(3, 31), (6, 30), (9, 30), (12, 31)]:
            qd = date(yy, mm, dd)
            if qd < d:
                cands.append(qd)
    return sorted(cands, reverse=True)[:n]


def fetch_financials(conn, day):
    """季報損益表(營收、毛利、營業利益)。FinMind 沒有『入庫日』欄位,所以自己記:每天重新查最近
    一個季度的全市場資料(財報公布期間),資料庫裡還沒有的股票就是『今天新入庫』(first_seen = 今天)。
    第一次(整張表是空的)把近 6 季補齊;季底超過 100 天的季度視為歷史,first_seen='baseline'。
    回傳 (今天新入庫的檔數, 略過的檔數:沒有毛利欄位,例如金融股)。"""
    valid = get_valid_stock_ids(conn)
    new_total, skipped = 0, 0
    for qd in _quarter_ends(day, 6):
        iso = qd.isoformat()
        age = (date.fromisoformat(day) - qd).days
        have = {r[0] for r in conn.execute("SELECT stock_id FROM financials_quarterly WHERE date=?", (iso,))}
        if have and age > 100:
            continue  # 老季度已經存過,不再重查
        df = finmind_query("TaiwanStockFinancialStatements", None, iso, iso, quiet=True)
        if len(df) == 0:
            continue
        df = df[df["stock_id"].astype(str).isin(valid) & df["type"].isin(["Revenue", "GrossProfit", "OperatingIncome"])]
        piv = df.pivot_table(index="stock_id", columns="type", values="value", aggfunc="first")
        tag = "baseline" if age > 100 else day
        rows = []
        for sid, r in piv.iterrows():
            sid = str(sid)
            if sid in have:
                continue
            rev, gp, oi = r.get("Revenue"), r.get("GrossProfit"), r.get("OperatingIncome")
            if pd.isna(rev) or pd.isna(gp):
                skipped += 1
                continue
            rows.append((iso, sid, float(rev), float(gp), None if pd.isna(oi) else float(oi), tag))
        conn.executemany("INSERT OR IGNORE INTO financials_quarterly VALUES (?,?,?,?,?,?)", rows)
        conn.commit()
        if tag == day:
            new_total += len(rows)
    print(f"  季報損益表:今天新入庫 {new_total} 檔(略過沒有毛利欄位的 {skipped} 檔)")
    return new_total, skipped


# ---------- 內部人持股轉讓事前申報(策略 J)/ 董監質押月報(風險標記)----------


def _roc8(s):
    s = str(s or "").strip()
    if len(s) != 7 or not s.isdigit():
        return None
    return f"{int(s[:3]) + 1911}-{s[3:5]}-{s[5:7]}"


def _num(v):
    try:
        return float(str(v).strip().replace(",", "") or 0)
    except ValueError:
        return 0.0


def fetch_insider_transfer(conn, day):
    """證交所/櫃買 OpenAPI『每日內部人持股轉讓事前申報表-持股轉讓日報表』:每天只提供最近一個營業日那一份,
    所以每天抓下來存起來。回傳存了幾筆。"""
    import requests  # noqa: PLC0415

    H = {"User-Agent": "Mozilla/5.0"}
    rows = []
    for url, market, date_key, code_key, ident_key in [
        ("https://openapi.twse.com.tw/v1/opendata/t187ap12_L", "上市", "出表日期", "公司代號", "申報人身分"),
        ("https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap12_O", "上櫃", "Date", "SecuritiesCompanyCode", "申請人身分"),
    ]:
        try:
            data = _get_json(url)
        except Exception as e:  # noqa: BLE001
            print(f"  內部人轉讓申報({market})抓取失敗:{str(e)[:120]}")
            continue
        for x in data:
            d = _roc8(x.get(date_key))
            sid = str(x.get(code_key, "")).strip()
            if not d or not sid:
                continue
            rows.append((d, sid, x.get("姓名", "").strip(), x.get(ident_key, "").strip(),
                         x.get("預定轉讓方式及股數-轉讓方式", "").strip(),
                         _num(x.get("預定轉讓方式及股數-轉讓股數")), _num(x.get("目前持有股數-自有持股")),
                         _num(x.get("預定轉讓後持股-自有持股")), x.get("有效轉讓期間", "").strip(), market))
    conn.executemany("INSERT OR REPLACE INTO insider_transfer_daily VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    print(f"  內部人持股轉讓事前申報:存了 {len(rows)} 筆(出表日期 {sorted({r[0] for r in rows})})")
    return len(rows)


def fetch_pledge_monthly(conn, day):
    """董監事持股餘額明細(月報,每月更新一次):算每家公司『董事/監察人整體』的設質比。已經是最新一個月就不重抓。"""
    import requests  # noqa: PLC0415

    H = {"User-Agent": "Mozilla/5.0"}
    agg, ym = {}, None
    for url in ["https://openapi.twse.com.tw/v1/opendata/t187ap11_L",
                "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap11_O"]:
        try:
            data = _get_json(url, timeout=90)
        except Exception as e:  # noqa: BLE001
            print(f"  董監質押月報抓取失敗({url[-14:]}):{str(e)[:120]}")
            return None
        for x in data:
            title = x.get("職稱", "")
            if not ("董事" in title or "監察人" in title):  # 只算董監;經理人、大股東不算
                continue
            ym = ym or x.get("資料年月")
            a = agg.setdefault(str(x.get("公司代號", "")).strip(), [0.0, 0.0])
            a[0] += _num(x.get("目前持股"))
            a[1] += _num(x.get("設質股數"))
    if not agg or not ym:
        return None
    latest = conn.execute("SELECT MAX(ym) FROM insider_pledge_monthly").fetchone()[0]
    if latest and latest >= ym:
        return latest
    conn.execute("DELETE FROM insider_pledge_monthly")
    conn.executemany(
        "INSERT OR REPLACE INTO insider_pledge_monthly VALUES (?,?,?,?,?,?)",
        [(ym, sid, h, p, (p / h if h else 0.0), day) for sid, (h, p) in agg.items()],
    )
    conn.commit()
    print(f"  董監質押月報:{ym},{len(agg)} 家公司")
    return ym


def fetch_month_revenue_all(conn, day, keep_months=15):
    """全市場月營收(近 15 個月)。FinMind 月營收 date = 營收月份的下個月 1 號,全市場一次查。
    最近兩個月每天重查一次(公布期間會陸續進來),其餘月份只補缺的。"""
    valid = get_valid_stock_ids(conn)
    d = date.fromisoformat(day)
    y, m = d.year, d.month  # 目前這個月的營收要下個月才有,所以最新可能的營收月 = 上個月
    months = []
    for i in range(1, keep_months + 1):
        mm = m - i
        yy = y
        while mm <= 0:
            mm += 12
            yy -= 1
        months.append((yy, mm))
    have = {r[0] for r in conn.execute("SELECT DISTINCT ym FROM month_revenue_all")}
    n_new = 0
    for idx, (yy, mm) in enumerate(months):
        ym = yy * 100 + mm
        if ym in have and idx >= 2:
            continue
        ny, nm = (yy, mm + 1) if mm < 12 else (yy + 1, 1)
        df = finmind_query("TaiwanStockMonthRevenue", None, f"{ny}-{nm:02d}-01", f"{ny}-{nm:02d}-01", quiet=True)
        if len(df) == 0:
            continue
        df = df[df["stock_id"].astype(str).isin(valid)]
        conn.executemany(
            "INSERT OR REPLACE INTO month_revenue_all VALUES (?,?,?)",
            [(str(r.stock_id), ym, float(r.revenue)) for r in df.itertuples()],
        )
        n_new += len(df)
    conn.execute("DELETE FROM month_revenue_all WHERE ym < ?", (months[-1][0] * 100 + months[-1][1],))
    conn.commit()
    print(f"  全市場月營收:寫入/更新 {n_new} 筆")
    return n_new


def do_fetch(conn, day):
    """抓某一天全部要用的資料。回傳 False 代表那天沒有行情(假日),不用往下做。
    市值門檻決定後面要查哪些股票的分點日報;當天的市值表還沒更新就用前一個交易日的。"""
    print(f"=== 抓取 {day} 的資料 ===")
    price_df = fetch_price(conn, day)
    if price_df is None or len(price_df) == 0:
        return False

    fetch_market_value(conn, day)  # 當天的市值表常常還沒更新,沒有就用前一個交易日的
    fetch_institutional(conn, day)

    mv, mv_date = get_market_values_latest(conn, day)
    big = {s for s, v in mv.items() if v > BIG_MARKET_VALUE} if mv else set()
    if mv and mv_date != day:
        print(f"  今天的市值表還沒更新,市值門檻用 {mv_date} 的市值表")
    print(f"  市值 > 500 億的普通股:{len(big)} 檔" if mv else "  (完全沒有市值資料,市值門檻的策略會顯示今日無資料)")

    universe = (
        price_df.sort_values("Trading_money", ascending=False)
        .head(TOP_N_BY_TURNOVER)["stock_id"]
        .astype(str)
        .tolist()
    )
    limit_up = {s: v for s, v in limit_up_candidates(price_df).items() if s in big}

    # 策略七:同區分點(A)和庫藏股分點(B)也要存。公司不在成交金額前300名也要補查分點日報。
    extra_by_stock = _extra_by_stock(conn, big)

    in_universe = set(universe)
    extra = [s for s in list(limit_up) + sorted(extra_by_stock) if s not in in_universe]
    extra = list(dict.fromkeys(extra))
    print(f"  漲停且市值>500億:{len(limit_up)} 檔;策略七地緣/庫藏股分點涉及 {len(extra_by_stock)} 檔;"
          f"其中共 {len(extra)} 檔不在成交金額前 {TOP_N_BY_TURNOVER},另外補查分點日報")
    fetch_broker_reports(conn, day, universe + extra, limit_up=limit_up, extra_by_stock=extra_by_stock)

    if big:
        fetch_month_revenue(conn, day, big)

    # 第二批策略用的資料:每一項都能單獨失敗,不影響主流程
    for name, fn in [
        ("融資融券", fetch_margin),
        ("借券賣出餘額", fetch_short_balance),
        ("集保股權分散", fetch_holding_dist),
        ("全市場月營收", fetch_month_revenue_all),
        ("季報損益表", fetch_financials),
        ("內部人轉讓申報", fetch_insider_transfer),
        ("董監質押月報", fetch_pledge_monthly),
    ]:
        try:
            fn(conn, day)
        except Exception as e:  # noqa: BLE001
            print(f"  {name} 抓取失敗:{str(e)[:100]}")

    prune(conn)
    return True
