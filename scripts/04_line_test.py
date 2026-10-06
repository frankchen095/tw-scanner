"""
04_line_test.py —— 測試 LINE 推播,並查你的 LINE 官方帳號每月訊息額度和已用量

執行前要先設定兩個環境變數:
    LINE_CHANNEL_ACCESS_TOKEN   (LINE Developers 後台 → Messaging API → Channel access token)
    LINE_USER_ID                (LINE Developers 後台 → Basic settings 最下面的 Your user ID,U 開頭 33 碼)
還有:你要先用手機把這個官方帳號加為好友,不然推不到。

用法:
    python scripts/04_line_test.py            # 送 1 則測試訊息 + 印出額度
    python scripts/04_line_test.py --quota    # 只查額度,不送訊息
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from notify import line_quota, send_line  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("--quota", action="store_true", help="只查額度,不送訊息")
args = parser.parse_args()

before = line_quota()
if before:
    print(f"額度:{before[0]};本月已用 {before[1]} 則")

if not args.quota:
    sent = send_line(["台股盤後掃描:LINE 測試訊息。收到這則代表推播設定成功。"])
    print(f"已送出 {sent} 則,請到 LINE 看有沒有收到。")
    after = line_quota()
    if after and before:
        print(f"送出後本月已用 {after[1]} 則(這次用了 {after[1] - before[1]} 則)")
