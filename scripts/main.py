"""
main.py —— 主程式:抓資料 → 產生功能1/2/3/4的報表 → 推播 Telegram

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
import feature1_dama_institutional as feature1  # noqa: E402
import feature2_joint_buy as feature2  # noqa: E402
import feature3_news as feature3  # noqa: E402
import feature4_breakout as feature4  # noqa: E402

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
    blocks.append(("header", f"台股盤後掃描 {day}\n功能1:大摩分點 + 投信/外資買賣超排行"))
    blocks += [("body", s) for s in feature1.build(conn, day)]
    blocks.append(("header", f"功能2:外資+投信同買({day})"))
    blocks += [("body", s) for s in feature2.build(conn, day)]
    blocks.append(("header", f"功能3:新聞事件分析({day})"))
    blocks += [("prose", s) for s in feature3.build(conn, day)]
    blocks.append(("header", f"功能4:帶量突破盤整({day})"))
    blocks += [("body", s) for s in feature4.build(conn, day)]

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
