"""
fetchers.py —— 抓資料的共用函式,被 02_fetch_daily.py 和 01b_backfill.py 共用

只存「有效個股」(排除 ETF、ETN、權證等衍生商品),避免資料庫塞進一堆用不到的
資料、檔案爆大(GitHub 單檔上限 100MB)。

你不需要動這個檔案。
"""

import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import finmind_query  # noqa: E402
from db import DAMA_TRADER_ID  # noqa: E402
from helpers import get_valid_stock_ids  # noqa: E402

SLEEP = 0.3


def fetch_price(conn, day):
    df = finmind_query("TaiwanStockPrice", None, day, day, quiet=True)
    if len(df) == 0:
        print(f"  {day} 沒有股價資料(可能不是交易日),跳過")
        return None

    valid_ids = get_valid_stock_ids(conn)
    df = df[df["stock_id"].astype(str).isin(valid_ids)]

    conn.executemany(
        "INSERT OR REPLACE INTO daily_price VALUES (?,?,?,?,?,?,?,?)",
        [
            (
                day,
                str(r.stock_id),
                r.open,
                r.max,
                r.min,
                r.close,
                r.Trading_Volume,
                r.Trading_money,
            )
            for r in df.itertuples()
        ],
    )
    conn.commit()
    print(f"  {day} 股價 {len(df)} 筆(已排除ETF/權證等)")
    return df


def fetch_institutional(conn, day):
    df = finmind_query("TaiwanStockInstitutionalInvestorsBuySell", None, day, day, quiet=True)
    if len(df) == 0:
        print(f"  {day} 沒有法人資料,跳過")
        return

    valid_ids = get_valid_stock_ids(conn)
    df = df[df["stock_id"].astype(str).isin(valid_ids)]

    conn.executemany(
        "INSERT OR REPLACE INTO daily_institutional VALUES (?,?,?,?,?)",
        [(day, str(r.stock_id), r.name, r.buy, r.sell) for r in df.itertuples()],
    )
    conn.commit()
    print(f"  {day} 法人買賣超 {len(df)} 筆(已排除ETF/權證等)")


def fetch_dama(conn, day, universe):
    n = 0
    for i, sid in enumerate(universe, 1):
        df = finmind_query("TaiwanStockTradingDailyReport", sid, day, day, quiet=True)
        if len(df) and "securities_trader_id" in df.columns:
            dama = df[df["securities_trader_id"] == DAMA_TRADER_ID]
            if len(dama):
                conn.executemany(
                    "INSERT OR REPLACE INTO daily_dama VALUES (?,?,?,?,?)",
                    [
                        (day, str(r.stock_id), r.price, r.buy, r.sell)
                        for r in dama.itertuples()
                    ],
                )
                conn.commit()
                n += len(dama)
        if i % 50 == 0:
            print(f"  大摩分點查詢進度 {i}/{len(universe)}")
        time.sleep(SLEEP)
    print(f"  {day} 大摩分點資料 {n} 筆(掃了 {len(universe)} 檔)")
