# 台股盤後掃描推播

每天收盤後自動抓 FinMind 資料，符合條件就推播到 Telegram，用 GitHub Actions 排程執行。

## 目前推播的內容

1. 帶量突破盤整：近20日盤整幅度 ≤ 10%、今日收盤突破20日高、量 ≥ 5日均量1.5倍、漲幅 > 3%
2. 追蹤名單前40大分點,合計買超個股排行：名單來自 fenpoint(分點勝率工作台)專案的回測結果
   ——2023–2026 年資料裡,單日淨買超≥5,000萬之後20日內收盤漲超過20%命中率最高的前40個
   營業處(見 `data/tracked_brokers.json`,由 fenpoint 的 `scripts/08_export_tracked_brokers.py`
   產生)。把這40個分點「當天」和「近3天」買超同一檔股票的金額加總,列出合計買超前10名的股票
   (單日版 + 近3日版)。跟功能1(已停用)的查詢共用同一次 API 呼叫,不會多花額度。
3. 整理當日總經/電子業重大新聞，用 AI 分析利多利空及受影響公司(需要另外申請 Anthropic API key)

**已停用(程式碼還在,只是沒有推播):**
- 大摩(台灣摩根士丹利)分點 + 投信/外資買賣超排行
- 外資+投信同買(皆 > 5000萬)且成交金額排全市場前100名

## 建置進度

- [x] 專案骨架、共用工具(FinMind 查詢 + Telegram 推播)
- [x] 探測 FinMind 帳號權限(大摩分點代號 1470、法人/股價可全市場查詢、股本用集保資料估算)
- [x] 本地資料庫 + 每日抓取 + 一次性歷史回補
- [x] 功能 1、2、4 實作(功能3新聞+AI分析待補,需要 Anthropic API key)
- [x] GitHub Actions 排程(.github/workflows/daily-scan.yml)
- [x] 功能1~4 端對端測試通過,已上線
- [x] 功能5(追蹤 fenpoint 回測出的高命中率分點)已建置,本機測試通過

## 資料夾

```
tw-scanner/
├── scripts/
│   ├── common.py            共用工具，不用動
│   ├── 00_telegram_test.py  測試 Telegram 推播
│   ├── 01_probe.py          探測 FinMind 權限與欄位
│   └── (後續功能腳本)
├── .github/workflows/       GitHub Actions 排程(之後補)
└── requirements.txt
```

## 設定環境變數(每次開新終端機都要設一次)

Windows PowerShell:
```powershell
$env:FINMIND_TOKEN="你的token"
$env:TELEGRAM_BOT_TOKEN="你的bot token"
$env:TELEGRAM_CHAT_ID="你的chat id"
```

## 安裝套件

```bash
pip install -r requirements.txt
```
