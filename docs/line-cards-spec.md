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

**決策：採前者。** 一天一次 push，所有卡當成 carousel 內的 bubble。理由是額度餘裕從 34% 拉到 89%，且未來加卡不增加費用（bubble 上限 12 > 目前 6 張卡＋1 張免責卡＝7）。

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
| 1 | 次產業湧入 | C≥1.5 且 R≥1% | **R 值降序**（分離度 0.97%） | `report_sorting.md:159` |
| 2 | 土洋同買 | 湧入訊號 ∩ 投信**近3日**≥2日買超 ∩ 外資**近3日**≥2日買超 | **外資當日買超金額降序**（Q1 med +1.74%） | `:276` |
| 3 | 突破新高 | **僅** 突破20日新高（單條件） | **法人買強度降序**（分離度 0.92%） | `:229` M4 |
| 4 | 弱勢榜 | 跌破20日新低（排除跌停鎖死） | **量能趨勢降序**（分離度 0.45%） | `:115` |
| 5 | 退出＋法人賣 | 退出訊號 ∩ 法人賣強度<-5% | **不排序**（排序欄平手率 89.8%，分離度不可重現） | `:313` ⚠ |
| 6 | 追高警示 | 爆量大漲 S≥2 R≥2% P≥0.7（**排除漲停鎖死**） | **不排序**，S 值降序呈現 | `:341` |

> 回測依據行號對應 2026-07-26 重跑版 `report_sorting.md`（9 項版，`PYTHONHASHSEED=0`）。

**卡 3 與卡 5 的偏離說明**：
- 卡 3：M2「突破∩法人買強度>5%」交集版回測陣亡（`:183`），規格退回單條件版。
  單條件版的排序欄原本未驗證 → **已於 2026-07-26 實跑 M4 補測**（`run_sorting.py` 的 `run_m4()`）。
  三個候選的分離度：法人買強度 **0.92%**、量能趨勢 0.68%、乖離率 0.31%，
  最佳者達先訂門檻 0.30% → **卡 3 排序欄採法人買強度降序**（`:229`）。
  五分位仍**非單調**（Q1 −0.04% / Q2 +0.11% / Q3 −0.13% / Q4 −0.54% / Q5 −0.96%），
  依第 4 節鐵律只能當大致分層呈現。
  **⚠ 卡 3 的重大限制：母體本身無正超額。** 突破20日新高全體 N=15107、
  T+3 勝大盤僅 41.7%、超額 avg −0.31%；即使排序後的 Q1 也只有 −0.04%、勝率 43.6%。
  排序做到的是「把更差的往後排」，**不是挑出會漲的**。卡面文案不得暗示 Q1 為看多標的。
- 卡 5：報告結論寫「採連續退出日數」（`:313`），但該欄 **89.8% 的樣本值都是 1**
  （分布 0→129、1→1353、2→23、3→2），五分位切點全落在平手值內，分組由迭代順序決定。
  實測同一份快取、同一份程式碼跑五次得到 0.07%／0.18%／0.35%／0.57%／1.36%，
  **分離度不可重現**（最後一筆為獨立驗收者以 `PYTHONHASHSEED=7` 覆核所得）。
  舊規格所寫的「0.17%」只是其中一次的抽樣結果，非穩定量。
  **不排序的結論不變**（已於 2026-07-26 由使用者確認），但理由更正為「排序欄無有效變異」，
  比「分離度低於卡 4」更根本。詳見下方待處理事項。

### 大盤 Regime 閘門（R1）

R1 測了 6 組訊號，其中**只有「次產業湧入」超額翻向**（`report_sorting.md:13`），
另 5 組在多空環境下同向（`:18`、`:23`、`:28`、`:33`、`:38`）。

**但這 6 組訊號 ≠ 本文件的 6 張卡**，對應關係必須看清楚：

| R1 測的訊號 | 對應卡 | 超額翻向 |
|------|------|------|
| 次產業湧入 | 卡 1 | **是 ⚠** |
| 突破20日新高 | 卡 3 | 否 |
| 乖離>+10% 過熱組 | **不對應任何一張卡** | 否 |
| 跌破20日新低 | 卡 4 | 否 |
| 退出＋法人賣 | 卡 5 | 否 |
| 爆量大漲 | 卡 6 | 否 |

**卡 2（土洋同買）完全不在 R1 測試範圍內**——M3 段（`:167-197`）沒有做多空切分。

因此：

- **regime 閘門掛卡 1 與卡 2**，判定＝TAIEX 收盤 vs 自身 20MA。
- 卡 3–6 有「不翻向」的實測依據，確定不掛閘門。
- **卡 2 已於 2026-07-26 實跑 R2 補測**（`run_sorting.py` 的 `run_r2()`，母體與 M3 相同）：
  多頭 N=378 超額 avg **+0.68%**、空頭 N=31 超額 avg **−0.76%**，**超額翻向 ⚠**（`:58`）。
  → **卡 2 補掛 regime 閘門**，空頭環境下抑制，與卡 1 同一判定。
  **⚠ 空頭樣本僅 31 筆**（占母體 409 筆的 7.6%；回測期 227 個有樣本交易日中 186 天為多頭＝82%），
  單月最多 11 筆、其中兩個月各只有 1 筆；且 −0.76% 的均值由 5 筆主導
  （2026-04 N=1 −5.99%、2026-05 N=4 −5.41%），而 2025-12 的 6 筆反而是 +1.87%。
  翻向方向與掛閘門的動作**偏保守**
  （空頭時少發卡，錯了的代價是少賺不是多賠），故仍依此定案；
  但依鐵律 #4，卡面與文件不得將「空頭時土洋同買會轉負」陳述為已充分驗證的事實。
  累積滿 3 個完整空頭月（約 100 筆）後應重跑 R2 覆核。

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
- 測試慣例：`worker/test/alerts.mjs` 43 項離線斷言（其中約 9 項驗 LINE payload），純函式、免 token

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
- [ ] regime 閘門只作用於卡 1 與卡 2（測試：空頭環境下卡 1／卡 2 被抑制、**卡 3–6 不變**）
- [x] R2 已實跑（2026-07-26），卡 2 的 regime 結論已回填本文件
      → **翻向，補掛閘門**；空頭 N=31 的樣本限制已一併記載
- [x] M4 已實跑（2026-07-26），卡 3 的排序欄已依 M4 結論回填本文件
      → 法人買強度 0.92% ≥ 0.30% 門檻，**採用**；母體無正超額的限制已一併記載
- [ ] 卡 3 卡面文案不得暗示 Q1 為看多標的（母體 T+3 勝大盤僅 41.7%）
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

---

## 8. 待處理：回測腳本的平手值不可重現問題（2026-07-26 發現，未修）

**現象**：`run_sorting.py` 的 `quintile()` 對平手值沒有穩定的次要排序鍵，分組結果依賴
Python 的迭代順序，而該順序受 `PYTHONHASHSEED` 影響（預設每次啟動隨機）。

**實證**：同一份 `backtest/cache/`、同一份程式碼，S2 的「連續退出日數」分離度四次分別跑出
0.07%／0.18%／0.35%／1.36%。R2 與 M4 在 seed 0/1/2 下**完全一致**（R2 +0.68%／−0.76%、
M4 法人買強度 0.92%），因為 R2 不做分位、M4 三個候選欄都是連續值幾無平手。

**影響範圍**：只影響平手率高的排序欄。已知 `consec_exit`（S2，89.8% 值為 1）受害；
`consec`（M1 的連續湧入日數）疑似同類，但 M1 已採 R 值故不影響結論。
**六張卡的現行決定均不因此改變。**

**現況處置**：`backtest/report_sorting.md` 以 `PYTHONHASHSEED=0` 產生，行號引用以該版為準。
重跑複核時務必帶同一個 seed，否則 S2 段數字對不上。

**建議修法（未實作，需另案決定）**：在 `quintile()` 排序鍵加上穩定的次要鍵
（如 `(值, 日期, 股票代號)`），使分組與 hash 順序無關。此舉會改動所有分位表的邊界樣本，
六項既有結論需全部重跑覆核，故不在本次 R2／M4 補測範圍內逕行變更。
