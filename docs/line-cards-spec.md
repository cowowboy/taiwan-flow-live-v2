# LINE 圖卡體系實作規格

目標專案：`/home/user/taiwan-flow-live-v2`（Cloudflare Worker `worker/src/index.js`）
狀態：**規格待審，尚未實作**。回測依據已完成（`backtest/report_sorting.md`，鐵律 #8 已滿足）。

本文件是動手前的驗收條件（鐵律 #3）。實作者請先確認「未查證事項」一節沒有變成定論。

---

## 1. 核心約束：訊息額度決定架構

LINE 的計費單位是**收訊人數**，不是 message object 數、也不是 bubble 數：

> The number of messages is counted by the number of people you send a message to... The number of message objects in a request doesn't affect the number of messages sent.
> — https://developers.line.biz/en/docs/messaging-api/pricing/#how-to-count-the-number-of-messages-sent

推論出的兩種架構成本（收訊人只有 1 位 = `LINE_USER_ID`，每月約 22 交易日）：

| 架構 | 每月計費訊息 | 佔日本免費方案 200 則 |
|------|------|------|
| **每日 1 次 push、1 個 carousel 裝所有卡** | **22 則** | 11% |
| 每張卡各發一次 push（6 張） | 132 則 | 66% |

**決策：採前者。** 一天一次 push，所有卡當成 carousel 內的 bubble。理由是額度餘裕從 34% 拉到 89%，且未來加卡不增加費用（bubble 上限 12 > 目前 6 張）。

> ⚠️ 200 則是**日本**方案的官方 example，台灣方案數字未查證（見第 7 節）。實作時先打
> `GET https://api.line.me/v2/bot/message/quota` 確認本帳號實際額度，再決定要不要縮頻。

---

## 2. Flex 硬性限制（已查證，附出處）

| 項目 | 限制 | 出處 |
|------|------|------|
| carousel 內 bubble 數 | **最多 12** | https://developers.line.biz/en/reference/messaging-api/#f-carousel |
| carousel 內 bubble `size` | **必須全部相同** | 同上 |
| `altText` | **必填**，上限 **1500 字**，可含 Unicode emoji | https://developers.line.biz/en/reference/messaging-api/#flex-message |
| 單一 bubble JSON | 30 KB | https://developers.line.biz/en/reference/messaging-api/#bubble |
| 單一 carousel JSON | 50 KB | https://developers.line.biz/en/reference/messaging-api/#f-carousel |
| 整個 HTTP request | 2 MB，超過回 `413` | https://developers.line.biz/en/reference/messaging-api/#status-codes |
| 一次 push 的 message object 數 | 1–5，超過回 400 `Size must be between 1 and 5` | https://developers.line.biz/en/reference/messaging-api/#send-push-message |
| push rate limit | 2,000 req/s（非瓶頸） | https://developers.line.biz/en/reference/messaging-api/#rate-limits |
| 超出免費額度 | 回 `429`，**訊息不會送出** | https://developers.line.biz/en/docs/messaging-api/pricing/ |

**注意：`altText` 上限是 1500，不是網路上流傳的 400。**

### 排版能力

- **沒有 table 元件**。官方模型是 CSS Flexbox，表格要用 box 疊：外層 `vertical` box 當表身，
  每列一個 `horizontal` box，欄寬用 `flex` 比例分配（預設 `flex:1`），數值欄 `align:"end"` 右對齊。
  https://developers.line.biz/en/docs/messaging-api/flex-message-layout/
- **`flex:0` 的坑**：元件只佔內容所需寬度，但**超出 box 寬度的部分不會顯示**（直接切掉）。
  固定寬欄位要自己確保不溢出。
- **`baseline` box 的子元件只能是 icon / text / filler**（box、image、button 都不行），
  且不能用 `gravity` / `offsetBottom`。要塞色塊就得改用 `horizontal`。
- **`wrap` 預設是 `false`，溢出文字會被省略號截斷。** 所有多字文字元件**必須顯式設 `wrap:true`**，
  這是最容易漏的一條。`maxLines` 預設 `0` = 全部顯示。
- 顏色可自由指定 hex（`color`），box `backgroundColor` 另支援 alpha（`#RRGGBBAA`）。
  **紅漲綠跌做得到。** 同一段文字內要多色用 `span` 元件。
- **LINE 貼圖式 emoji（`$` placeholder）在 Flex 內用不了**——text 元件沒有 `emojis` 屬性，
  那是獨立 text message object 才有的功能。

---

## 3. 卡別清單（依回測結論，不得自行加碼）

排序欄位一律引用 `backtest/report_sorting.md` 的結論，**不重新發明口徑**。

| # | 卡 | 篩選條件 | 排序 | 回測依據 |
|---|---|------|------|------|
| 1 | 次產業湧入 | C≥1.5 且 R≥1% | **R 值降序**（分離度 0.97%） | `report_sorting.md:126` |
| 2 | 土洋同買 | 湧入訊號 ∩ 投信≥2日 ∩ 外資≥2日 | **外資當日買超金額降序**（Q1 med +1.74%） | `:197` |
| 3 | 突破新高 | **僅** 突破20日新高（單條件） | 未定，見下方待決 | `:150` 交集版陣亡 |
| 4 | 弱勢榜 | 跌破20日新低（排除跌停鎖死） | **量能趨勢降序**（分離度 0.45%） | `:82` |
| 5 | 退出＋法人賣 | 退出訊號 ∩ 法人賣強度<-5% | **不排序**（分離度僅 0.17%） | `:234` |
| 6 | 追高警示 | 爆量大漲 S≥2 R≥2% P≥0.7 | **不排序**，S 值降序呈現 | `:262` |

**卡 3 與卡 5 的偏離說明**：
- 卡 3：M2「突破∩法人買強度>5%」交集版回測陣亡（`:150`），規格退回單條件版。
  單條件版本身的排序欄未在本次 7 項中驗證 → **要嘛補回測，要嘛此卡不排序**。
- 卡 5：報告結論寫「採連續退出日數」（`:234`），但分離度 0.17% 實質等於無排序力。
  本規格改為**不排序**，理由是 0.17% 低於卡 4 的 0.45%，而卡 6 已因同類理由放棄排序。
  此為規格層決定，與報告結論不同，實作前請確認。

### 大盤 Regime 閘門（R1）

6 組訊號中**只有「次產業湧入」超額翻向**（`report_sorting.md:13`），其餘 5 組在多空環境下同向
（`:18`、`:23`、`:28`、`:33`、`:38`）。

**因此 regime 閘門只掛卡 1，不得全面套用。** 判定＝TAIEX 收盤 vs 自身 20MA。

---

## 4. 誠實原則（專案鐵律，不可協商）

回測的關鍵限制：**12 個五分位表全部非單調**。排序只能當「大致分層」，名次不具統計意義。

卡面因此必須遵守：

1. **不標名次序號**（不出現「第1名」「Top 1」），僅依值降序排列。
2. **不用「最強／最弱／必漲／該買」等字眼**；狀態詞中性。
3. **每張卡底部附口徑註記**：`排序依 <欄位>，歷史分離度 <X>%、非單調`。
4. **carousel 末尾固定一張免責 bubble**：「技術指標為現況描述、非買賣訊號，僅供參考」
   （與 `taiwan-stock-news` 分頁頂部免責卡同一組約定）。
5. 不做預測宣稱，只描述歷史統計傾向與局限（鐵律 #8）。

---

## 5. 實作接點

現有程式（`worker/src/index.js`）：

- `lineRequest(token, userId, text)` :1269-1273 — payload 硬寫 `messages:[{type:"text",text}]`
- `sendAlert(env, text, fetchFn)` :1276-1293 — **簽章只吃 text 字串**，webhook 與 LINE 共用同一份
- `alertJob(env, tp, tag, text)` :1302-1314 — 已有週末守門 ＋ KV 每日每 tag 去重
- 測試慣例：`worker/test/alerts.mjs` 43 項驗 LINE payload，純函式、免 token、離線可跑

### 必要改動

1. **`lineRequest` 擴充成可帶任意 message object**，保持既有 text 呼叫端不變（預設仍包 text）。
   純函式性質必須保留——這是離線測試的前提。
2. **新增 Flex 建構器**（建議 `buildCardCarousel(cards)`），純函式、無 I/O，輸出 carousel JSON。
3. **雙通道降級**：Flex 只有 LINE 認得，Discord/Telegram 無法渲染。
   每張卡同時產「Flex 版」與「純文字降級版」，`sendAlert` 依通道選用。
   Flex 建構失敗時 LINE 也退純文字——不可因版型錯誤導致整則不發。
4. **推播時段**：接在既有 `evening` 晚場協調班（台北 21:00–23:55 每 5 分）之後，
   資料落地後推一次。沿用 KV 去重鍵型式（`alerted:<date>:<tag>`）避免一晚重複推。

---

## 6. 驗收條件

實作完成的定義（鐵律 #6）——逐條可驗，全部離線、免 token：

- [ ] `buildCardCarousel` 為純函式，`node` 直接呼叫可產出 JSON，無網路無 token
- [ ] 產出的 carousel bubble 數 ≤ 12，且所有 bubble 的 `size` 相同
- [ ] carousel JSON 序列化後 < 50 KB；單一 bubble < 30 KB（測試中斷言）
- [ ] `altText` 必存在、長度 ≤ 1500
- [ ] 所有多字 text 元件都有 `wrap:true`（測試遍歷 JSON 樹斷言）
- [ ] 每張卡都有口徑註記行；carousel 末尾有免責 bubble
- [ ] 卡面無名次序號、無「最強／該買」類字眼（測試以字串比對守門）
- [ ] regime 閘門只作用於卡 1（測試：空頭環境下卡 1 被抑制、其餘 5 卡不變）
- [ ] 純文字降級版在 Flex 建構丟例外時仍可產出
- [ ] `worker/test/alerts.mjs` 既有 43 項全過（不得回歸）
- [ ] 新增測試檔納入 `worker/test/`，離線可跑
- [ ] `GET /v2/bot/message/quota` 實測本帳號額度，把實際數字補回本文件第 1 節
- [ ] **線上驗證**：實際推一則到 LINE，手機確認版型正確、紅綠配色正確、無截字
- [ ] fresh-context subagent 驗收通過（改動者不得自驗，鐵律 #3）

---

## 7. 未查證事項（不得寫成定論）

以下沒有官方依據，實作時遇到請實測或走 validate 端點
（`POST https://api.line.me/v2/bot/message/validate/push`）：

1. **台灣免費方案每月則數。** 官方只有日本 example（200 則/月）。
   台灣頁面 `tw.linebiz.com` 被本 session 的 egress policy 擋住，無法查證。
   → 用 `GET /v2/bot/message/quota` 問自己的帳號最準。
2. **carousel 超過 12 bubble 的確切行為**（400／截斷／只渲染前 12）。官方未明寫。
3. **`altText` 超過 1500 字的確切行為**（400／自動截斷）。官方未明寫。
4. **Flex text 元件的字數上限**——官方**確實沒有規定**（逐欄看完 spec 無 "Max character limit"）。
   實務受 30KB/50KB 綁。可寫「無明文上限，以 KB 為準」。
5. **Flex text 內使用 Unicode emoji 是否官方支援。** 只有 `altText` 有明文。
6. **單一 box 子元件數 / 巢狀深度上限。** 官方未規定，同樣以 KB 為實際上限。

> 本節事實來源：LINE 官方將 developers.line.biz 全站內容以 Markdown 發佈於
> GitHub `line/line-developers-docs-source`（查證時 commit `c7dfdeaf`，2026-07-23），
> 另交叉核對官方 OpenAPI spec `line/line-openapi` 的 `messaging-api.yml`。
> `developers.line.biz` 本身被本 session 的 egress policy 擋住，上表 URL 為來源標註，
> 供人工複核用。
