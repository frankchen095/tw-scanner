"""
context.py —— 每個策略共用的『今天的環境』:資料庫連線、報表日期、市值、股票名稱、風險標記

你不需要動這個檔案。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from helpers import BIG_MARKET_VALUE, get_market_values_latest, get_stock_info  # noqa: E402

NO_MV = "今日無資料(FinMind 市值表沒抓到,無法套用『市值>500億』條件)"


class Context:
    def __init__(self, conn, day, risk=None):
        self.conn = conn
        self.day = day
        self.risk = risk
        self.mv, self.mv_date = get_market_values_latest(conn, day)
        self.mv_note = (
            f"市值門檻(>500億)用的是 {self.mv_date} 的市值表(FinMind 今天的市值表還沒更新)"
            if self.mv and self.mv_date != day
            else None
        )
        self.big = {s for s, v in self.mv.items() if v > BIG_MARKET_VALUE} if self.mv else None
        self._names = {sid: v[0] for sid, v in get_stock_info(conn).items()}

    def name(self, sid):
        return self._names.get(sid, "")

    def label(self, sid):
        """『名稱(代號)〔風險標記〕』"""
        tags = self.risk.tags(sid) if self.risk else ""
        return f"{self.name(sid)}({sid}){tags}"


def result(key, title, lines, picks=None, **meta):
    """每個策略統一回傳的格式。picks 是這個策略選出的股票代號(給交集榜和成效追蹤用)。"""
    return {"key": key, "title": title, "lines": lines, "picks": list(picks or []), "meta": meta}


def nodata(key, title, reason):
    return result(key, title, [reason])
