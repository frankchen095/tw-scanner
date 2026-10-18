"""
03_news_test.py —— 只測試策略六(新聞+AI分析),不需要 FinMind、不會推播

用 repo 裡的 scanner.db 拿股票名單(補個股代號用);在 GitHub Actions 的測試裡,資料庫是暫時的,
寫進去的簡報/候選新聞不會被存回 repo。

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
from db import connect  # noqa: E402

conn = connect()
for section in feature3_news.build(conn, date.today().strftime("%Y-%m-%d")):
    print("\n" + "=" * 60)
    print(section)
conn.close()
