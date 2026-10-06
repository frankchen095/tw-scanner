"""
05_setup_line.py —— 一次設定 LINE 推播:存到 GitHub Secrets,並馬上送一則測試訊息

做的事:
  1. 請你在 LINE 網頁上按「複製」,程式直接讀剪貼簿(不用貼上;讀完會清掉剪貼簿,
     不會寫進任何檔案、不會印出內容,只會印出讀到幾個字)
       - Channel access token
       - User ID
  2. 把兩個值存到 GitHub repo 的 Secrets(LINE_CHANNEL_ACCESS_TOKEN、LINE_USER_ID)
  3. 用同樣的值在你電腦上送一則 LINE 測試訊息,並印出官方帳號的每月額度和已用量

用法:
    python scripts/05_setup_line.py
"""

import os
import re
import subprocess
import sys
from pathlib import Path

REPO = "frankchen095/tw-scanner"
ROOT = Path(__file__).resolve().parent


def _ps(command):
    return subprocess.run(
        ["powershell", "-NoProfile", "-Command", command], capture_output=True, text=True, encoding="utf-8"
    )


def read_clipboard():
    """讀剪貼簿,把所有空白和換行拿掉(長字串被換行切開也沒關係)。"""
    r = _ps("[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-Clipboard -Raw")
    return re.sub(r"\s+", "", r.stdout or "")


def ask(label, ok, hint, secret=True):
    """不用貼上:請使用者在網頁上按『複製』,程式直接讀剪貼簿。只印出讀到幾個字,不印內容。"""
    while True:
        input(f"\n{label}\n  → 先到 LINE 網頁上按「複製」,再回到這個視窗按 Enter … ")
        v = read_clipboard()
        print(f"  讀到 {len(v)} 個字。")
        if ok(v):
            if secret:
                _ps("Set-Clipboard -Value ' '")
                print("  已收到,並且已清除剪貼簿。")
            else:
                print("  已收到。")
            return v
        print(f"  {hint}")


def get_valid_token():
    """讀 token,並馬上問 LINE 這串有沒有效;無效就不存、請使用者重新複製。"""
    sys.path.insert(0, str(ROOT))
    from notify import check_token

    while True:
        token = ask(
            "1/2  Channel access token(很長的一串,約 172 個字,在 Messaging API 分頁)",
            lambda v: len(v) >= 80 and not re.search(r"\s", v),
            "這串看起來不對(太短,或中間有空白/換行)。請回 LINE Developers 重新複製一次。",
        )
        status, info = check_token(token)
        if status == 200:
            print(f"  ✓ LINE 確認這串有效,官方帳號名稱:{info}")
            return token
        print(f"  ✗ LINE 回應 {status},這串 token 無效。(沒有存到任何地方)")
        print(f"    你複製到的長度是 {len(token)} 個字(正常是 172 個),結尾字元是「{token[-1]}」(正常是「=」)。")
        print("    常見原因:(1) 還沒按 Reissue,複製到的是已作廢的舊 token;(2) 少複製到最後一個字。")
        print("    請回網頁,用 Channel access token 旁邊的「Copy」按鈕再複製一次(不要用滑鼠拖曳)。")


def main():
    token = get_valid_token()
    user_id = ask(
        "2/2  Your user ID(U 開頭,共 33 個字,在 Basic settings 分頁最下面)",
        lambda v: re.fullmatch(r"U[0-9a-f]{32}", v) is not None,
        "這串看起來不對。User ID 是 U 開頭、後面 32 個英數字,在 Basic settings 分頁最下面。",
        secret=False,
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
