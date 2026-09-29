"""
03_news_test.py —— 只測試功能3(新聞+AI分析),不需要 FinMind、不會送 Telegram

執行前,先設定好環境變數:
  Windows PowerShell:
    $env:ANTHROPIC_API_KEY="你的key"

執行(在專案根目錄):
    python scripts/03_news_test.py
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "reports"))
import feature3_news  # noqa: E402

for section in feature3_news.build(None, date.today().strftime("%Y-%m-%d")):
    print("\n" + "=" * 60)
    print(section)
