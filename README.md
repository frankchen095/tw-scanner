# 台股盤後掃描推播

每天收盤後自動抓 FinMind 與公開資料,算出各策略的篩選結果,用 **LINE** 推播,用 GitHub Actions 排程
(台灣時間週一~週五 21:00;GitHub 的排程常比設定時間晚幾小時才啟動,手動觸發則馬上跑,
手動觸發時勾選 dry_run 可以只跑完整流程、不推播)。

> 這是程式依公開資料自動整理的篩選結果,不是投資建議;歷史命中率不代表未來表現。

## 目前推播的內容(第一、二階段)

推播順序:交集榜 → 策略一~五 → 策略七 → 策略八、九 → 策略六。(成效追蹤在第三階段,只在每週五推播。)

共同規則:股票池 = 上市 + 上櫃普通股(排除興櫃、ETF、ETN、權證、存託憑證、特別股);
有寫市值條件的策略一律 **市值 > 500 億**,市值用 FinMind `TaiwanStockMarketValue`(= 收盤價 × 已發行股數);
跟股價高低點有關的計算一律用還原股價 `TaiwanStockPriceAdj`;抓不到資料就標「今日無資料」,不編造、不估算。

| 策略 | 內容 |
|---|---|
| 交集榜 | 同時出現在兩類以上偏多策略(策略一、策略二今日買超榜、策略五、策略七、法人[策略八+九算一類];策略四不納入)。若同時在策略三或策略二賣超榜,標「訊號衝突」 |
| 策略一 創新高策略 | 市值>500億,收盤低於近60日(還原)收盤高點,且再漲3%內就創高;依距離由近到遠,全部列出 |
| 策略二 籌碼追蹤 | 前40大分點(`data/tracked_brokers.json`,fenpoint 回測命中率排序)今日合計買超前十、賣超前十、近3日合計買超前十。只掃分點日報查得到的股票(成交金額前300名 + 當天漲停且市值>500億) |
| 策略三 漲停放空 | 市值>500億,漲停價成交量 ÷ 當日總成交量 > 30%;標示能不能融券 |
| 策略四 當日營收公布 | 市值>500億、今天新公布月營收,依 MoM 由大到小,標創歷史/N個月新高新低 |
| 策略五 創新高 | 市值>500億,今日收盤創近60個交易日(還原)收盤新高 |
| 策略七 地緣分點買進 | 兩種:(A)〔同區〕分點跟公司總部在同一個縣市+鄉鎮市區,公司總部不在臺北市/新北市、市值>500億,同區分點合計淨買超(單日或近5日)>5,000萬;(B)〔庫藏股〕公司過去執行庫藏股時用的分點(不限市值),該分點淨買超(單日或近5日)>5,000萬,公司目前正在執行庫藏股標〔庫藏股執行中〕。每檔一行:個股(縣市+區)〔同區/庫藏股〕單日 X 萬 / 5 日 X 萬,主要買超分點:… |
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
- **策略七 A(同區分點)的地址**:公司地址只寫「新竹科學園區」沒寫區的(台積電、聯發科、聯電、瑞昱…約 30 家,
  清單在 `data/geo_unparsed.txt`)沒辦法判斷是哪個區,所以不參與比對,我不猜。你確認後,把縣市+區填進
  `data/geo_overrides.json`(格式見 `scripts/geo.py` 最上面),下次每月更新對照表就會生效。
  地址有做的固定處理:台/臺統一、異體字「巿」改「市」、去掉開頭的郵遞區號和「新竹科學園區」、
  「北市…」視為臺北市、14 個縣轄市(彰化市、竹北市…)補上縣名。
- **策略七 B(庫藏股分點)是推算出來的**:公告不會寫用哪個分點,用 fenpoint 的分點歷史資料(400 檔、
  2023-01 起)推算,兩層規則,第一層沒辨識出來的才用第二層:
  (1)『股數對上』:買回期間內累計淨買超股數與公告已買回股數誤差 ≤ 5%,而且『持續買超』(這個分點有
  淨買超的交易日占它有交易的天數 ≥ 70%),剛好只有 1 個分點符合。不設賣出上限(你決定的);持續買超是用來
  擋『一般營業處買買賣賣、淨買超數字剛好碰巧接近』的巧合。
  (2)『穩定買超』:看公布實施庫藏股後哪個分點穩定在買——有淨買超的天數 ≥ 70%、交易天數 ≥ 買回期間的
  一半、淨買超最多,而且 ≥ 第二名的 2 倍(或只有它一個),淨買超落在公告股數的 70%~130%。
  驗證:第一層辨識出的 43 筆,穩定買超第一名有 36 筆是同一個分點。
  結果:592 筆計畫(369 家公司)中,辨識成功 71 筆、53 家公司(第一層 43 筆/35 家,第二層 28 筆/24 家),
  其中市值>500億的 20 家;策略七 B 不限市值,53 家都會追蹤。第一層的 43 筆裡仍有 7 筆賣出占比超過 10%
  (例如保瑞 64%),可能是巧合。`data/buyback_match_report.csv` 可以看到每一筆用哪一層、賣出占比、買超天數占比。
  去年 4 月(關稅風暴)是庫藏股最多的月份,135 筆計畫(平常一個月約 10~30 筆),其中 47 筆在分點資料範圍內,辨識成功 15 筆。
  公告的『實際已買回股數』要等計畫執行完畢才會填,所以執行中的計畫還不能用來推算,只能標「庫藏股執行中」。
  公司規模小的庫藏股,分點淨買超常常到不了 5,000 萬的門檻,所以這一類平常不太會出現。
  完整的處理結果在 `data/buyback_match_report.csv`。
- **FinMind 的「市值表」比其他資料晚更新**(2026-10-07 實測:股價、法人、分點日報 21:00 都有,市值表 21:25 還沒有)。
  所以『市值>500億』的門檻,當天的市值表還沒更新時**用前一個交易日的市值表**(你決定的,這樣比較快),
  報表最後會註明用的是哪一天的;當天的有了就用當天的。(之前試過『等到市值表更新再推播』,改成現在這樣。)
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
| 公司總部地址 | 公開資訊觀測站 `mopsfin.twse.com.tw/opendata/t187ap03_L.csv`(上市)、`..._O.csv`(上櫃),欄位「住址」,每月更新 |
| 券商分點地址 | 證交所 `openapi.twse.com.tw/v1/opendata/OpenData_BRK02`,欄位「地址」(證券商代號 = FinMind 的 securities_trader_id),每月更新 |
| 庫藏股買回計畫 | 公開資訊觀測站 `mopsov.twse.com.tw/mops/web/ajax_t35sc09`(一次請求回傳全部公司,每天更新最近 180 天) |
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
python scripts/07_geo_report.py                                 # 地緣分點:符合條件的家數、解析不出區的公司清單
python scripts/06_build_buyback_brokers.py                      # (一次性)推算庫藏股分點,輸出 data/buyback_brokers.json
python scripts/02_fetch_daily.py --date 2026-10-06 --extra-only # 只補抓策略七涉及股票的分點日報
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
│   ├── geo.py             地址解析、同區分點(策略七 A)
│   ├── mops_buyback.py    庫藏股買回計畫(策略七 B)
│   ├── common.py          FinMind / Claude 呼叫
│   └── reports/           s1_s5_highs、s2_brokers、s3_limit_up、s4_revenue、s7_geo_brokers、
│                          s8_s9_institutional、intersection、feature3_news(策略六)
├── data/
│   ├── scanner.db         每天由 GitHub Actions 自動存回 repo
│   ├── tracked_brokers.json
│   ├── buyback_brokers.json     庫藏股分點(06 腳本產生)
│   ├── geo_overrides.json       (選用)人工補登公司/分點的縣市+區
│   └── geo_unparsed.txt         解析不出區的大市值公司清單(07 腳本產生)
└── .github/workflows/     daily-scan.yml(每日排程)、test-news.yml(單獨測策略六)
```

## 資料庫維護紀錄

- 2026-10:修正兩個舊錯誤。(1)`daily_dama` 主鍵會讓同分點不同價位互相覆蓋,分點金額嚴重少算
  (例:大摩 2330 在 10/05 實際淨買超約 59.7 億,舊算法只剩約 13 億),改成先逐價位算金額再加總;
  舊資料已清除。(2)`get_shares` 把集保表的 total 列一起加總,股數變兩倍,已修正並清除快取。
