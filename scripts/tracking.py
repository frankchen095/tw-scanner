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

追蹤的策略(方向在彙總裡每一行都標明:偏多=買進,偏空=放空、報酬反向計算):
  偏多:策略一、策略二(今日買超榜、近3日買超榜)、策略四(列出的全部)、策略五、策略七、策略八(單日榜、5日榜)、
        策略九(投信、外資)、交集榜、策略十四(大戶持股比上升)、策略十五(毛利率轉折)、基本面動能榜(三升、二升)、
        族群訊號(達標族群內所有股票等權)
  偏空:策略三(漲停放空)、策略十三(借券賣出餘額大增)、散戶接刀榜、策略十六(內部人申報轉讓:一般/鉅額交易)
沒有追蹤:策略二的賣超榜、策略六(文字分析)、策略十(ETF)、行事曆、負面新聞掃描、借券大減榜、毛利轉弱榜、大戶下降榜。
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
    "s3": "策略三 漲停放空",
    "s4": "策略四 當日營收公布",
    "s5": "策略五 創新高",
    "s7": "策略七 地緣/庫藏股分點",
    "s8_1": "策略八 法人單日買超榜",
    "s8_5": "策略八 法人近5日買超榜",
    "s9_trust": "策略九 投信連續買超",
    "s9_foreign": "策略九 外資連續買超",
    "intersection": "交集榜",
    "group": "族群訊號(達標族群內股票等權)",
    "s13": "策略十三 借券賣出餘額大增",
    "e_knife": "散戶接刀榜",
    "s14": "策略十四 大戶持股比上升",
    "s15": "策略十五 財報毛利率轉折",
    "s15_mom": "基本面動能榜〔三升〕",
    "s15_mom2": "基本面動能榜〔二升〕",
    "s16": "策略十六 內部人申報轉讓",
}
SHORT = {"s3", "s13", "e_knife", "s16"}


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


def _streak_split(conn):
    """交集榜每一筆訊號,算出『當天是連續第幾個推播日在榜上』,分成 連2日以上 / 只出現1日 兩組各自的成績。
    連續天數的算法跟報表一樣(只看 signals 表裡有記錄的推播日)。回傳 [文字行]。"""
    sig = pd.read_sql("SELECT date, stock_id FROM signals WHERE strategy='intersection'", conn)
    if sig.empty:
        return []
    dates = sorted(pd.read_sql("SELECT DISTINCT date FROM signals", conn)["date"])
    pos = {d: i for i, d in enumerate(dates)}
    have = set(zip(sig["date"], sig["stock_id"]))
    streak = {}
    for d, sid in have:
        n, i = 1, pos[d] - 1
        while i >= 0 and (dates[i], sid) in have:
            n += 1
            i -= 1
        streak[(d, sid)] = n
    ret = pd.read_sql("SELECT date, stock_id, horizon, ret, index_ret FROM signal_returns WHERE strategy='intersection'", conn)
    if ret.empty:
        return ["交集榜持續天數對照:還沒有滿 5 個交易日的交集榜訊號"]
    ret["streak"] = [streak.get((d, sid), 1) for d, sid in zip(ret["date"], ret["stock_id"])]
    lines = ["交集榜持續天數對照(連 2 日以上 vs 只出現 1 日):"]
    for h in HORIZONS:
        for label, sel in (("連2日以上", ret["streak"] >= 2), ("只出現1日", ret["streak"] < 2)):
            r = ret[sel & (ret["horizon"] == h)]
            if r.empty:
                lines.append(f"  {h}日 {label}:還沒有成績")
            else:
                lines.append(f"  {h}日 {label}:{len(r)} 筆,平均 {r['ret'].mean() * 100:+.1f}%,"
                             f"勝率 {(r['ret'] > 0).mean() * 100:.0f}%(大盤同期 {r['index_ret'].mean() * 100:+.1f}%)")
    return lines


def _compare(ret, sig, a, b, name_a, name_b):
    """兩個策略並列對照(5日/20日平均報酬與勝率),任一邊還沒有成績就說明。"""
    if not ((sig["strategy"] == a).any() and (sig["strategy"] == b).any()):
        return []
    lines = [f"對照:{name_a} vs {name_b}"]
    for h in HORIZONS:
        cells = []
        for key, name in ((a, name_a), (b, name_b)):
            r = ret[(ret["strategy"] == key) & (ret["horizon"] == h)]
            cells.append(f"{name} " + ("還沒有成績" if r.empty else
                                       f"{len(r)}筆 平均{r['ret'].mean() * 100:+.1f}% 勝率{(r['ret'] > 0).mean() * 100:.0f}%"))
        lines.append(f"  {h}日:" + " | ".join(cells))
    return lines


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
        "勝率 = 報酬>0 的比例;標〔偏空〕的是放空訊號,報酬和大盤對照都反向計算(跌才算賺);同一檔連續出現每天各算一筆。",
    ]
    for key, name in NAMES.items():
        n_sig = int((sig["strategy"] == key).sum())
        if not n_sig:
            continue
        way = "〔偏空,報酬反向〕" if key in SHORT else "〔偏多〕"
        parts = [f"{name}{way}:訊號 {n_sig} 筆"]
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
