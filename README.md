# 台股盤後掃描推播

每天收盤後自動抓 FinMind 資料，符合條件就推播到 Telegram，用 GitHub Actions 排程執行。

## 目前規劃的 4 個功能

1. 每天自動產生「大摩(台灣摩根士丹利)分點」+「投信/外資買賣超」排行報表，推播 Telegram
2. 外資+投信同買(皆 > 5000萬)且成交金額排全市場前100名 — 當日版 + 連續3天版(3天都要達標)
3. 整理當日總經/電子業重大新聞，用 AI 分析利多利空及受影響公司(需要另外申請 Anthropic API key)
4. 帶量突破盤整：近20日盤整幅度 ≤ 10%、今日收盤突破20日高、量 ≥ 5日均量1.5倍、漲幅 > 3%

## 建置進度

- [x] 專案骨架、共用工具(FinMind 查詢 + Telegram 推播)
- [x] 探測 FinMind 帳號權限(大摩分點代號 1470、法人/股價可全市場查詢、股本用集保資料估算)
- [x] 本地資料庫 + 每日抓取 + 一次性歷史回補
- [x] 功能 1、2、4 實作(功能3新聞+AI分析待補,需要 Anthropic API key)
- [x] GitHub Actions 排程(.github/workflows/daily-scan.yml)
- [ ] 端對端測試中

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
