"""
main.py —— 主程式:抓資料 → 產生報表 → 推播 Telegram

目前推播的內容只有:
    功能4:帶量突破盤整
    功能5:追蹤名單前40大分點,合計買超個股排行(單日 + 近3日)
    功能3:新聞事件 AI 分析

功能1(大摩分點+投信外資排行)、功能2(外資投信同買)已經停用、不再推播,
程式檔案還在 scripts/reports/,之後想恢復可以參考舊版 main.py(git log 看得到)
把兩行 import 和對應 blocks 加回來就好,資料抓取(fetch_institutional)還是照跑,
沒有停,只是沒人在用那份資料而已。

功能3(新聞+AI分析)需要環境變數 ANTHROPIC_API_KEY,沒設定的話該區塊會顯示提示,
不會讓整支程式當掉。

用法:
    python scripts/main.py                       # 用今天日期跑,抓資料+送Telegram
    python scripts/main.py --date 2024-01-05      # 指定日期(測試用)
    python scripts/main.py --date 2024-01-05 --no-fetch   # 跳過抓資料,只用資料庫現有資料出報表
    python scripts/main.py --date 2024-01-05 --dry-run    # 不送Telegram,只印在畫面上看結果
"""

import argparse
import html
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import send_telegram  # noqa: E402
from db import connect, prune  # noqa: E402
from fetchers import fetch_price, fetch_institutional, fetch_dama  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent / "reports"))
import feature3_news as feature3  # noqa: E402
import feature4_breakout as feature4  # noqa: E402
import feature5_tracked_brokers as feature5  # noqa: E402

TOP_N_FOR_DAMA = 300


def do_fetch(conn, day):
    print(f"=== 抓取 {day} 的資料 ===")
    price_df = fetch_price(conn, day)
    fetch_institutional(conn, day)
    if price_df is not None and len(price_df):
        universe = (
            price_df.sort_values("Trading_money", ascending=False)
            .head(TOP_N_FOR_DAMA)["stock_id"]
            .astype(str)
            .tolist()
        )
        fetch_dama(conn, day, universe)
    prune(conn)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=date.today().strftime("%Y-%m-%d"))
    parser.add_argument("--no-fetch", action="store_true", help="跳過抓資料,直接用資料庫現有資料出報表")
    parser.add_argument("--dry-run", action="store_true", help="不送Telegram,只印在畫面上")
    args = parser.parse_args()
    day = args.date

    conn = connect()

    if not args.no_fetch:
        do_fetch(conn, day)

    print(f"\n=== 產生 {day} 的報表 ===")
    blocks = []
    blocks.append(("header", f"台股盤後掃描 {day}"))
    blocks.append(("header", f"帶量突破盤整({day})"))
    blocks += [("body", s) for s in feature4.build(conn, day)]
    blocks.append(("header", f"追蹤分點合計買超排行({day})"))
    blocks += [("body", s) for s in feature5.build(conn, day)]
    blocks.append(("header", f"新聞事件分析({day})"))
    blocks += [("prose", s) for s in feature3.build(conn, day)]

    conn.close()

    for kind, text in blocks:
        escaped = html.escape(text)
        if kind == "header":
            msg = f"<b>{escaped}</b>"
        elif kind == "prose":
            msg = escaped
        else:
            msg = f"<pre>{escaped}</pre>"
        if args.dry_run:
            print("\n" + "=" * 60)
            print(text)
        else:
            send_telegram(msg)

    print("\n完成。")


if __name__ == "__main__":
    main()
