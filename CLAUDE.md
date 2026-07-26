# CLAUDE.md — taiwan-flow-live-v2 接手速覽

<!-- CANON:BEGIN v1 -->
<!-- 唯一事實來源＝shihpc/claude-harness 的 CANON.md。以下區塊在五個 repo 的 CLAUDE.md 頂端
     有 byte-identical 逐字副本，由各 repo 的 .github/workflows/canon.yml 守門（比對 sha256）。
     改動流程：先改 claude-harness/CANON.md → 跑 tools/sync_canon.py 同步五份 → 更新守門 hash。
     不要只改單一 repo，CI 會擋下來。 -->

## 通用工作鐵律（五個 repo 逐字相同，勿單獨修改）

1. **機密**：token／金鑰一律走 `.env` 或 Actions secret，絕不寫進任何會 commit 的檔案、log 或
   對話輸出。commit 前用 `git diff --staged` 檢查有無夾帶金鑰樣式字串（`sk-ant-`、`ghp_`、`eyJ` 開頭）。
2. **指揮官不下場**：掃 repo、通讀 >300 行的檔、一次讀 >3 個檔、查網頁研究、批次改檔、
   驗收改過的東西——這六類一律派 subagent，主對話只收結論＋`檔案:行號`。
3. **先寫驗收條件再動手**：動手前先寫下目標專案完整路徑＋怎樣算完成＋怎麼驗。改完派
   fresh-context subagent 驗收——**改東西的 agent（含主對話自己）不得擔任驗收者**。
4. **不確定不亂說**：陳述事實（尤其技術細節、數字、外部服務的限制與行為）要嘛附佐證（官方
   文件、實測、`檔案:行號`），要嘛明說「這點我不確定，需要查證」，不可憑印象當確定講。
   區分「已驗證事實」與「推測」，推測要標明。
5. **一次只做一件事**：只做明確要求的那件事，做完給簡短結果；少主動丟一堆延伸提案。
6. **完成的定義**：驗收條件逐條打勾＋fresh-context subagent 驗過＋產物在使用者拿得到的位置。
   **沒實跑過不算完成**。涉及部署者另需 push＋部署 workflow 成功＋**線上驗證本次變更的具體內容**
   （破快取 raw URL／curl／瀏覽器實查），只寫在本機不算完成。
7. **push 前**：先 `git fetch`；`git log --oneline main..origin/main` 非空必須先看內容（訊息／
   時間戳／diff）。一般 push → rebase 整合，嚴禁直接覆蓋；force push 前若 origin 領先的 commit
   是真實新工作 → 停下來問，授權「這次 force push」不等於授權蓋掉 origin 所有領先 commit。
8. **新指標／訊號先問有沒有回測依據**，沒有就先驗證再上線；不做預測宣稱，只描述歷史統計
   傾向與局限。
9. **語言**：對話與文件用繁體中文；程式碼註解可中文，identifier 用英文。

> 判準細則、派工模板、教訓簿見 `shihpc/claude-harness`（private）。雲端 session 需 add_repo 才讀得到。
<!-- CANON:END v1 -->

台股盤中即時資金流向監控站，同時是「股市雷達」四站家族的**資料中樞**
（`PROJECT_SUMMARY.md:386`）。線上 https://shihpc.github.io/taiwan-flow-live-v2/ 。
前端是單檔 `index.html`（150KB），7 個 tab：即時一覽／產業別／產業鏈／成交佔比／
資金湧入／資金退出＋摘要分析（`index.html:145-151`）。
**`PROJECT_SUMMARY.md`（50KB）是本專案主記憶，接手先讀它**（「快速接手」段有未解問題）。

## 佈局

- `src/` Python 夜間 builder（morning/aetf/baseline/daysummary/us/intraday…）；
  `worker/` Cloudflare Worker（`src/index.js` 單檔＋`wrangler.toml`＋`test/` 14 支）；
  `data/` 產出 JSON（姊妹站上游）；`backtest/`；`.github/workflows/`（10 支＝9 支兜底備援
  ＋ `canon.yml` 守 CLAUDE.md 頂端的 CANON 區塊）

## Worker 哨兵（跨 repo 觸發中樞，改動前必讀）

程式在 `worker/src/index.js` 的 `runSentinel`（:654-675），設計說明 :547-558。

- **cron**：`"*/5 9-14 * * 1-5"`（`worker/wrangler.toml:35`）＝ UTC 09:00–14:55
  ＝ **台北 17:00–22:55、週一至五、每 5 分**。程式端二次守門 `scheduledRole`
  （:597-599）：`weekday && hour>=17 && hour<23 && minute%5===0` 才回 `sentinel`。
- **探測法**（`probeSignal` :645-653）：對每個未完成訊號打 FinMind
  `dataset=<X>&data_id=2330&start_date=end_date=今日`（最便宜的請求，不掛 cf 快取）。
- **落地判定** `signalLanded()`（:603-607）：今日資料非空即算落地；`daytrade` 另要求
  某列 `Volume>0`——FinMind 會先出空殼列、量值晚到。
- **四訊號 → 觸發對象**（`SENTINEL_SIGNALS` :561-570）：
  | 訊號 | dataset | dispatch 目標 |
  |------|---------|---------------|
  | `inst` 法人買賣超 | TaiwanStockInstitutionalInvestorsBuySell | `taiwan-flows` / `daily.yml` |
  | `holding` 集保持股（約 21:00 後） | TaiwanStockShareholding | `taiwan-flows` / `daily.yml`（冪等重跑補持股欄）|
  | `margin` 融資券 | TaiwanStockMarginPurchaseShortSale | `postmkt` / `build.yml` |
  | `daytrade` 當沖（約 21:30 後才非零） | TaiwanStockDayTrading | `postmkt` / `build.yml` |
- **dispatch**（:611-631）：`POST api.github.com/repos/shihpc/<repo>/actions/workflows/
  <wf>/dispatches`，body `{ref:"main"}`，回應非 204 即拋錯。
- **冪等**：KV 鍵 `sentinel:<YYYYMMDD>:<signal>`（`sentinelKey` :602），值 `"dispatched"`，
  TTL 172800（2 天）（:668）；四訊號全寫入則當晚短路，只讀 KV 不打 FinMind（:656-658）。
- **dispatch 失敗不寫 KV**（:670-673）→ 下一輪（5 分後）自動重試。
- **不變式：下游 GitHub cron 全數保留為兜底，一條不刪**（`PROJECT_SUMMARY.md:58`）——
  `taiwan-flows/daily.yml` 台北 21:19、`postmkt/build.yml` 21:53，兩管線冪等，重跑無害。

## 其他 scheduled 角色（分流入口 `dispatchRoleForCron` :779 → `scheduledRole` :589）

- `frame`：台北 09:00–13:59 每分鐘存 KV frame ＋ `runAlerts`
- `news`：每日（含週末）06:07–22:07 每小時 :07 → `taiwan-stock-news/build-news.yml`
- `morning`：平日 06:47 → 本 repo `morning.yml`
- `evening` 晚場協調班：台北 21:00–23:55 每 5 分，串 pm summary → diag → mktbal → aetf2
- `health` 健檢班：台北 23:50、09:30，只盤點產物落地與否、不 dispatch

## 資料是姊妹站上游（跨站變更）

`taiwan-stock-news` 讀 `data/morning.json`；`postmkt` 讀 `data/aetf/latest.json`（含
`stocks[code][3]` 市值欄）。**改輸出格式屬跨站變更**（`PROJECT_SUMMARY.md:486`）。

## 驗證方式

```bash
cd worker && npm run dev            # 本機 Worker
cd worker && npm run deploy         # 部署
cd worker && npm test               # 注意：只跑 test/parity.mjs
node test/sentinel.mjs              # 其餘 13 支要個別跑（離線、免 token）
npx wrangler tail                   # 線上即時觀測 scheduled 事件成敗
```

密鑰全走 `wrangler secret put`、不寫檔：`FINMIND_TOKEN`、`GH_DISPATCH_TOKEN`
（fine-grained PAT，對 taiwan-flows／postmkt／taiwan-stock-news 三 repo Actions 讀寫）、
`ALERT_WEBHOOK`、`LINE_TOKEN`、`LINE_USER_ID`。

## 已知限制／坑

1. KV **list** 免費版僅 1000 次/日、曾爆額度；改用時間索引 key 讓 `pickFrames` 只用
   get（`PROJECT_SUMMARY.md:458`）。
2. 告警由 Worker 自己發，**Worker 整個掛掉時發不出**（`PROJECT_SUMMARY.md:47`）。
3. 2026-07-24 盤中 frame 班整天沒落格：`series:<date>` 是 TW 班的交易日守門，
   缺它會讓**所有 TW 主觸發被靜默跳過**（`PROJECT_SUMMARY.md:7-13`，未結案）。
4. 版控曾發生 force-push 誤刪 98 commit 事故並救回（`PROJECT_SUMMARY.md:462`）。
