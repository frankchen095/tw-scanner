"""
notify.py —— 用 LINE Messaging API 推播

LINE 的限制(官方文件):
  - 一則文字訊息最多 5000 字;一次 push 請求最多 5 則訊息
  - 只支援純文字(不能用 <b>、<pre>,也沒有等寬字型),所以報表一律用「每檔一行」的純文字格式
  - 官方帳號免費方案每月有則數上限(依網路資料是 200 則,實際以你帳號的 API 回報為準,
    用 scripts/04_line_test.py 可以查到剩餘額度),每一則訊息(含合併後的長文字)都算一則,
    所以這裡把多個策略合併塞進同一則訊息,盡量減少則數。

需要環境變數:LINE_CHANNEL_ACCESS_TOKEN(長期的 channel access token)和
LINE_USER_ID(你自己的 user ID,在 LINE Developers 後台 Basic settings 最下面的 Your user ID)。
"""

import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import get_env  # noqa: E402

PUSH_URL = "https://api.line.me/v2/bot/message/push"
QUOTA_URL = "https://api.line.me/v2/bot/message/quota"
CONSUMPTION_URL = "https://api.line.me/v2/bot/message/quota/consumption"
MAX_CHARS = 4800  # 官方上限 5000,留一點緩衝
MAX_PER_REQUEST = 5


def _split_long(text, limit):
    """單一區塊超過 limit 就在『換行處』切開,後面的段落開頭補上標題 + (續)。"""
    if len(text) <= limit:
        return [text]
    lines = text.split("\n")
    title = lines[0]
    pieces, cur = [], []
    size = 0
    for ln in lines:
        add = len(ln) + 1
        if cur and size + add > limit:
            pieces.append("\n".join(cur))
            cur, size = [f"{title}(續)"], len(title) + 4
        cur.append(ln)
        size += add
    if cur:
        pieces.append("\n".join(cur))
    return pieces


def pack(sections, limit=MAX_CHARS):
    """把許多區塊(每個是一段文字)合併成最少則的訊息,每則不超過 limit 字。"""
    pieces = []
    for s in sections:
        pieces += _split_long(s, limit)
    bubbles, cur = [], ""
    for p in pieces:
        if cur and len(cur) + 2 + len(p) > limit:
            bubbles.append(cur)
            cur = p
        else:
            cur = p if not cur else cur + "\n\n" + p
    if cur:
        bubbles.append(cur)
    return bubbles


def _headers():
    return {"Authorization": "Bearer " + get_env("LINE_CHANNEL_ACCESS_TOKEN"), "Content-Type": "application/json"}


def send_line(bubbles):
    """依序推播多則文字訊息,回傳成功送出的則數。失敗會印出狀態碼和原因(不會印出 token)。"""
    user_id = get_env("LINE_USER_ID")
    sent = 0
    for i in range(0, len(bubbles), MAX_PER_REQUEST):
        chunk = bubbles[i : i + MAX_PER_REQUEST]
        resp = requests.post(
            PUSH_URL,
            headers=_headers(),
            json={"to": user_id, "messages": [{"type": "text", "text": t} for t in chunk]},
            timeout=30,
        )
        if resp.status_code != 200:
            print(f"    LINE 推播失敗: {resp.status_code} {resp.text[:300]}")
            break
        sent += len(chunk)
        time.sleep(0.5)
    return sent


def check_token(token):
    """驗證 channel access token 有沒有效。回傳 (HTTP 狀態碼, 官方帳號名稱或錯誤訊息)。"""
    try:
        r = requests.get(
            "https://api.line.me/v2/bot/info", headers={"Authorization": "Bearer " + token}, timeout=30
        )
    except requests.RequestException as e:
        return None, f"連不上 LINE:{e}"
    if r.status_code == 200:
        return 200, r.json().get("displayName", "")
    return r.status_code, r.text[:200]


def line_quota():
    """回傳 (上限描述, 本月已用則數)。查不到就回傳 None。"""
    try:
        qr = requests.get(QUOTA_URL, headers=_headers(), timeout=30)
        cr = requests.get(CONSUMPTION_URL, headers=_headers(), timeout=30)
        if qr.status_code != 200 or cr.status_code != 200:
            print(f"    查詢 LINE 額度失敗: HTTP {qr.status_code} / {cr.status_code}")
            return None
        q, c = qr.json(), cr.json()
        limit = "沒有上限" if q.get("type") == "none" else f"每月 {q.get('value')} 則"
        return limit, c.get("totalUsage")
    except Exception as e:  # noqa: BLE001
        print(f"    查詢 LINE 額度失敗: {e}")
        return None
