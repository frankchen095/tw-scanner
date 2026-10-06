"""
05_setup_line.py —— 一次設定 LINE 推播:存到 GitHub Secrets,並馬上送一則測試訊息

做的事:
  1. 問你兩個值(輸入時畫面不會顯示任何字,貼上後按 Enter 就好;不會寫進任何檔案、不會印出來)
       - Channel access token
       - User ID
  2. 把兩個值存到 GitHub repo 的 Secrets(LINE_CHANNEL_ACCESS_TOKEN、LINE_USER_ID)
  3. 用同樣的值在你電腦上送一則 LINE 測試訊息,並印出官方帳號的每月額度和已用量

用法:
    python scripts/05_setup_line.py
"""

import getpass
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = "frankchen095/tw-scanner"
ROOT = Path(__file__).resolve().parent


def ask(label, ok, hint):
    while True:
        v = getpass.getpass(f"\n{label}\n(貼上後按 Enter;畫面不會顯示任何字,這是正常的)\n> ").strip()
        if ok(v):
            print("  已收到。")
            return v
        print(f"  {hint}")


def main():
    token = ask(
        "1/2  請貼上 Channel access token(很長的一串,約 170 個字)",
        lambda v: len(v) >= 80 and not re.search(r"\s", v),
        "這串看起來不對(太短,或中間有空白/換行)。請回 LINE Developers 重新複製一次再貼。",
    )
    user_id = ask(
        "2/2  請貼上 Your user ID(U 開頭,共 33 個字)",
        lambda v: re.fullmatch(r"U[0-9a-f]{32}", v) is not None,
        "這串看起來不對。User ID 是 U 開頭、後面 32 個英數字,在 Basic settings 分頁最下面。",
    )

    print("\n=== 存到 GitHub Secrets ===")
    for name, value in [("LINE_CHANNEL_ACCESS_TOKEN", token), ("LINE_USER_ID", user_id)]:
        r = subprocess.run(
            ["gh", "secret", "set", name, "--repo", REPO], input=value, text=True, capture_output=True
        )
        if r.returncode == 0:
            print(f"  ✓ 已設定 {name}")
        else:
            print(f"  ✗ {name} 設定失敗:{r.stderr.strip()[:200]}")
            sys.exit(1)

    print("\n=== 送 LINE 測試訊息 ===")
    env = dict(os.environ, LINE_CHANNEL_ACCESS_TOKEN=token, LINE_USER_ID=user_id, PYTHONIOENCODING="utf-8")
    subprocess.run([sys.executable, str(ROOT / "04_line_test.py")], env=env)


if __name__ == "__main__":
    main()
