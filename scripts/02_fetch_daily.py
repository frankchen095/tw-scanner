"""
02_fetch_daily.py —— 只抓某一天的資料存進資料庫,不產生報表、不推播

用法:
    python scripts/02_fetch_daily.py                    # 抓今天
    python scripts/02_fetch_daily.py --date 2026-10-05  # 抓指定日期(補資料、測試用)
    python scripts/02_fetch_daily.py --date 2026-10-05 --extra-only
        # 只補抓策略七(同區/庫藏股分點)涉及的股票的分點日報,不重抓行情、法人、營收
"""

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from db import connect  # noqa: E402
from fetchers import do_fetch, fetch_extra_only  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=date.today().strftime("%Y-%m-%d"))
    parser.add_argument("--extra-only", action="store_true", help="只補抓策略七涉及的股票的分點日報")
    args = parser.parse_args()

    conn = connect()
    ok = fetch_extra_only(conn, args.date) if args.extra_only else do_fetch(conn, args.date)
    conn.close()
    print("\n完成。" if ok else "\n這天沒有行情資料(非交易日?),什麼都沒存。")


if __name__ == "__main__":
    main()
