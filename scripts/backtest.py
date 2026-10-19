"""
backtest.py —— 歷史回測:重播每個策略,看各自的勝率(L 項)

核心原則:**直接重用 scripts/reports/ 裡每個策略的 build()**,一天一天把 Context 的「今天」設成歷史日期 t,
產出當天會推播的名單;不另外寫一份簡化版的策略邏輯。訊號存進 backtest_signals(跟正式推播的 signals 分開),
報酬算法跟成效追蹤(tracking.py)一樣:進場 = 訊號隔一個交易日開盤、5日/20日收盤出場(進場日算第1天)、
還原價、策略三等空方榜反向、大盤對照用 TAIEX 同區間。

資料放在獨立的 data/backtest.db(很大,不進 repo,不會被每日排程碰到)。

用法:
  python scripts/backtest.py --plan --from 2024-01-02 --to 2026-10-16     # 盤點資料缺口、估算 FinMind 請求次數與時間(不抓)
  python scripts/backtest.py --init                                       # 建立 backtest.db,從 scanner.db 複製小表(股票名單、地址…)
  python scripts/backtest.py --backfill --yes --from ... --to ...         # 實際回補(要加 --yes;可分批、可中斷再續跑)
  python scripts/backtest.py --import-chips --yes --from ... --to ...     # 從 fenpoint/chips.db 匯入分點資料(不花 API)
  python scripts/backtest.py --run --from ... --to ... [--strategies s1,s2,...]   # 重播 + 算報酬 + 出報告
  python scripts/backtest.py --report --from ... --to ...                 # 只重新產生報告(CSV + summary.md)

能測 / 不能測(也寫在報告開頭):
  能測:策略一、三、四、五、八、九、交集榜;策略二、七在有分點資料的日子能測(只有 fenpoint 的 400 檔,到 chips.db 最後一天)。
  不能測:策略六(AI 時事)、負面新聞(RSS 只有近期)、質押/內部人轉讓(沒有歷史)、風險標記裡的法說會(抓不到歷史)。
偏誤處理:策略二的前40大分點名單是用 fenpoint 2023–2024 資料篩出來的 → 分開報告 2024(樣本內)和 2025 至今(樣本外),
只有樣本外算數;股票池用現在的 TaiwanStockInfo(已下市的不在,存活者偏誤,市值>500億影響小);市值門檻用當天的市值;
交易日用 TAIEX;交集榜另算『只算首次出現那天』的版本。
"""

import argparse
import bisect
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "reports"))
import db as livedb  # noqa: E402
import helpers  # noqa: E402
from common import finmind_query  # noqa: E402
from context import Context  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BT_PATH = ROOT / "data" / "backtest.db"
CHIPS_PATH = ROOT.parent / "fenpoint" / "chips.db"
HORIZONS = (5, 20)
RANDOM_REPS = 100
MIN_N = 30

# 群組名 → 要記錄的 tracking 策略 key
GROUPS = {
    "s1": ["s1"], "s5": ["s5"], "s2": ["s2_buy", "s2_buy3"], "s3": ["s3"], "s4": ["s4"], "s7": ["s7"],
    "s8": ["s8_1", "s8_5"], "s9": ["s9_trust", "s9_foreign"], "intersection": ["intersection"],
}
# 群組 → 需要先跑哪些模組(交集榜要先跑 s1/s2/s5/s7/s8/s9;s3 的結果也要拿來標衝突)
MODULES = {
    "s1": "s1_s5_highs", "s5": "s1_s5_highs", "s2": "s2_brokers", "s3": "s3_limit_up", "s4": "s4_revenue",
    "s7": "s7_geo_brokers", "s8": "s8_s9_institutional", "s9": "s8_s9_institutional",
}
NEEDS_FOR_INTERSECTION = ["s1", "s2", "s3", "s5", "s7", "s8", "s9"]
SHORT_KEYS = {"s3"}
# 沒有分點資料就不能測的群組
BROKER_GROUPS = {"s2", "s3", "s7"}


# ---------------------------------------------------------------- 資料庫

def connect_bt(path=None):
    path = Path(path) if path else BT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(livedb.SCHEMA)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS adj_price (stock_id TEXT, date TEXT, open REAL, close REAL, PRIMARY KEY (stock_id, date));
        CREATE TABLE IF NOT EXISTS index_price (date TEXT PRIMARY KEY, open REAL, close REAL);
        CREATE TABLE IF NOT EXISTS bt_skipped (date TEXT, strategy TEXT, reason TEXT);
        """
    )
    return conn


def init_from_live(conn):
    """從 scanner.db 複製不隨日期變動的小表:股票名單、公司/分點地址、庫藏股計畫"""
    conn.execute(f"ATTACH DATABASE '{livedb.DB_PATH}' AS live")
    for t in ("stock_info", "company_geo", "broker_geo", "buyback_programs", "shares_outstanding"):
        try:
            conn.execute(f"DELETE FROM main.{t}")
            conn.execute(f"INSERT INTO main.{t} SELECT * FROM live.{t}")
        except sqlite3.Error as e:
            print(f"  (複製 {t} 失敗:{e})")
    conn.commit()
    conn.execute("DETACH DATABASE live")
    print("  已從 scanner.db 複製股票名單、地址對照、庫藏股計畫")


def trading_days(conn, d_from, d_to):
    """用 TAIEX 的交易日;backtest.db 沒有就回傳空(要先 --backfill)"""
    return [r[0] for r in conn.execute(
        "SELECT date FROM index_price WHERE date >= ? AND date <= ? ORDER BY date", (d_from, d_to))]


# ---------------------------------------------------------------- 盤點與估算

TABLE_DATE = {
    "daily_price": "date", "daily_market_value": "date", "daily_institutional": "date", "margin_daily": "date",
    "short_balance_daily": "date", "daily_dama": "date", "daily_limitup": "date", "adj_price": "date",
    "index_price": "date", "month_revenue": "first_seen",
}


def coverage(conn):
    out = {}
    for t, col in TABLE_DATE.items():
        try:
            r = conn.execute(f"SELECT MIN({col}), MAX({col}), COUNT(DISTINCT {col}), COUNT(*) FROM {t}").fetchone()
        except sqlite3.Error:
            r = (None, None, 0, 0)
        out[t] = r
    return out


def weekdays(d_from, d_to):
    d, out = date.fromisoformat(d_from), []
    end = date.fromisoformat(d_to)
    while d <= end:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def plan(conn, d_from, d_to):
    days = weekdays(d_from, d_to)
    n = len(days)  # 週一~五的天數(含國定假日,實際交易日約少 3%)
    live_n = conn.execute("SELECT COUNT(*) FROM stock_info WHERE is_common=1").fetchone()[0] or 1850
    cov = coverage(conn)
    have = lambda t: cov[t][2] or 0  # noqa: E731
    items = [
        ("股價 TaiwanStockPrice(全市場每天1次)", n, have("daily_price")),
        ("市值 TaiwanStockMarketValue(每天1次)", n, have("daily_market_value")),
        ("法人買賣超(每天1次)", n, have("daily_institutional")),
        ("融資融券(每天1次,只有擴充策略 E 用)", n, have("margin_daily")),
        ("借券賣出餘額(每天1次,只有擴充策略 D 用)", n, have("short_balance_daily")),
        ("集保股權分散(每個平日查1次,約 20% 有資料,只有擴充策略 F 用)", n, 0),
        ("月營收 TaiwanStockMonthRevenue(全市場每月1次,從 2022-01 起算 YoY/新高)", 4 * 12 + 12, 0),
        ("季報損益表(全市場每季1次,擴充策略 G 用)", 16, 0),
        ("還原股價 TaiwanStockPriceAdj(每檔1次,整段區間)", live_n, 0),
        ("加權指數 TAIEX、股票基本資料", 2, 0),
    ]
    core = sum(a for (name, a, _b) in items[:3] + items[6:7] + items[8:10])
    ext = sum(a for (name, a, _b) in items[3:6] + items[7:8])
    print(f"\n=== 回測資料盤點與請求次數估算:{d_from} ~ {d_to}(週一~五 {n} 天)===")
    print("backtest.db 目前有的資料:")
    for t, (mn, mx, nd, nr) in cov.items():
        print(f"  {t:22s} {nd:5d} 天 {nr:9d} 列  {mn} ~ {mx}")
    print("\n需要回補的請求次數(FinMind,每次一個查詢):")
    for name, a, b in items:
        print(f"  {a:6d} 次  {name}" + (f"  (已有 {b} 天可略過)" if b else ""))
    print(f"\n核心策略(一、三、四、五、八、九、交集榜)需要約 {core:,} 次;擴充策略(D/E/F/G)另需約 {ext:,} 次。")
    print("分點日報:不打 API,從 fenpoint/chips.db(400 檔、到 2026-09-15 為止)匯入;"
          "chips.db 最後一天之後的策略二、三、七略過不測。")
    print("若要用 API 補 chips.db 之後的分點:每個交易日 × 約 300 檔 約 300 次/天。")
    print("\n時間估算:市值/股價/法人這類全市場查詢每次約 1~3 秒,還原股價每檔約 0.4 秒,4 條執行緒並行 →"
          f" 核心約 {core * 1.5 / 4 / 60:.0f}~{core * 2.5 / 4 / 60:.0f} 分鐘;含擴充約 {(core + ext) * 2.5 / 4 / 60:.0f} 分鐘。")
    print("額度:SponsorPro 到 2026-10-18 為止(每小時 26,000 次),之後降回每小時 6,000 次,"
          f"所以 {core + ext:,} 次要拆成 {-(-(core + ext) // 5000)} 個小時分批跑(程式會自己計次、接近上限就停手等下一小時)。")


# ---------------------------------------------------------------- 回補

class Quota:
    """簡單計次:每小時最多 N 次,超過就睡到下一小時"""

    def __init__(self, per_hour):
        self.per_hour, self.t0, self.n = per_hour, time.time(), 0

    def tick(self):
        if time.time() - self.t0 > 3600:
            self.t0, self.n = time.time(), 0
        if self.n >= self.per_hour:
            wait = 3600 - (time.time() - self.t0) + 5
            print(f"  (本小時額度用到 {self.n} 次,等 {wait / 60:.0f} 分鐘)")
            time.sleep(max(wait, 1))
            self.t0, self.n = time.time(), 0
        self.n += 1


def backfill(conn, d_from, d_to, extended=False, per_hour=4500, workers=4):
    """實際回補。每一步都先看資料庫有沒有,可以中斷後再跑。"""
    q = Quota(per_hour)
    init_from_live(conn)
    # 1. 加權指數(交易日曆)
    start = (date.fromisoformat(d_from) - timedelta(days=60)).isoformat()
    q.tick()
    idx = finmind_query("TaiwanStockPrice", "TAIEX", start, (date.fromisoformat(d_to) + timedelta(days=45)).isoformat(), quiet=True)
    if idx.empty:
        print("加權指數抓不到,停止")
        return
    conn.executemany("INSERT OR REPLACE INTO index_price VALUES (?,?,?)",
                     [(r.date, float(r.open), float(r.close)) for r in idx.itertuples()])
    conn.commit()
    days = [r[0] for r in conn.execute("SELECT date FROM index_price WHERE date>=? AND date<=? ORDER BY date", (d_from, d_to))]
    print(f"交易日 {len(days)} 天({days[0]} ~ {days[-1]})")
    valid = helpers.get_valid_stock_ids(conn)

    def per_day(table, dataset, saver, tag):
        have = {r[0] for r in conn.execute(f"SELECT DISTINCT date FROM {table}")}
        todo = [d for d in days if d not in have]
        print(f"[{tag}] 需要補 {len(todo)} 天")

        def one(d):
            q.tick()
            return d, finmind_query(dataset, None, d, d, quiet=True)

        done = 0
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for d, df in ex.map(one, todo):
                if len(df):
                    saver(d, df)
                done += 1
                if done % 50 == 0:
                    conn.commit()
                    print(f"  [{tag}] {done}/{len(todo)}")
        conn.commit()

    def save_price(d, df):
        df = df[df["stock_id"].astype(str).isin(valid)]
        conn.executemany(
            "INSERT OR REPLACE INTO daily_price (date, stock_id, open, high, low, close, volume, money, spread) VALUES (?,?,?,?,?,?,?,?,?)",
            [(d, str(r.stock_id), r.open, r.max, r.min, r.close, r.Trading_Volume, r.Trading_money, r.spread) for r in df.itertuples()])

    def save_mv(d, df):
        df = df[df["stock_id"].astype(str).isin(valid)]
        conn.executemany("INSERT OR REPLACE INTO daily_market_value VALUES (?,?,?)",
                         [(d, str(r.stock_id), float(r.market_value)) for r in df.itertuples()])

    def save_inst(d, df):
        df = df[df["stock_id"].astype(str).isin(valid) & df["name"].isin(["Foreign_Investor", "Investment_Trust"])]
        conn.executemany("INSERT OR REPLACE INTO daily_institutional VALUES (?,?,?,?,?)",
                         [(d, str(r.stock_id), r.name, r.buy, r.sell) for r in df.itertuples()])

    def save_margin(d, df):
        df = df[df["stock_id"].astype(str).isin(valid)]
        conn.executemany("INSERT OR REPLACE INTO margin_daily VALUES (?,?,?,?,?,?)",
                         [(d, str(r.stock_id), float(r.MarginPurchaseTodayBalance), float(r.MarginPurchaseYesterdayBalance),
                           float(r.MarginPurchaseLimit), float(r.ShortSaleTodayBalance)) for r in df.itertuples()])

    def save_sbl(d, df):
        df = df[df["stock_id"].astype(str).isin(valid)]
        conn.executemany("INSERT OR REPLACE INTO short_balance_daily "
                         "(date, stock_id, sbl_bal, sbl_prev, sbl_quota, sbl_sales, sbl_returns, sbl_adj) VALUES (?,?,?,?,?,?,?,?)",
                         [(d, str(r.stock_id), float(r.SBLShortSalesCurrentDayBalance), float(r.SBLShortSalesPreviousDayBalance),
                           float(r.SBLShortSalesQuota), float(r.SBLShortSalesShortSales), float(r.SBLShortSalesReturns),
                           float(r.SBLShortSalesAdjustments)) for r in df.itertuples()])

    per_day("daily_price", "TaiwanStockPrice", save_price, "股價")
    per_day("daily_market_value", "TaiwanStockMarketValue", save_mv, "市值")
    per_day("daily_institutional", "TaiwanStockInstitutionalInvestorsBuySell", save_inst, "法人")
    if extended:
        per_day("margin_daily", "TaiwanStockMarginPurchaseShortSale", save_margin, "融資融券")
        per_day("short_balance_daily", "TaiwanDailyShortSaleBalances", save_sbl, "借券餘額")

    # 2. 月營收:從 2022-01 起全市場逐月(first_seen 用 FinMind 的 create_time 日期)
    have_rev = {(r[0], r[1]) for r in conn.execute("SELECT DISTINCT revenue_year, revenue_month FROM month_revenue")}
    y, m = 2022, 1
    last = date.fromisoformat(d_to)
    while (y, m) <= (last.year, last.month):
        if (y, m) not in have_rev:
            ny, nm = (y, m + 1) if m < 12 else (y + 1, 1)
            q.tick()
            df = finmind_query("TaiwanStockMonthRevenue", None, f"{ny}-{nm:02d}-01", f"{ny}-{nm:02d}-01", quiet=True)
            if len(df):
                df = df[df["stock_id"].astype(str).isin(valid)]
                rows = []
                for r in df.itertuples():
                    ct = str(getattr(r, "create_time", "") or "")[:10] or f"{ny}-{nm:02d}-01"
                    rows.append((str(r.stock_id), int(r.revenue_year), int(r.revenue_month), float(r.revenue), ct, ct))
                conn.executemany("INSERT OR IGNORE INTO month_revenue VALUES (?,?,?,?,?,?)", rows)
                conn.commit()
        y, m = (y, m + 1) if m < 12 else (y + 1, 1)

    # 3. 還原股價:每檔一次整段
    have_adj = {r[0] for r in conn.execute("SELECT DISTINCT stock_id FROM adj_price")}
    todo = [s for s in sorted(valid) if s not in have_adj]
    print(f"[還原股價] 需要補 {len(todo)} 檔")
    a0 = (date.fromisoformat(d_from) - timedelta(days=120)).isoformat()
    a1 = (date.fromisoformat(d_to) + timedelta(days=60)).isoformat()

    def one(sid):
        q.tick()
        return sid, finmind_query("TaiwanStockPriceAdj", sid, a0, a1, quiet=True)

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for sid, df in ex.map(one, todo):
            if len(df) and "close" in df.columns:
                conn.executemany("INSERT OR REPLACE INTO adj_price VALUES (?,?,?,?)",
                                 [(sid, r.date, float(r.open), float(r.close)) for r in df.itertuples()])
            done += 1
            if done % 100 == 0:
                conn.commit()
                print(f"  [還原股價] {done}/{len(todo)}")
    conn.commit()
    print("回補完成")


def import_chips(conn, d_from, d_to):
    """從 fenpoint/chips.db(400 檔、每分點每價位一列)匯入:daily_dama(只存策略二/七要用的分點的每分點每股每天加總)
    和 daily_limitup(策略三:漲停價成交量)。不花 FinMind 額度。"""
    if not CHIPS_PATH.exists():
        print(f"找不到 {CHIPS_PATH}")
        return
    import geo  # noqa: PLC0415

    init_from_live(conn)
    watched = set(livedb.ALL_WATCHED_TRADER_IDS)
    for tids in livedb.buyback_brokers_by_stock().values():
        watched |= set(tids)
    for tids in geo.same_district_brokers(conn).values():
        watched |= set(tids)
    print(f"要匯入的分點 {len(watched)} 個")
    chips = sqlite3.connect(f"file:{CHIPS_PATH}?mode=ro", uri=True)
    stocks = [x.strip() for x in (CHIPS_PATH.parent / "stocks.txt").read_text(encoding="utf-8").split() if x.strip()]
    from fetchers import limit_up_candidates  # noqa: PLC0415

    px = pd.read_sql("SELECT date, stock_id, close, high AS max, spread, volume AS Trading_Volume FROM daily_price "
                     "WHERE date BETWEEN ? AND ?", conn, params=[d_from, d_to])
    limit, total_vol = {}, {}
    for d, g in px.groupby("date"):
        for sid, v in limit_up_candidates(g).items():
            limit[(d, sid)] = v
    for r in px.itertuples():
        total_vol[(r.date, r.stock_id)] = r.Trading_Volume
    n_dama = n_lim = 0
    for i, sid in enumerate(stocks, 1):
        df = pd.read_sql("SELECT date, trader_id, price, buy, sell FROM chips WHERE stock_id=? AND date BETWEEN ? AND ?",
                         chips, params=[sid, d_from, d_to])
        if df.empty:
            continue
        for c in ("price", "buy", "sell"):
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
        w = df[df["trader_id"].isin(watched)]
        if len(w):
            g = (w.assign(buy_amt=w["buy"] * w["price"], sell_amt=w["sell"] * w["price"])
                 .groupby(["date", "trader_id"])[["buy", "sell", "buy_amt", "sell_amt"]].sum().reset_index())
            conn.executemany("INSERT OR REPLACE INTO daily_dama VALUES (?,?,?,?,?,?,?)",
                             [(r.date, sid, r.trader_id, r.buy, r.sell, r.buy_amt, r.sell_amt) for r in g.itertuples()])
            n_dama += len(g)
        for d in df["date"].unique():
            lp = limit.get((d, sid))
            if lp is None:
                continue
            dd = df[df["date"] == d]
            v = float(dd.loc[(dd["price"] - lp[0]).abs() < 1e-6, "buy"].sum())
            conn.execute("INSERT OR REPLACE INTO daily_limitup VALUES (?,?,?,?,?)",
                         (d, sid, lp[0], v, total_vol.get((d, sid)) or lp[1]))
            n_lim += 1
        if i % 20 == 0:
            conn.commit()
            print(f"  chips 匯入 {i}/{len(stocks)}")
    conn.commit()
    print(f"匯入完成:daily_dama {n_dama} 列、daily_limitup {n_lim} 列")


# ---------------------------------------------------------------- 重播

def _local_adj_history_factory(conn):
    cache = {}

    def get_adj_history(stock_ids, end_date, n_rows=60, workers=4):
        out = {}
        for sid in stock_ids:
            if sid not in cache:
                cache[sid] = pd.read_sql("SELECT date, close FROM adj_price WHERE stock_id=? ORDER BY date", conn, params=[sid])
            df = cache[sid]
            d = df[df["date"] <= end_date].tail(n_rows).reset_index(drop=True)
            out[sid] = d if len(d) else None
        return out

    return get_adj_history


def replay(conn, days, groups, chips_last):
    import importlib  # noqa: PLC0415

    import intersection  # noqa: PLC0415
    from tracking import tracked_picks  # noqa: PLC0415

    want_keys = {k for g in groups for k in GROUPS[g]}
    mods = {}
    need = set(groups)
    if "intersection" in groups:
        need |= set(NEEDS_FOR_INTERSECTION)
    for g in need:
        if g in MODULES:
            mods[MODULES[g]] = importlib.import_module(MODULES[g])
    local_adj = _local_adj_history_factory(conn)
    for name in ("s1_s5_highs",):
        if name in mods:
            mods[name].get_adj_history = local_adj
    conn.execute("DELETE FROM backtest_signals WHERE date >= ? AND date <= ?", (days[0], days[-1]))
    skipped = {}
    n = 0
    for i, t in enumerate(days, 1):
        ctx = Context(conn, t, None)
        res = {}
        broker_ok = chips_last is not None and t <= chips_last
        for mname, mod in mods.items():
            if mname in ("s2_brokers", "s3_limit_up", "s7_geo_brokers") and not broker_ok:
                skipped.setdefault("無分點資料", []).append(t)
                continue
            try:
                res.update(mod.build(ctx))
            except Exception as e:  # noqa: BLE001
                skipped.setdefault(f"{mname} 執行失敗", []).append(t)
                print(f"  {t} {mname} 失敗:{str(e)[:80]}")
        if ctx.mv is None:
            skipped.setdefault("沒有市值表", []).append(t)
            continue
        if "intersection" in groups:
            res["intersection"] = intersection.build(ctx, res)
        rows = [(t, k, sid, d) for k, sid, d in tracked_picks(res) if k in want_keys]
        conn.executemany("INSERT OR IGNORE INTO backtest_signals VALUES (?,?,?,?)", rows)
        n += len(rows)
        if i % 20 == 0:
            conn.commit()
            print(f"  重播 {i}/{len(days)} ({t}) 累計訊號 {n}")
    conn.commit()
    return n, skipped


# ---------------------------------------------------------------- 報酬

def compute_returns(conn, d_from, d_to):
    sig = pd.read_sql("SELECT date, strategy, stock_id, direction FROM backtest_signals WHERE date BETWEEN ? AND ?",
                      conn, params=[d_from, d_to])
    if sig.empty:
        return pd.DataFrame()
    idx = pd.read_sql("SELECT date, open, close FROM index_price ORDER BY date", conn)
    dates = idx["date"].tolist()
    pos = {d: i for i, d in enumerate(dates)}
    iopen, iclose = dict(zip(idx["date"], idx["open"])), dict(zip(idx["date"], idx["close"]))
    adj = pd.read_sql("SELECT stock_id, date, open, close FROM adj_price", conn)
    by = {sid: g.set_index("date")[["open", "close"]] for sid, g in adj.groupby("stock_id")}
    out = []
    for r in sig.itertuples():
        e = bisect.bisect_right(dates, r.date)
        if e >= len(dates):
            continue
        p = by.get(r.stock_id)
        if p is None:
            continue
        ed = dates[e]
        for h in HORIZONS:
            if e + h - 1 >= len(dates):
                continue
            xd = dates[e + h - 1]
            if ed not in p.index or xd not in p.index:
                continue
            entry, exit_ = float(p.loc[ed, "open"]), float(p.loc[xd, "close"])
            if entry <= 0 or exit_ <= 0:
                continue
            ret, idx_ret = exit_ / entry - 1, float(iclose[xd]) / float(iopen[ed]) - 1
            if r.direction == "short":
                ret, idx_ret = -ret, -idx_ret
            out.append((r.date, r.strategy, r.stock_id, h, ed, entry, xd, exit_, ret, idx_ret))
    df = pd.DataFrame(out, columns=["date", "strategy", "stock_id", "horizon", "entry_date", "entry_price",
                                    "exit_date", "exit_price", "ret", "index_ret"])
    conn.execute("DELETE FROM backtest_returns WHERE date BETWEEN ? AND ?", (d_from, d_to))
    conn.executemany("INSERT OR REPLACE INTO backtest_returns VALUES (?,?,?,?,?,?,?,?,?,?)", out)
    conn.commit()
    return df


def random_baseline(conn, ret_df, d_from, d_to, seed=7):
    """同市值級距隨機抽樣基準:每天、每個策略,從當天市值>500億的股票隨機抽跟訊號數一樣多的檔數,
    用跟策略一樣的進出場規則算報酬,重複 RANDOM_REPS 次取平均。回傳 DataFrame(date, strategy, horizon, base_ret, n)"""
    rng = np.random.default_rng(seed)
    idx = pd.read_sql("SELECT date, open, close FROM index_price ORDER BY date", conn)
    dates = idx["date"].tolist()
    adj = pd.read_sql("SELECT stock_id, date, open, close FROM adj_price", conn)
    by = {sid: g.set_index("date")[["open", "close"]] for sid, g in adj.groupby("stock_id")}
    sig = pd.read_sql("SELECT date, strategy, COUNT(*) n, MAX(direction) direction FROM backtest_signals "
                      "WHERE date BETWEEN ? AND ? GROUP BY date, strategy", conn, params=[d_from, d_to])
    rows = []
    cache = {}
    for d, g in sig.groupby("date"):
        mv = pd.read_sql("SELECT stock_id FROM daily_market_value WHERE date=? AND market_value>?", conn,
                         params=[d, helpers.BIG_MARKET_VALUE])
        pool = mv["stock_id"].tolist()
        e = next((i for i, x in enumerate(dates) if x > d), None)
        if not pool or e is None:
            continue
        rets = {}
        for h in HORIZONS:
            if e + h - 1 >= len(dates):
                continue
            ed, xd = dates[e], dates[e + h - 1]
            arr = []
            for sid in pool:
                p = by.get(sid)
                if p is None or ed not in p.index or xd not in p.index:
                    arr.append(np.nan)
                    continue
                o, c = float(p.loc[ed, "open"]), float(p.loc[xd, "close"])
                arr.append(c / o - 1 if o > 0 and c > 0 else np.nan)
            rets[h] = np.array(arr)
        for r in g.itertuples():
            for h, arr in rets.items():
                ok = arr[~np.isnan(arr)]
                if len(ok) < r.n:
                    continue
                means = [rng.choice(ok, size=r.n, replace=False).mean() for _ in range(RANDOM_REPS)]
                b = float(np.mean(means))
                rows.append((d, r.strategy, h, -b if r.direction == "short" else b, r.n))
    return pd.DataFrame(rows, columns=["date", "strategy", "horizon", "base_ret", "n"])


# ---------------------------------------------------------------- 報告

def _stats(g):
    return pd.Series({"n": len(g), "mean": g["ret"].mean(), "win": (g["ret"] > 0).mean(), "index": g["index_ret"].mean()})


def summarize(conn, ret_df, base_df, d_from, d_to, skipped, chips_last):
    lines = [f"# 歷史回測 {d_from} ~ {d_to}", ""]
    lines += [
        "**能測**:策略一、三、四、五、八、九、交集榜;策略二、七、三(需要分點日報)只有 fenpoint 的 400 檔、"
        f"到 chips.db 最後一天({chips_last or '無'})為止。",
        "**不能測**(不假裝測):策略六(AI 時事)、負面新聞掃描(RSS 只有近期)、質押/內部人轉讓(沒有歷史)、風險標記裡的法說會(抓不到歷史)。",
        "",
        "**偏誤處理**:①策略二的前40大分點名單是用 fenpoint 2023–2024 資料篩出來的,拿來回測 2024 等於用答案考試,"
        "所以策略二分開報 2024(樣本內)和 2025 至今(樣本外),**只有樣本外的數字算數**;"
        "②股票池用現在的股票名單,已下市的不在裡面(存活者偏誤;市值>500億影響小);"
        "③市值門檻用當天的市值;④交易日用 TAIEX;⑤交集榜同一檔連續幾天出現各算一筆,樣本不獨立,另算『只算首次出現那天』的版本。",
        "",
        "報酬算法:進場 = 訊號隔一個交易日開盤、5日/20日 = 進場日算第1天的第5/第20個交易日收盤、還原股價;"
        "策略三(放空)報酬和大盤對照都反向;隨機基準 = 每天從當天市值>500億的股票隨機抽跟訊號數一樣多的檔數、"
        f"重複 {RANDOM_REPS} 次取平均。樣本數 N < {MIN_N} 標「樣本不足」。",
        "",
    ]
    if skipped:
        lines.append("**略過的日子**(不補、不估):")
        for reason, ds in skipped.items():
            lines.append(f"- {reason}:{len(ds)} 天" + (f"({ds[0]} ~ {ds[-1]})" if ds else ""))
        lines.append("")
    if ret_df.empty:
        lines.append("沒有任何訊號有成績(資料不足)。")
        return "\n".join(lines)
    ret_df = ret_df.copy()
    ret_df["year"] = ret_df["date"].str[:4]
    def base_for(label, h, dates):
        """隨機基準:只取『這組訊號出現過的日期』的那幾天(依訊號檔數加權),所以分年/樣本內外的表各有自己的基準"""
        if base_df.empty:
            return np.nan
        under = label.split("(")[0]
        b = base_df[(base_df["strategy"] == under) & (base_df["horizon"] == h) & (base_df["date"].isin(dates))]
        return float(np.average(b["base_ret"], weights=b["n"])) if len(b) else np.nan

    def table(sel, title):
        rows = []
        for (k, h), g in sel.groupby(["strategy", "horizon"]):
            s = _stats(g)
            b = base_for(k, h, set(g["date"]))
            rows.append((k, h, int(s["n"]), s["mean"], s["win"], s["index"], s["mean"] - s["index"], b))
        out = [f"## {title}", "", "| 策略 | 天數 | 訊號數 | 平均報酬 | 勝率 | 同期大盤 | 超額報酬 | 隨機抽樣基準 |", "|---|---|---|---|---|---|---|---|"]
        for k, h, n, m, w, ix, ex, b in sorted(rows):
            flag = "(樣本不足)" if n < MIN_N else ""
            out.append(f"| {k} | {h}日 | {n}{flag} | {m * 100:+.2f}% | {w * 100:.0f}% | {ix * 100:+.2f}% | {ex * 100:+.2f}% | "
                       + ("-" if pd.isna(b) else f"{b * 100:+.2f}%") + " |")
        return out + [""]

    lines += table(ret_df, "各策略全期間(策略二請看下面分年的樣本外那一列)")
    for y in sorted(ret_df["year"].unique()):
        lines += table(ret_df[ret_df["year"] == y], f"{y} 年")
    # 策略二:樣本內外
    s2 = ret_df[ret_df["strategy"].isin(["s2_buy", "s2_buy3"])]
    if not s2.empty:
        lines += table(s2[s2["date"] < "2025-01-01"], "策略二 2024(樣本內,不算數)")
        lines += table(s2[s2["date"] >= "2025-01-01"], "策略二 2025 至今(樣本外,只有這個算數)")
    # 交集榜對照
    inter = ret_df[ret_df["strategy"] == "intersection"]
    if not inter.empty:
        sig = pd.read_sql("SELECT date, stock_id FROM backtest_signals WHERE strategy='intersection'", conn)
        cnt = pd.read_sql("SELECT date, strategy, COUNT(*) c FROM backtest_signals GROUP BY date, strategy", conn)
        all_dates = sorted(pd.read_sql("SELECT DISTINCT date FROM backtest_signals", conn)["date"])
        pos = {d: i for i, d in enumerate(all_dates)}
        have = set(zip(sig["date"], sig["stock_id"]))
        streak, first = {}, {}
        for d, sid in have:
            n, i = 1, pos[d] - 1
            while i >= 0 and (all_dates[i], sid) in have:
                n += 1
                i -= 1
            streak[(d, sid)] = n
            first[(d, sid)] = n == 1
        inter = inter.copy()
        inter["streak"] = [streak.get((d, s), 1) for d, s in zip(inter["date"], inter["stock_id"])]
        lines += table(inter[inter["streak"] == 1].assign(strategy="intersection(只算首次出現)"), "交集榜:只算首次出現那天")
        lines += table(inter[inter["streak"] >= 2].assign(strategy="intersection(連2日以上)"), "交集榜:連 2 日以上")
        lines += table(inter[inter["streak"] == 1].assign(strategy="intersection(單日)"), "交集榜:單日(對應 I 項)")
    # 一句話結論
    lines += ["## 一句話結論", ""]
    for k in sorted(ret_df["strategy"].unique()):
        sel = ret_df[ret_df["strategy"] == k]
        if k in ("s2_buy", "s2_buy3"):
            sel = sel[sel["date"] >= "2025-01-01"]
            note = "(只看樣本外 2025 至今)"
        else:
            note = ""
        verdicts = []
        for h in HORIZONS:
            g = sel[sel["horizon"] == h]
            if g.empty:
                continue
            n, m, w = len(g), g["ret"].mean(), (g["ret"] > 0).mean()
            ex = m - g["index_ret"].mean()
            b = base_for(k, h, set(g["date"]))
            if n < MIN_N:
                verdicts.append(f"{h}日:樣本不足(N={n})")
            elif m > 0 and ex > 0 and w > 0.5 and (pd.isna(b) or m > b):
                verdicts.append(f"{h}日:有效(勝率 {w * 100:.0f}%、超額 {ex * 100:+.1f}%,樣本 {n})")
            else:
                verdicts.append(f"{h}日:無效(勝率 {w * 100:.0f}%、超額 {ex * 100:+.1f}%"
                                + ("" if pd.isna(b) else f"、隨機基準 {b * 100:+.1f}%") + f",樣本 {n})")
        lines.append(f"- **{k}**{note}:" + ";".join(verdicts))
    return "\n".join(lines)


def write_outputs(conn, ret_df, base_df, d_from, d_to, skipped, chips_last):
    csv_path = ROOT / "data" / f"backtest_{d_from}_{d_to}.csv"
    sig = pd.read_sql("SELECT date, strategy, stock_id FROM backtest_signals WHERE date BETWEEN ? AND ?", conn, params=[d_from, d_to])
    if not ret_df.empty:
        wide = ret_df.pivot_table(index=["date", "strategy", "stock_id", "entry_date", "entry_price"], columns="horizon",
                                  values=["ret", "index_ret"], aggfunc="first").reset_index()
        wide.columns = ["_".join(str(x) for x in c if x != "").strip("_") for c in wide.columns]
        wide = wide.rename(columns={"ret_5": "5日報酬", "ret_20": "20日報酬", "index_ret_5": "同期大盤5日", "index_ret_20": "同期大盤20日",
                                    "date": "日期", "strategy": "策略", "stock_id": "代號", "entry_date": "進場日", "entry_price": "進場價"})
        wide.to_csv(csv_path, index=False, encoding="utf-8-sig")
    else:
        sig.to_csv(csv_path, index=False, encoding="utf-8-sig")
    md = summarize(conn, ret_df, base_df, d_from, d_to, skipped, chips_last)
    (ROOT / "data" / "backtest_summary.md").write_text(md, encoding="utf-8")
    print(md)
    print(f"\n已輸出 {csv_path} 與 data/backtest_summary.md")


def chips_last_day():
    if not CHIPS_PATH.exists():
        return None
    c = sqlite3.connect(f"file:{CHIPS_PATH}?mode=ro", uri=True)
    r = c.execute("SELECT MAX(date) FROM prices").fetchone()
    return r[0] if r else None


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="d_from", default="2024-01-02")
    ap.add_argument("--to", dest="d_to", default=date.today().isoformat())
    ap.add_argument("--strategies", default=",".join(GROUPS))
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--extended", action="store_true", help="回補時連融資融券、借券餘額一起補(擴充策略 D/E 用)")
    ap.add_argument("--import-chips", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--yes", action="store_true", help="確認要真的花 FinMind 額度回補/匯入")
    ap.add_argument("--per-hour", type=int, default=4500)
    ap.add_argument("--db", default=None, help="回測資料庫路徑(預設 data/backtest.db;測試用)")
    args = ap.parse_args()
    conn = connect_bt(args.db)

    if args.plan:
        plan(conn, args.d_from, args.d_to)
        return
    if args.init:
        init_from_live(conn)
        return
    if args.backfill or args.import_chips:
        if not args.yes:
            print("這會花 FinMind 額度/讀 92GB 的 chips.db,先跑 --plan 看估算;確定要做請加 --yes")
            return
        if args.backfill:
            backfill(conn, args.d_from, args.d_to, extended=args.extended, per_hour=args.per_hour)
        if args.import_chips:
            import_chips(conn, args.d_from, args.d_to)
        return
    groups = [g.strip() for g in args.strategies.split(",") if g.strip() in GROUPS]
    days = trading_days(conn, args.d_from, args.d_to)
    last = chips_last_day()
    if args.run:
        if not days:
            print("backtest.db 裡沒有交易日曆/資料,請先 --backfill")
            return
        n, skipped = replay(conn, days, groups, last)
        print(f"重播完成:{len(days)} 天、{n} 筆訊號;略過 {sum(len(v) for v in skipped.values())} 個『策略×日』")
    if args.run or args.report:
        ret_df = compute_returns(conn, args.d_from, args.d_to)
        base_df = random_baseline(conn, ret_df, args.d_from, args.d_to) if not ret_df.empty else pd.DataFrame()
        skipped = locals().get("skipped", {})
        write_outputs(conn, ret_df, base_df, args.d_from, args.d_to, skipped, last)


if __name__ == "__main__":
    main()
