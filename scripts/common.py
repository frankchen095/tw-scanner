"""
common.py —— 共用工具

你不需要動這個檔案。它負責:
  - 讀取環境變數(FINMIND_TOKEN / TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID / ANTHROPIC_API_KEY)
  - 打 FinMind API,遇到額度限制自動等待重試
  - 送 Telegram 訊息(太長會自動分段)
  - 呼叫 Claude API(功能3新聞分析用)
"""

import os
import sys
import time
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent

FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"
TELEGRAM_URL = "https://api.telegram.org/bot{token}/sendMessage"
CLAUDE_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL = "claude-haiku-4-5-20251001"


def get_env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        print(f"找不到環境變數 {name}")
        print("  Windows PowerShell:  $env:" + name + '="你的值"')
        print("  Mac / Linux:         export " + name + '="你的值"')
        sys.exit(1)
    return value


def finmind_query(dataset, data_id=None, start=None, end=None, retries=6, quiet=False):
    """
    打一次 FinMind API,回傳 DataFrame。
    - 額度用完(402/429 或訊息含 limit)→ 等 60 秒再試
    - 其他失敗 → 印出訊息,回傳空的 DataFrame
    """
    params = {"dataset": dataset, "token": get_env("FINMIND_TOKEN")}
    if data_id:
        params["data_id"] = data_id
    if start:
        params["start_date"] = start
    if end:
        params["end_date"] = end

    for _ in range(retries):
        try:
            resp = requests.get(FINMIND_URL, params=params, timeout=90)
        except requests.RequestException as e:
            print(f"    連線錯誤 {e},60 秒後重試")
            time.sleep(60)
            continue

        if resp.status_code in (402, 429):
            print("    額度上限,等 60 秒再試…")
            time.sleep(60)
            continue

        try:
            payload = resp.json()
        except Exception:
            print("    回傳不是 JSON,30 秒後重試")
            time.sleep(30)
            continue

        if payload.get("status") != 200:
            msg = str(payload.get("msg", ""))
            if "limit" in msg.lower() or "上限" in msg:
                print("    額度上限,等 60 秒再試…")
                time.sleep(60)
                continue
            if not quiet:
                print(f"    查詢失敗 [{dataset} {data_id} {start}]: {msg}")
            return pd.DataFrame()

        return pd.DataFrame(payload.get("data", []))

    print("    重試次數用完,略過")
    return pd.DataFrame()


def send_telegram(text, parse_mode="HTML"):
    """傳一則文字訊息到 Telegram。文字太長會自動切成多則依序送出。"""
    token = get_env("TELEGRAM_BOT_TOKEN")
    chat_id = get_env("TELEGRAM_CHAT_ID")
    url = TELEGRAM_URL.format(token=token)

    max_len = 3500
    chunks = [text[i : i + max_len] for i in range(0, len(text), max_len)] or [text]

    for chunk in chunks:
        resp = requests.post(
            url,
            data={"chat_id": chat_id, "text": chunk, "parse_mode": parse_mode},
            timeout=30,
        )
        if resp.status_code != 200:
            print(f"    Telegram 傳送失敗: {resp.status_code} {resp.text}")
        time.sleep(1)


def call_claude(prompt, max_tokens=1500):
    """呼叫 Claude API,回傳文字回覆。找不到 ANTHROPIC_API_KEY 或呼叫失敗會丟出例外(不會中斷整支程式)。"""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("找不到環境變數 ANTHROPIC_API_KEY")

    resp = requests.post(
        CLAUDE_URL,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": CLAUDE_MODEL,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    return "".join(block.get("text", "") for block in data.get("content", []))
