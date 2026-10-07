"""
tracking.py —— 成效追蹤(第三階段)

每天(正式推播時)把每個策略選出的股票記進 signals 表;之後每天檢查哪些訊號已經滿 5 個、
20 個交易日,算出報酬存進 signal_returns;每週五推播一次彙總。

算法(都寫在報表裡,不藏著):
  進場價   訊號隔天(下一個交易日)的『開盤價』——因為推播是晚上才收到。
  出場價   進場日算第 1 天,第 5 / 第 20 個交易日的『收盤價』。
  價格     一律用還原股價(TaiwanStockPriceAdj);進場和出場在同一次查詢裡取,不會被之後的除權息影響。
  報酬     出場價 ÷ 進場價 − 1。策略三是放空,報酬反向計算(取負號)。
  勝率     報酬 > 0 的比例。
  大盤對照 加權指數(TaiwanStockPrice 的 TAIEX)同一段:進場日開盤進、同一個出場日收盤出;
           放空的策略,大盤對照也反向(大盤跌才算贏)。
  交易日   用加權指數的交易日當日曆,所以國定假日、颱風假都不會算錯。

追蹤的策略:策略一、策略二(今日買超榜、近3日買超榜)、策略三(放空)、策略四(列出的全部)、策略五、
策略七、策略八(單日榜、5日榜)、策略九(投信、外資)、交集榜。
沒有追蹤:策略二的賣超榜(不是偏多訊號)、策略六(是文字分析,不是選股名單)。
同一檔股票連續幾天都出現,每一天各算一筆訊號。
"""

import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import finmind_query  # noqa: E402
from helpers import parallel_map  # noqa: E402

HORIZONS = (5, 20)

NAMES = {
    "s1": "策略一 創新高策略",
    "s2_buy": "策略二 前40大分點今日買超榜",
    "s2_buy3": "策略二 前40大分點近3日買超榜",
    "s3": "策略三 漲停放空(放空)",
    "s4": "策略四 當日營收公布",
    "s5": "策略五 創新高",
    "s7": "策略七 地緣/庫藏股分點",
    "s8_1": "策略八 法人單日買超榜",
    "s8_5": "策略八 法人近5日買超榜",
    "s9_trust": "策略九 投信連續買超",
    "s9_foreign": "策略九 外資連續買超",
    "intersection": "交集榜",
}
SHORT = {"s3"}


def tracked_picks(res):
    """res = {策略key: result},回傳 [(strategy, stock_id, direction)]。"""
    out = []
    for key in NAMES:
        r = res.get(key)
        if not r:
            continue
        picks = r.get("meta", {}).get("all_picks") if key == "s4" else r["picks"]
        for sid in dict.fromkeys(picks or []):
            out.append((key, str(sid), "short" if key in SHORT else "long"))
    return out


def record_signals(conn, day, res):
    rows = [(day, k, sid, d) for k, sid, d in tracked_picks(res)]
    conn.executemany("INSERT OR IGNORE INTO signals VALUES (?,?,?,?)", rows)
    conn.commit()
    return len(rows)


def _index_calendar(start, end):
    df = finmind_query("TaiwanStockPrice", "TAIEX", start, end, quiet=True)
    if df.empty:
        return None
    df = df.sort_values("date").reset_index(drop=True)
    return df[["date", "open", "close"]]


def update_returns(conn, day):
    """檢查所有已經滿 5 / 20 個交易日、還沒算報酬的訊號,算出來存起來。回傳 (新算筆數, 錯誤訊息或 None)。"""
    sig = pd.read_sql("SELECT date, strategy, stock_id, direction FROM signals WHERE date < ?", conn, params=[day])
    if sig.empty:
        return 0, None
    done = pd.read_sql("SELECT date, strategy, stock_id, horizon FROM signal_returns", conn)
    done_set = set(zip(done["date"], done["strategy"], done["stock_id"], done["horizon"]))

    first = sig["date"].min()
    cal = _index_calendar((date.fromisoformat(first) - timedelta(days=7)).isoformat(), day)
    if cal is None:
        return 0, "加權指數資料抓不到,成效追蹤今天沒有更新"
    dates = cal["date"].tolist()
    idx_open, idx_close = dict(zip(cal["date"], cal["open"])), dict(zip(cal["date"], cal["close"]))

    due = []  # (signal row, horizon, entry_date, exit_date)
    for r in sig.itertuples():
        later = [i for i, d in enumerate(dates) if d > r.date]
        if not later:
            continue
        e = later[0]
        for h in HORIZONS:
            if (r.date, r.strategy, r.stock_id, h) in done_set or e + h - 1 >= len(dates):
                continue
            due.append((r, h, dates[e], dates[e + h - 1]))
    if not due:
        return 0, None

    by_stock = {}
    for r, h, ed, xd in due:
        lo = by_stock.setdefault(r.stock_id, [r.date, day])
        lo[0] = min(lo[0], r.date)

    def fetch(sid):
        start = (date.fromisoformat(by_stock[sid][0]) - timedelta(days=3)).isoformat()
        df = finmind_query("TaiwanStockPriceAdj", sid, start, day, quiet=True)
        return sid, (None if df.empty else df.set_index("date")[["open", "close"]])

    prices = {}
    for res in parallel_map(fetch, list(by_stock), workers=4):
        if res and res[1] is not None:
            prices[res[0]] = res[1]

    out = []
    for r, h, ed, xd in due:
        p = prices.get(r.stock_id)
        if p is None or ed not in p.index or xd not in p.index:
            continue
        entry, exit_ = float(p.loc[ed, "open"]), float(p.loc[xd, "close"])
        if entry <= 0 or exit_ <= 0:
            continue
        ret = exit_ / entry - 1
        idx_ret = float(idx_close[xd]) / float(idx_open[ed]) - 1
        if r.direction == "short":
            ret, idx_ret = -ret, -idx_ret
        out.append((r.date, r.strategy, r.stock_id, h, ed, entry, xd, exit_, ret, idx_ret))
    conn.executemany("INSERT OR REPLACE INTO signal_returns VALUES (?,?,?,?,?,?,?,?,?,?)", out)
    conn.commit()
    return len(out), None


def summary_text(conn, day):
    """每週五推播的彙總文字。"""
    title = "【成效追蹤】"
    sig = pd.read_sql("SELECT date, strategy, stock_id FROM signals", conn)
    if sig.empty:
        return f"{title}\n還沒有任何訊號紀錄(成效追蹤從正式推播開始記錄)。"
    ret = pd.read_sql("SELECT strategy, horizon, ret, index_ret FROM signal_returns", conn)
    first = sig["date"].min()
    lines = [
        f"{title}自 {first} 起累計(到 {day})",
        "算法:進場 = 訊號隔天開盤價;5日/20日 = 進場日算第1天的第5/第20個交易日收盤;還原股價;",
        "勝率 = 報酬>0 的比例;策略三是放空,報酬和大盤對照都反向計算;同一檔連續出現每天各算一筆。",
    ]
    for key, name in NAMES.items():
        n_sig = int((sig["strategy"] == key).sum())
        if not n_sig:
            continue
        parts = [f"{name}:訊號 {n_sig} 筆"]
        for h in HORIZONS:
            r = ret[(ret["strategy"] == key) & (ret["horizon"] == h)]
            if r.empty:
                parts.append(f"  {h}日:還沒有滿 {h} 個交易日的訊號")
            else:
                parts.append(
                    f"  {h}日:{len(r)} 筆有成績,平均 {r['ret'].mean() * 100:+.1f}%,"
                    f"勝率 {(r['ret'] > 0).mean() * 100:.0f}%(大盤同期 {r['index_ret'].mean() * 100:+.1f}%)"
                )
        lines.append("\n".join(parts))
    return "\n".join(lines)
