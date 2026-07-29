// LINE 圖卡發送層＋排程接線（Phase B2）離線單元測試（無需 token/網路——fetch/KV 全 mock）
// 執行：cd worker && node test/cardsend.mjs
// 守的規格：docs/line-cards-spec.md 第 5 節（發送層）、9.3（判空/預過濾/退純文字/KV 去重）。
import { pushDailyCards, fetchCardSources, cardSourceUrls, CARDS_PUSH_AFTER_MIN,
  alertedKey, runEvening } from "../src/index.js";

let pass = 0, fail = 0;
function chk(name, ok, detail) {
  if (ok) { pass++; } else { fail++; console.log(`  x ${name}  ${detail || ""}`); }
}

// ---- mock 基座（沿用 test/summary.mjs 慣例）----
function fakeKV(init = {}) {
  const m = new Map(Object.entries(init));
  return {
    _m: m,
    async get(k, type) { const v = m.get(k); if (v === undefined) return null; return type === "json" ? (typeof v === "string" ? JSON.parse(v) : v) : v; },
    async put(k, v) { m.set(k, v); },
  };
}
const TODAY = "2026-07-21";
const DEDUP_KEY = alertedKey(TODAY, "cards");            // alerted:20260721:cards
const TP = { date: TODAY, hour: 22, minute: 30, dow: 2 };   // 台北週二 22:30（窗下緣）
const TRADING_KV = (extra = {}) => fakeKV({ [`series:${TODAY}`]: [{ t: "09:00", amt: 1 }], ...extra });
const ENV_BASE = { DATA_BASE: "https://raw.githubusercontent.com/shihpc/taiwan-flow-live-v2/main/data" };
const WEBHOOK = "https://discord.example/api/webhooks/1/x";
const ENV_LINE = { ...ENV_BASE, LINE_TOKEN: "tk", LINE_USER_ID: "U1" };
const URLS = cardSourceUrls(ENV_BASE, TODAY);

// mock fetch：api.line.me / webhook → 依 mode 回應並記 payload；/dispatches → 204；
// 其他 URL → byUrl 表（undefined 代表 404）。spy 記所有呼叫（URL 去 query）。
const mkFetch = (byUrl, spy = [], mode = {}) => async (u, init) => {
  const s = String(u).split("?")[0];
  if (s.startsWith("https://api.line.me/")) {
    const body = JSON.parse(init.body);
    const isFlex = body.messages[0] && body.messages[0].type === "flex";
    spy.push({ url: s, kind: isFlex ? "line-flex" : "line-text", body });
    if (mode.lineFailAll || (mode.lineFailFlex && isFlex)) return { ok: false, status: 500 };
    return { ok: true, status: 200, json: async () => ({}) };
  }
  if (s === WEBHOOK) { spy.push({ url: s, kind: "webhook", body: JSON.parse(init.body) }); return { ok: true, status: 200 }; }
  if (s.includes("/dispatches")) { spy.push({ url: s, kind: "dispatch" }); return { status: 204 }; }
  spy.push({ url: s, kind: "product" });
  const obj = byUrl[s];
  return { ok: obj != null, status: obj ? 200 : 404, json: async () => obj };
};
const lineCalls = (spy) => spy.filter((c) => c.kind === "line-flex" || c.kind === "line-text");
const productUrls = (spy) => new Set(spy.filter((c) => c.kind === "product").map((c) => c.url));

// ---- fixture（縮減自 2026-07-24 真檔，與 test/dailycards.mjs 同款、日期改為 TODAY）----
function mkTotals(days = 22) {   // taiex 緩升 → 末日 > 20MA → bull；含一筆 null（實檔同款情境）
  const dates = [], rows = {};
  for (let i = 0; i < days; i++) {
    const d = `2026-06-${String(i + 1).padStart(2, "0")}`;
    dates.push(d);
    rows[d] = { tse: { f_net_k: -1, t_net_k: 1, d_net_k: -1, turnover_k: 1 },
      otc: { f_net_k: -1, t_net_k: 1, d_net_k: -1, turnover_k: 1 },
      taiex: i === 3 ? null : 44000 + i * 100 };
  }
  return { dates, rows };
}
const FULL = () => ({
  [URLS.baseline]: {   // Phase A 後 schema：stocks [a5,it,fi,y1,y2,ints,nl,its,nh,a20]
    date: TODAY, tot5: 1109333144542,
    stocks: {
      "3231": [5e9, 2, 3, 1, 0, 12.3, 0, 0, 0, 4e9],
      "2454": [9e9, 2, 2, 1, 1, 8.0, 0, 0, 0, 7e9],
      "2330": [5e10, 0, 1, 0, 0, 3.2, 0, 0, 1, 4.5e10],
      "3481": [8e8, 0, 0, 0, 0, -1.0, 1, 0, 0, 1e9],
      "6669": [6e9, 1, 0, -1, 0, -12.5, 0, 2, 0, 5e9],
    },
    subs_y: { "運算設備": [1, 0, 2.1, 0.034], "晶圓製造": [1, 1, 1.8, 0.021] },
  },
  [URLS.flowsDaily]: {
    date: TODAY,
    cols: ["code", "close", "chg_pct", "vol", "amt", "t_net", "t_amt", "f_net", "f_amt", "d_net", "d_amt",
      "t_inv", "f_shares", "f_pct", "f_buy", "f_sell", "t_buy", "t_sell", "d_buy", "d_sell"],
    rows: [
      ["3231", 179, 3.17, 32435, 12000000, 100, 500000, 32435, 5805883, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
      ["2454", 1300, 1.2, 5000, 9000000, 50, 300000, 3000, 3290026, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
      ["2330", 2340, -2.9, 60000, 140000000, 0, 0, -190, -446500, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
      ["3481", 49.5, -3.0, 20000, 990000, 0, 0, -100, -49500, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
      ["6669", 3950, -1.5, 3000, 11850000, -50, -197500, -200, -790000, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    ],
  },
  [URLS.totals]: mkTotals(),
  [URLS.daysummary]: {
    date: TODAY,
    index: { tse: { val: 43654.84, chgP: -1195.97, chg: -2.66, amt_yi: 7780.1 },
      otc: { val: 377.63, chgP: -14.48, chg: -3.69, amt_yi: 1862 } },
    breadth: { up: 612, down: 1483 },
    tone: "大盤 -1196.0 點（-2.66%）；廣度 漲612／跌1483。",
    stocks_top5: [{ c: "6669", n: "緯穎", pts: 15.29 }], stocks_bot3: [{ c: "2330", n: "台積電", pts: -451.39 }],
    subs_top5: [{ n: "電信服務業", pts: 7.64, amt_yi: 55.6, share_pct: 0.7, n_stk: 4 }],
    subs_bot3: [{ n: "晶圓製造", pts: -544.89, amt_yi: 1483.9, share_pct: 19.1, n_stk: 15 }],
    chain_top5: [{ n: "電信網路", pts: 8.1, amt_yi: 60.2, share_pct: 0.8, n_stk: 6 }],
    chain_bot3: [{ n: "半導體", pts: -600.4, amt_yi: 2100.5, share_pct: 27.0, n_stk: 40 }],
    subs_all: [{ n: "電信服務業", pts: 7.64, amt_yi: 55.6, share_pct: 0.7, n_stk: 4 }],
    share_top: { n: "晶圓製造", pts: -544.89, amt_yi: 1483.9, share_pct: 19.1, n_stk: 15 },
    pts_top: { n: "電信服務業", pts: 7.64, amt_yi: 55.6, share_pct: 0.7, n_stk: 4 },
  },
});

// ---- ① 時間守門：<22:30 不推（零 fetch 零 KV 寫）----
{
  const spy = [];
  const kv = fakeKV();
  const out = await pushDailyCards({ ...ENV_LINE, FLOW_KV: kv },
    { date: TODAY, hour: 22, minute: 25, dow: 2 }, mkFetch(FULL(), spy));
  chk("22:25 → waiting before-22:30", out.waiting === "before-22:30", JSON.stringify(out));
  chk("22:25 → 零網路零 KV", spy.length === 0 && kv._m.size === 0);
  chk("CARDS_PUSH_AFTER_MIN = 22:30", CARDS_PUSH_AFTER_MIN === 22 * 60 + 30);
  const late = await pushDailyCards({ ...ENV_LINE, FLOW_KV: fakeKV() },
    { date: TODAY, hour: 23, minute: 55, dow: 2 }, mkFetch({}, []));
  chk("23:55（窗尾）仍在守門內側（非 waiting）", late.waiting !== "before-22:30", JSON.stringify(late));
}

// ---- ② 通道守門：LINE/webhook 皆未設 → 靜默零 fetch ----
{
  const spy = [];
  const out = await pushDailyCards({ ...ENV_BASE, FLOW_KV: fakeKV() }, TP, mkFetch(FULL(), spy));
  chk("無通道 → skipped no-channel＋零網路", out.skipped === "no-channel" && spy.length === 0, JSON.stringify(out));
}

// ---- ③ KV 去重：已有鍵 → 不 fetch 不推 ----
{
  const spy = [];
  const out = await pushDailyCards({ ...ENV_LINE, FLOW_KV: fakeKV({ [DEDUP_KEY]: "pushed" }) },
    TP, mkFetch(FULL(), spy));
  chk("已推過 → skipped already-pushed", out.skipped === "already-pushed", JSON.stringify(out));
  chk("已推過 → 零網路", spy.length === 0);
}

// ---- ④ 交易日守門：baseline date ≠ 今日 → 不推、不寫 KV（下輪再看）----
{
  const spy = [];
  const kv = fakeKV();
  const byUrl = FULL();
  byUrl[URLS.baseline] = { ...byUrl[URLS.baseline], date: "2026-07-18" };
  const out = await pushDailyCards({ ...ENV_LINE, FLOW_KV: kv }, TP, mkFetch(byUrl, spy));
  chk("baseline 非今日 → skipped", out.skipped === "baseline-not-today" && out.baselineDate === "2026-07-18", JSON.stringify(out));
  chk("baseline 非今日 → 無 LINE 呼叫", lineCalls(spy).length === 0);
  chk("baseline 非今日 → 不寫 KV（遲到下輪重試）", !kv._m.has(DEDUP_KEY));
  const gone = await pushDailyCards({ ...ENV_LINE, FLOW_KV: fakeKV() }, TP, mkFetch({}, []));
  chk("baseline 缺檔 → 同樣 skipped", gone.skipped === "baseline-not-today" && gone.baselineDate === null, JSON.stringify(gone));
}

// ---- ⑤ 0 卡 → 不推、KV 記 skip ----
{
  const spy = [];
  const kv = fakeKV();
  const byUrl = { [URLS.baseline]: { date: TODAY, stocks: {}, subs_y: {} } };   // 無任何訊號、其餘源全缺
  const out = await pushDailyCards({ ...ENV_LINE, FLOW_KV: kv }, TP, mkFetch(byUrl, spy));
  chk("0 卡 → skipped no-cards", out.skipped === "no-cards", JSON.stringify(out));
  chk("0 卡 → 無 LINE 呼叫", lineCalls(spy).length === 0);
  chk("0 卡 → KV 記 skip-empty", kv._m.get(DEDUP_KEY) === "skip-empty");
}

// ---- ⑥ 正常路徑：13 支 fetch、flex carousel、altText 帶序號、成功寫 KV ----
{
  const spy = [];
  const kv = fakeKV();
  const out = await pushDailyCards({ ...ENV_LINE, ALERT_WEBHOOK: WEBHOOK, FLOW_KV: kv },
    TP, mkFetch(FULL(), spy));
  chk("正常 → sent via line-flex＋webhook", out.sent === true
    && out.via.includes("line-flex") && out.via.includes("webhook"), JSON.stringify(out));
  chk("正常 → 卡數>0（來源缺的卡進 skipped 不擋）", out.cards > 0 && out.skippedCards > 0, JSON.stringify(out));
  const wanted = new Set(Object.values(URLS));
  const got = productUrls(spy);
  chk("正常 → 13 支來源 JSON 全打過", wanted.size === 13 && [...wanted].every((u) => got.has(u)),
    `wanted=${wanted.size} got=${got.size}`);
  const lc = lineCalls(spy);
  chk("正常 → LINE 恰一次 push", lc.length === 1 && lc[0].kind === "line-flex");
  const msgs = lc[0].body.messages;
  chk("正常 → payload 是 flex carousel", msgs.length >= 1 && msgs.length <= 5
    && msgs.every((m) => m.type === "flex" && m.contents.type === "carousel"), JSON.stringify(msgs.map((m) => m.type)));
  chk("正常 → altText 帶序號 N/M＋日期", msgs.every((m, i) =>
    m.altText === `股市雷達 盤後圖卡 ${i + 1}/${msgs.length}｜${TODAY}`), msgs.map((m) => m.altText).join(" | "));
  const wh = spy.find((c) => c.kind === "webhook");
  chk("正常 → webhook 收純文字版（Discord content）", typeof wh.body.content === "string"
    && wh.body.content.includes("【股市雷達 盤後圖卡】"), wh && JSON.stringify(wh.body).slice(0, 80));
  chk("正常 → 成功寫 KV pushed", kv._m.get(DEDUP_KEY) === "pushed");
  // 再喚醒 → 去重短路
  const spy2 = [];
  const again = await pushDailyCards({ ...ENV_LINE, ALERT_WEBHOOK: WEBHOOK, FLOW_KV: kv },
    { ...TP, minute: 35 }, mkFetch(FULL(), spy2));
  chk("推過再喚醒 → already-pushed 零網路", again.skipped === "already-pushed" && spy2.length === 0);
}

// ---- ⑦ 單支 JSON fetch 失敗 → 照推（該源的卡 skipped）----
{
  const spy = [];
  const kv = fakeKV();
  const byUrl = FULL();
  delete byUrl[URLS.daysummary];   // daysummary 404 → 其 6 張卡 skipped，其餘照組
  const out = await pushDailyCards({ ...ENV_LINE, FLOW_KV: kv }, TP, mkFetch(byUrl, spy));
  chk("單源失敗 → 照推", out.sent === true && out.cards > 0, JSON.stringify(out));
  const withDs = await pushDailyCards({ ...ENV_LINE, FLOW_KV: fakeKV() }, TP, mkFetch(FULL(), []));
  chk("單源失敗 → 該源卡進 skipped（較全源多）", out.skippedCards > withDs.skippedCards,
    `missing=${out.skippedCards} full=${withDs.skippedCards}`);
  const flexStr = JSON.stringify(lineCalls(spy)[0].body);
  chk("單源失敗 → 該源卡不在 payload", !flexStr.includes("市場指數") && !flexStr.includes("今日總結"));
  chk("單源失敗 → 仍寫 KV pushed", kv._m.get(DEDUP_KEY) === "pushed");
}

// ---- ⑧ Flex 推送失敗 → 退純文字重試 ----
{
  const spy = [];
  const kv = fakeKV();
  const out = await pushDailyCards({ ...ENV_LINE, FLOW_KV: kv }, TP,
    mkFetch(FULL(), spy, { lineFailFlex: true }));
  chk("flex 失敗 → 退純文字成功", out.sent === true && out.via.includes("line-text")
    && !out.via.includes("line-flex"), JSON.stringify(out));
  chk("flex 失敗 → errors 記 line-flex", (out.errors || []).some((e) => e.startsWith("line-flex")), JSON.stringify(out.errors));
  const lc = lineCalls(spy);
  chk("flex 失敗 → 兩次 LINE 呼叫（flex→text）", lc.length === 2
    && lc[0].kind === "line-flex" && lc[1].kind === "line-text");
  chk("flex 失敗 → 退路 payload 是 text message", lc[1].body.messages[0].type === "text"
    && lc[1].body.messages[0].text.includes("【股市雷達 盤後圖卡】"));
  chk("flex 失敗但 text 成功 → 寫 KV", kv._m.get(DEDUP_KEY) === "pushed");
}

// ---- ⑨ 全通道失敗 → 拋錯、不寫 KV（下輪自動重試）----
{
  const spy = [];
  const kv = fakeKV();
  let threw = null;
  try { await pushDailyCards({ ...ENV_LINE, FLOW_KV: kv }, TP, mkFetch(FULL(), spy, { lineFailAll: true })); }
  catch (e) { threw = String(e && e.message); }
  chk("全通道失敗 → 拋錯", threw != null && threw.includes("全通道失敗"), threw);
  chk("全通道失敗 → 不寫 KV", !kv._m.has(DEDUP_KEY));
  chk("全通道失敗 → flex 與 text 各試過一次", lineCalls(spy).length === 2);
}

// ---- ⑨b LINE 全敗但 webhook 成功 → 仍寫 KV（一晚一推）、但主通道失守要發告警 ----
{
  const spy = [];
  const kv = fakeKV();
  const env = { ...ENV_LINE, ALERT_WEBHOOK: WEBHOOK, FLOW_KV: kv };
  const out = await pushDailyCards(env, TP, mkFetch(FULL(), spy, { lineFailAll: true }));
  chk("LINE敗+webhook成 → 有送達（via=webhook）", out.sent && out.via.includes("webhook")
    && !out.via.some((v) => v.startsWith("line")), JSON.stringify(out.via));
  chk("LINE敗+webhook成 → 去重鍵已寫（一晚一推）", kv._m.get(DEDUP_KEY) === "pushed");
  chk("LINE敗+webhook成 → 主通道失守告警已發（cards-line-err）",
    kv._m.has(`alerted:${TP.date.replaceAll("-", "")}:cards-line-err`),
    [...kv._m.keys()].join(","));
}

// ---- ⑩ fetchCardSources：單支獨立失敗給 null、getProduct 快取可注入 ----
{
  const spy = [];
  const byUrl = FULL();
  const src = await fetchCardSources(ENV_BASE, TP, mkFetch(byUrl, spy));
  chk("fetchCardSources：有的源給物件、缺的給 null", src.baseline && src.baseline.date === TODAY
    && src.postmkt === null && src.mktbal === null);
  chk("fetchCardSources：無 FINMIND_TOKEN → vix null（不打 FinMind）", src.vix === null
    && !spy.some((c) => c.url.includes("finmindtrade")));
  chk("fetchCardSources：dateStr=tp.date", src.dateStr === TODAY);
  // getProduct 注入（runEvening getP 快取同款）：所有源改走注入函式，fetchFn 不被碰
  const hits = [];
  const src2 = await fetchCardSources(ENV_BASE, TP, () => { throw new Error("不該用到 fetchFn"); },
    { getProduct: async (u) => { hits.push(u); return u === URLS.baseline ? { date: TODAY } : null; } });
  chk("getProduct 注入 → 13 支全走快取層", hits.length === 13 && src2.baseline.date === TODAY);
}

// ---- ⑪ 接線：runEvening 附加步驟（既有鏈不受影響、錯誤被隔離）----
{
  // 22:05（<22:30）喚醒：cards 一律 waiting，不碰網路——與既有 summary.mjs 整合測試同窗
  const spy = [];
  const out = await runEvening({ ...ENV_LINE, GH_DISPATCH_TOKEN: "T", FLOW_KV: TRADING_KV() },
    { date: TODAY, hour: 22, minute: 5, dow: 2 }, mkFetch(FULL(), spy));
  chk("evening 22:05 → cards waiting（時間守門）", out.cards && out.cards.waiting === "before-22:30", JSON.stringify(out.cards));
  chk("evening 22:05 → 無 LINE 呼叫", lineCalls(spy).length === 0);
}
{
  // 22:30 喚醒：cards 實推；既有步驟照常存在
  const spy = [];
  const kv = TRADING_KV();
  const out = await runEvening({ ...ENV_LINE, GH_DISPATCH_TOKEN: "T", FLOW_KV: kv },
    TP, mkFetch(FULL(), spy));
  chk("evening 22:30 → cards sent", out.cards && out.cards.sent === true, JSON.stringify(out.cards));
  chk("evening 22:30 → 既有四步照常回報", "summary" in out && "diag" in out && "mktbal" in out && "aetf2" in out,
    JSON.stringify(Object.keys(out)));
  chk("evening 22:30 → aetf2 不受影響（fired）", out.aetf2 && out.aetf2.fired === true, JSON.stringify(out.aetf2));
  chk("evening 22:30 → KV 記 cards pushed", kv._m.get(DEDUP_KEY) === "pushed");
}
{
  // cards 全通道失敗：錯誤被接線層 try/catch 隔離，走 alertJob（tag cards-err ≠ 去重鍵）
  const kv = TRADING_KV();
  const out = await runEvening({ ...ENV_LINE, GH_DISPATCH_TOKEN: "T", FLOW_KV: kv },
    TP, mkFetch(FULL(), [], { lineFailAll: true }));
  chk("evening cards 失敗 → error 被隔離、不拋出", out.cards && typeof out.cards.error === "string", JSON.stringify(out.cards));
  chk("evening cards 失敗 → 既有步驟不受影響", out.aetf2 && out.aetf2.fired === true);
  chk("evening cards 失敗 → 去重鍵未寫（下輪重試）", !kv._m.has(DEDUP_KEY));
  chk("evening cards 失敗 → 告警鍵 cards-err 與去重鍵不相撞", kv._m.has(alertedKey(TODAY, "cards-err")));
}
{
  // 非交易日：runEvening 開頭 series 守門即短路，cards 不會執行
  const spy = [];
  const out = await runEvening({ ...ENV_LINE, GH_DISPATCH_TOKEN: "T", FLOW_KV: fakeKV() },
    { date: "2026-07-26", hour: 22, minute: 30, dow: 0 }, mkFetch(FULL(), spy));
  chk("evening 非交易日 → 整段 skip 零網路（含 cards）", out.skipped === "non-trading-day" && spy.length === 0);
}

console.log(`\n${fail === 0 ? "PASS" : "FAIL"}  ${pass} 通過 / ${fail} 失敗`);
process.exit(fail === 0 ? 0 : 1);
