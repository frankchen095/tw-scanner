"""
fetchers.py —— 抓資料的共用函式,每天收盤後由 main.py / 02_fetch_daily.py 呼叫

只存「普通股」(排除 ETF、ETN、權證、興櫃、存託憑證),避免資料庫塞進一堆用不到的
資料、檔案爆大(GitHub 單檔上限 100MB)。

你不需要動這個檔案。
"""

import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import finmind_query  # noqa: E402
from db import ALL_WATCHED_TRADER_IDS, prune  # noqa: E402
from helpers import (  # noqa: E402
    BIG_MARKET_VALUE,
    get_market_values,
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


def do_fetch(conn, day):
    """抓某一天全部要用的資料。回傳 False 代表那天沒有行情(假日),不用往下做。"""
    print(f"=== 抓取 {day} 的資料 ===")
    price_df = fetch_price(conn, day)
    if price_df is None or len(price_df) == 0:
        return False

    fetch_market_value(conn, day)
    fetch_institutional(conn, day)

    mv = get_market_values(conn, day)
    big = {s for s, v in mv.items() if v > BIG_MARKET_VALUE} if mv else set()
    print(f"  市值 > 500 億的普通股:{len(big)} 檔" if mv else "  (今天沒有市值資料,市值門檻的策略會顯示今日無資料)")

    universe = (
        price_df.sort_values("Trading_money", ascending=False)
        .head(TOP_N_BY_TURNOVER)["stock_id"]
        .astype(str)
        .tolist()
    )
    limit_up = {s: v for s, v in limit_up_candidates(price_df).items() if s in big}
    extra = [s for s in limit_up if s not in set(universe)]
    print(f"  漲停且市值>500億:{len(limit_up)} 檔(其中 {len(extra)} 檔不在成交金額前 {TOP_N_BY_TURNOVER},另外補查)")
    fetch_broker_reports(conn, day, universe + extra, limit_up=limit_up)

    if big:
        fetch_month_revenue(conn, day, big)

    prune(conn)
    return True
