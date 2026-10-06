# 台股盤後掃描推播

每天收盤後自動抓 FinMind 與公開資料,算出各策略的篩選結果,用 **LINE** 推播,用 GitHub Actions 排程
(台灣時間週一~週五 21:00;GitHub 的排程常比設定時間晚幾小時才啟動,手動觸發則馬上跑,
手動觸發時勾選 dry_run 可以只跑完整流程、不推播)。

> 這是程式依公開資料自動整理的篩選結果,不是投資建議;歷史命中率不代表未來表現。

## 目前推播的內容(第一階段)

推播順序:交集榜 → 策略一~五 → 策略八、九 → 策略六。(策略七地緣分點在第二階段,成效追蹤在第三階段。)

共同規則:股票池 = 上市 + 上櫃普通股(排除興櫃、ETF、ETN、權證、存託憑證、特別股);
有寫市值條件的策略一律 **市值 > 500 億**,市值用 FinMind `TaiwanStockMarketValue`(= 收盤價 × 已發行股數);
跟股價高低點有關的計算一律用還原股價 `TaiwanStockPriceAdj`;抓不到資料就標「今日無資料」,不編造、不估算。

| 策略 | 內容 |
|---|---|
| 交集榜 | 同時出現在兩類以上偏多策略(策略一、策略二今日買超榜、策略四標「新高」的、策略五、策略七、法人[策略八+九算一類])。若同時在策略三或策略二賣超榜,標「訊號衝突」 |
| 策略一 創新高策略 | 市值>500億,收盤低於近60日(還原)收盤高點,且再漲3%內就創高;依距離由近到遠,全部列出 |
| 策略二 籌碼追蹤 | 前40大分點(`data/tracked_brokers.json`,fenpoint 回測命中率排序)今日合計買超前十、賣超前十、近3日合計買超前十。只掃分點日報查得到的股票(成交金額前300名 + 當天漲停且市值>500億) |
| 策略三 漲停放空 | 市值>500億,漲停價成交量 ÷ 當日總成交量 > 30%;標示能不能融券 |
| 策略四 當日營收公布 | 市值>500億、今天新公布月營收,依 MoM 由大到小,標創歷史/N個月新高新低 |
| 策略五 創新高 | 市值>500億,今日收盤創近60個交易日(還原)收盤新高 |
| 策略六 時事分析 | AI 零組件供應鏈 + 總經。Claude 開 web_search,含新聞、論壇、社群;格式「利多/利空:產業 → 個股 → 原因」;未證實消息標〔未證實〕附出處;個股名稱用股票名單核對,對不上標(?) |
| 策略八 法人買超排行 | 外資+投信合計買超金額,單日前十、近5日前十,分開列外資、投信 |
| 策略九 法人連續買超 | 投信、外資各一張榜:連買3天以上且合計>1億,列連買天數、金額、占期間成交金額比例 |

風險標記(加在名稱後面):〔處置〕〔注意〕〔除權息 月/日〕〔法說 月/日〕(後兩者只標未來5個交易日內)。

### 已知限制(請看這裡)

- **策略四「當天公布」是以 FinMind 入庫日判斷**:FinMind 的 `create_time` 是入庫日,不是公司的精確
  公布時間,可能比實際公布晚(例如台積電 2026 年 6 月營收的入庫日是 7/13 週一)。所以某公司可能在
  公布後的隔天才出現;晚上才公布的,也會在隔天出現,不會漏掉。
- **法說會**:公開資訊觀測站沒有 JSON/CSV,只能解析網頁表格,而且連續請求會被斷線;在 GitHub Actions
  的雲端 IP 可能被擋。抓不到時,報表最後會明確寫「法說會標記可能不完整」,不會假裝沒事。
- **「漲停股被限制平盤下放空」找不到對應的公開資料**(證交所那欄只對「前一天跌停」的股票有意義),
  策略三只標「能不能融券」(暫停融券賣出、停券預告、沒有融資融券資格),不拿處置股當替代。
- 策略一、五用『收盤價』比高點(不是盤中最高價);新上市不足60個交易日的股票無法判斷,會在報表最後列出略過幾檔。
- 融券狀態以報表當天的名單為準(證交所隔天的名單要到晚上8:30以後才公布)。
- 舊的「功能1(大摩+法人排行)、功能2(外資投信同買)、功能4(帶量突破盤整)」已停用,程式檔案還在
  `scripts/reports/`,但沒有被呼叫。

## 資料來源

| 資料 | 來源 |
|---|---|
| 股價、法人買賣超、市值、還原股價、月營收、分點日報、股票基本資料 | FinMind(`TaiwanStockPrice`、`TaiwanStockInstitutionalInvestorsBuySell`、`TaiwanStockMarketValue`、`TaiwanStockPriceAdj`、`TaiwanStockMonthRevenue`、`TaiwanStockTradingDailyReport`、`TaiwanStockInfo`) |
| 處置股 | 證交所 `rwd/zh/announcement/punish`、櫃買 `tpex_disposal_information` |
| 注意股 | 證交所 `rwd/zh/announcement/notice`、櫃買 `tpex_trading_warning_information` |
| 除權息 | 證交所 `exchangeReport/TWT48U_ALL`、櫃買 `tpex_exright_prepost` |
| 法說會 | 公開資訊觀測站 `mopsov.twse.com.tw/mops/web/t100sb02_1`(網頁表格) |
| 融券 | 證交所 `marginTrading/TWT92U`、`BFI84U`;櫃買 `tpex_margin_trading_margin_mark`、`tpex_margin_trading_term` |
| 時事 | Google News RSS(候選標題)+ Claude web_search |

FinMind 方案:SponsorPro(每小時 26,000 次;目前每天約 500~600 次)。

## 本機測試

環境變數(`$env:` 只在那個視窗有效):`FINMIND_TOKEN`、`ANTHROPIC_API_KEY`(策略六)、
`LINE_CHANNEL_ACCESS_TOKEN`、`LINE_USER_ID`(推播)。GitHub 上的設定在 repo 的 Secrets。

```powershell
python scripts/main.py --date 2026-10-06 --dry-run              # 完整跑一次,不推播,印在畫面上
python scripts/main.py --date 2026-10-06 --no-fetch --dry-run   # 不重抓資料,用資料庫現有的
python scripts/main.py --dry-run --no-news                      # 跳過策略六(不需要 ANTHROPIC_API_KEY)
python scripts/02_fetch_daily.py --date 2026-10-06              # 只抓某一天的資料存進資料庫
python scripts/04_line_test.py                                  # 送 LINE 測試訊息,並查額度
python scripts/03_news_test.py                                  # 只測策略六
```

## 資料夾

```
tw-scanner/
├── scripts/
│   ├── main.py            主程式:抓資料 → 算策略 → 組報表 → LINE 推播
│   ├── fetchers.py        每天抓資料(行情、市值、法人、分點日報、月營收)
│   ├── db.py              SQLite 結構與升級、分點名單
│   ├── helpers.py         共用工具(股票池、還原股價、漲停價…)
│   ├── context.py         每個策略共用的「今天的環境」
│   ├── risk_tags.py       風險標記
│   ├── notify.py          LINE 推播(長訊息自動合併/切割)
│   ├── common.py          FinMind / Claude 呼叫
│   └── reports/           s1_s5_highs、s2_brokers、s3_limit_up、s4_revenue、
│                          s8_s9_institutional、intersection、feature3_news(策略六)
├── data/
│   ├── scanner.db         每天由 GitHub Actions 自動存回 repo
│   └── tracked_brokers.json
└── .github/workflows/     daily-scan.yml(每日排程)、test-news.yml(單獨測策略六)
```

## 資料庫維護紀錄

- 2026-10:修正兩個舊錯誤。(1)`daily_dama` 主鍵會讓同分點不同價位互相覆蓋,分點金額嚴重少算
  (例:大摩 2330 在 10/05 實際淨買超約 59.7 億,舊算法只剩約 13 億),改成先逐價位算金額再加總;
  舊資料已清除。(2)`get_shares` 把集保表的 total 列一起加總,股數變兩倍,已修正並清除快取。
