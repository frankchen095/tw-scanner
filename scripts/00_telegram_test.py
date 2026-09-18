"""
00_telegram_test.py —— 測試 Telegram 推播能不能通

執行前,先設定好環境變數(每次開新終端機都要設一次):
  Windows PowerShell:
    $env:TELEGRAM_BOT_TOKEN="你的bot token"
    $env:TELEGRAM_CHAT_ID="你的chat id"

執行(在專案根目錄):
    python scripts/00_telegram_test.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import send_telegram  # noqa: E402

send_telegram("測試訊息:如果你在 Telegram 收到這則,代表推播設定成功 ✅")
print("已送出,請去 Telegram 看看機器人有沒有傳訊息給你。")
