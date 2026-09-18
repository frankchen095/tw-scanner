"""
02_fetch_daily.py —— 每天收盤後執行:抓當天的股價、法人買賣超、大摩分點資料,存進本地資料庫

用法:
    python scripts/02_fetch_daily.py                        # 抓今天
    python scripts/02_fetch_daily.py --date 2024-01-05       # 抓指定日期(測試用)
    python scripts/02_fetch_daily.py --date 2024-01-05 --dama-limit 10   # 大摩分點只掃10檔(快速測試)
    python scripts/02_fetch_daily.py --date 2024-01-05 --skip-dama      # 完全跳過大摩分點(最快)
"""

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from db import connect, prune  # noqa: E402
from fetchers import fetch_price, fetch_institutional, fetch_dama  # noqa: E402

TOP_N_FOR_DAMA = 300


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=date.today().strftime("%Y-%m-%d"))
    parser.add_argument("--skip-dama", action="store_true", help="完全跳過大摩分點迴圈查詢")
    parser.add_argument("--dama-limit", type=int, default=TOP_N_FOR_DAMA, help="大摩分點迴圈查詢的股票數上限")
    args = parser.parse_args()
    day = args.date

    conn = connect()
    print(f"=== 抓取 {day} 的資料 ===")

    print("\n[1/3] 全市場股價")
    price_df = fetch_price(conn, day)

    print("\n[2/3] 三大法人買賣超")
    fetch_institutional(conn, day)

    print(f"\n[3/3] 大摩分點(依成交金額前 {args.dama_limit} 名迴圈查詢)")
    if args.skip_dama:
        print("  (--skip-dama,跳過)")
    elif price_df is not None and len(price_df):
        universe = (
            price_df.sort_values("Trading_money", ascending=False)
            .head(args.dama_limit)["stock_id"]
            .astype(str)
            .tolist()
        )
        fetch_dama(conn, day, universe)
    else:
        print("  沒有當天股價資料,無法決定範圍,跳過")

    prune(conn)
    conn.close()
    print("\n完成。")


if __name__ == "__main__":
    main()
