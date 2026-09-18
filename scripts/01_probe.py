"""
01_probe.py —— 建置前的探測

在正式寫功能之前,先用小範圍查詢確認幾件事:
  1. 分點資料(TaiwanStockTradingDailyReport)能不能「不指定股票、查全市場」
     → 能的話,功能1(大摩分點排行)、功能4 會好做很多
     → 不能的話,就要逐檔迴圈查,要另外設計股票清單和額度分配
  2. 「大摩」(台灣摩根士丹利)的正確分點代號
  3. 三大法人買賣超(TaiwanStockInstitutionalInvestorsBuySell)能不能「查全市場」
  4. 全市場單日股價的欄位(算成交金額排行、股價突破要用)
  5. 有沒有股本資料可以拿來算「股本比」

執行前,先設定好環境變數:
  Windows PowerShell:
    $env:FINMIND_TOKEN="你的token"

執行(在專案根目錄):
    python scripts/01_probe.py

跑完後,把整段輸出複製貼給 Claude,才能繼續往下建。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import finmind_query  # noqa: E402

TEST_DATE = "2024-01-05"  # 隨便挑一個有交易的普通日子
TEST_STOCK = "2330"  # 台積電,拿來測單一個股查詢


def show(title, df):
    print(f"\n=== {title} ===")
    print(f"筆數: {len(df)}")
    if len(df):
        print("欄位:", list(df.columns))
        print(df.head(5).to_string())
    return df


# ---- 1. 分點資料,單一個股(先確認基本功能正常) ----
chips_single = show(
    f"分點資料(單一個股) TaiwanStockTradingDailyReport({TEST_STOCK}, {TEST_DATE})",
    finmind_query("TaiwanStockTradingDailyReport", TEST_STOCK, TEST_DATE, TEST_DATE),
)

# ---- 2. 分點資料,不指定股票(全市場單日)——這題最關鍵 ----
show(
    f"分點資料(全市場,不指定股票) TaiwanStockTradingDailyReport(None, {TEST_DATE})",
    finmind_query("TaiwanStockTradingDailyReport", None, TEST_DATE, TEST_DATE),
)

# ---- 3. 找「大摩」的分點代號 ----
if len(chips_single):
    print("\n--- 分點名稱範例(這檔股票當天出現的分點) ---")
    if "securities_trader" in chips_single.columns:
        print(
            chips_single[["securities_trader_id", "securities_trader"]]
            .drop_duplicates()
            .to_string(index=False)
        )

traders = finmind_query("TaiwanSecuritiesTraderInfo")
if len(traders):
    name_col = "securities_trader" if "securities_trader" in traders.columns else traders.columns[-1]
    mask = traders[name_col].astype(str).str.contains("摩根|大摩", na=False)
    print(f"\n=== 券商資料中,名稱含「摩根/大摩」的分點(共 {mask.sum()} 筆) ===")
    if mask.sum():
        print(traders[mask].to_string(index=False))
    else:
        print("(沒找到,可能欄位名稱不同,把上面 traders 的欄位列表貼給我)")
        print("欄位:", list(traders.columns))

# ---- 4. 三大法人買賣超,單一個股 ----
show(
    f"三大法人買賣超(單一個股) TaiwanStockInstitutionalInvestorsBuySell({TEST_STOCK}, {TEST_DATE})",
    finmind_query("TaiwanStockInstitutionalInvestorsBuySell", TEST_STOCK, TEST_DATE, TEST_DATE),
)

# ---- 5. 三大法人買賣超,不指定股票(全市場單日) ----
show(
    f"三大法人買賣超(全市場,不指定股票) TaiwanStockInstitutionalInvestorsBuySell(None, {TEST_DATE})",
    finmind_query("TaiwanStockInstitutionalInvestorsBuySell", None, TEST_DATE, TEST_DATE),
)

# ---- 6. 全市場單日股價(成交金額排行、股價突破都要用) ----
show(
    f"全市場單日股價 TaiwanStockPrice(不指定股票, {TEST_DATE})",
    finmind_query("TaiwanStockPrice", None, TEST_DATE, TEST_DATE),
)

# ---- 7. 股本資料候選(算「股本比」要用) ----
for ds in ["TaiwanStockCapital", "TaiwanStockHoldingSharesPer"]:
    show(
        f"股本候選資料集 {ds}({TEST_STOCK})",
        finmind_query(ds, TEST_STOCK, TEST_DATE, TEST_DATE, quiet=True),
    )

print(
    "\n========================\n"
    "把上面整段輸出複製貼給我,我會根據結果決定:\n"
    "  - 分點/法人資料能不能一次查全市場,還是要逐檔迴圈查\n"
    "  - 大摩的正確分點代號\n"
    "  - 股本資料要用哪個資料集\n"
    "========================"
)
