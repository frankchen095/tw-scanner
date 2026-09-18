"""
01b_backfill.py —— 一次性回補歷史資料

功能1需要「近100個交易日高低點」、功能4需要「近20個交易日」,剛建好的資料庫
沒有這些歷史,用這支腳本回補過去的股價和法人資料。只需要跑一次。

(大摩分點資料不回補——要迴圈查很多次太貴,讓它跟著每天排程自然累積5天份就好)

用法:
    python scripts/01b_backfill.py                                # 回補股價30個平日、法人15個平日
    python scripts/01b_backfill.py --price-days 15 --inst-days 10 # 先抓少一點測試
"""

import argparse
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from db import connect, prune  # noqa: E402
from fetchers import fetch_price, fetch_institutional  # noqa: E402


def trading_days_back(n, end_day):
    """從 end_day 往前推 n 個平日(週一~週五)。實際是否為交易日由抓取時判斷,國定假日會自然回傳空值跳過。"""
    days = pd.bdate_range(end=end_day, periods=n)
    return [d.strftime("%Y-%m-%d") for d in days]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--price-days", type=int, default=30)
    parser.add_argument("--inst-days", type=int, default=15)
    parser.add_argument(
        "--end-date", default=(date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    )
    args = parser.parse_args()

    conn = connect()

    price_days = trading_days_back(args.price_days, args.end_date)
    print(f"=== 回補股價,共 {len(price_days)} 個平日({price_days[0]} ~ {price_days[-1]}) ===")
    for i, day in enumerate(price_days, 1):
        fetch_price(conn, day)
        if i % 10 == 0:
            print(f"  [{i}/{len(price_days)}]")
        time.sleep(0.3)

    inst_days = trading_days_back(args.inst_days, args.end_date)
    print(f"\n=== 回補法人買賣超,共 {len(inst_days)} 個平日({inst_days[0]} ~ {inst_days[-1]}) ===")
    for i, day in enumerate(inst_days, 1):
        fetch_institutional(conn, day)
        time.sleep(0.3)

    prune(conn)
    conn.close()
    print("\n回補完成。(大摩分點資料不回補,會隨每天排程自然累積)")


if __name__ == "__main__":
    main()
