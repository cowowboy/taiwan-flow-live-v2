// summary 事件驅動觸發＋晚場協調班離線單元測試（無需 token、不打真實網路——fetch 全 mock）
// 執行：cd worker && node test/summary.mjs
import { ghDispatchRequest, dispatchRoleForCron, DISPATCH_ROLES, BACKUP_CRONS, FRAME_CRON,
  sumfiredKey, taipeiDayOf, newsFreshW, summaryReady, summarySources, runSummaryDispatch,
  chainStep, runChain, runAetf2, runEvening, backupPipelines, bkfiredKey } from "../src/index.js";

let pass = 0, fail = 0;
function chk(name, ok, detail) {
  if (ok) { pass++; } else { fail++; console.log(`  x ${name}  ${detail || ""}`); }
}

const ENV_BASE = { DATA_BASE: "https://raw.githubusercontent.com/shihpc/taiwan-flow-live-v2/main/data" };

// ---- ghDispatchRequest：inputs 向後相容 ----
{
  const old = ghDispatchRequest("postmkt", "build.yml", "T");
  chk("無 inputs body 與舊版相同", old.init.body === JSON.stringify({ ref: "main" }), old.init.body);
  const withSlot = ghDispatchRequest("postmkt", "summary.yml", "T", { slot: "pm" });
  chk("有 inputs body 帶 slot", withSlot.init.body === JSON.stringify({ ref: "main", inputs: { slot: "pm" } }), withSlot.init.body);
  chk("URL 指向 summary.yml", withSlot.url.includes("/shihpc/postmkt/actions/workflows/summary.yml/dispatches"));
}

// ---- cron 路由：12 條 cron 唯一歸屬、互不衝突 ----
{
  // wrangler.toml 全部 12 條（需與檔案完全一致）
  const ALL = ["* 1-5 * * 1-5", "*/5 9-14 * * 1-5", "7,47 0-14,22-23 * * *",
    "35 5 * * 1-5", "40 6 * * 1-5", "35 10 * * 1-5", "5 12 * * 1-5",
    "*/5 13-15 * * 1-5", "5 21 * * *", "50,55 22 * * *", "*/5 23 * * *", "*/10 0 * * *"];
  chk("12 條 cron 無重複字串", new Set(ALL).size === 12);
  // 前三條（frame/哨兵/news+morning）不得被 dispatchRoleForCron 攔截
  chk("frame cron 不被攔截", dispatchRoleForCron("* 1-5 * * 1-5") === null);
  chk("哨兵 cron 不被攔截", dispatchRoleForCron("*/5 9-14 * * 1-5") === null);
  chk("news/morning cron 不被攔截", dispatchRoleForCron("7,47 0-14,22-23 * * *") === null);
  // 後九條必須各有唯一角色
  chk("daysummary → backup", dispatchRoleForCron("35 5 * * 1-5")?.kind === "backup");
  chk("intraday → backup", dispatchRoleForCron("40 6 * * 1-5")?.kind === "backup");
  chk("aetf → backup", dispatchRoleForCron("35 10 * * 1-5")?.kind === "backup");
  chk("baseline → backup", dispatchRoleForCron("5 12 * * 1-5")?.kind === "backup");
  chk("us → backup", dispatchRoleForCron("5 21 * * *")?.kind === "backup");
  chk("晚場協調班 → evening", dispatchRoleForCron("*/5 13-15 * * 1-5")?.kind === "evening");
  chk("am 起手 → summary-am", dispatchRoleForCron("50,55 22 * * *")?.kind === "summary-am");
  chk("am 主窗 → summary-am", dispatchRoleForCron("*/5 23 * * *")?.kind === "summary-am");
  chk("am 尾窗 → summary-am", dispatchRoleForCron("*/10 0 * * *")?.kind === "summary-am");
  // BACKUP_CRONS 與 DISPATCH_ROLES 鍵不重疊
  const overlap = Object.keys(BACKUP_CRONS).filter((k) => DISPATCH_ROLES[k]);
  chk("backup 與 roles 鍵不重疊", overlap.length === 0, overlap.join(","));
}

// ---- 純函式：taipeiDayOf / newsFreshW / sumfiredKey ----
{
  chk("taipeiDayOf +08:00", taipeiDayOf("2026-07-21T22:16:35+08:00") === "2026-07-21");
  chk("taipeiDayOf UTC 跨日（21T23:30Z=台北22日）", taipeiDayOf("2026-07-21T23:30:00Z") === "2026-07-22");
  chk("taipeiDayOf 無效 → null", taipeiDayOf("not-a-date") === null && taipeiDayOf(null) === null);
  chk("sumfiredKey 格式", sumfiredKey("2026-07-21", "pm") === "sumfired:20260721:pm");
  const today = "2026-07-21";
  chk("news 21:00 整點 → fresh", newsFreshW("2026-07-21T21:00:00+08:00", today, 21) === true);
  chk("news 20:59 → 非fresh", newsFreshW("2026-07-21T20:59:59+08:00", today, 21) === false);
  chk("news 22:16 → fresh", newsFreshW("2026-07-21T22:16:35+08:00", today, 21) === true);
  chk("news 昨日晚班 → 非fresh", newsFreshW("2026-07-20T22:16:35+08:00", today, 21) === false);
  chk("news 缺 → 非fresh", newsFreshW(null, today, 21) === false);
}

// ---- summaryReady：am/pm 矩陣 ----
{
  const today = "2026-07-21";
  const flows = { date: "2026-07-21" }, postmkt = { date: "2026-07-21" },
    news = { generated_at: "2026-07-21T22:16:35+08:00" };
  chk("pm 三源全齊 → ready", summaryReady("pm", { flows, postmkt, news }, today).ready === true);
  chk("pm flows 昨日 → 未齊", summaryReady("pm", { flows: { date: "2026-07-20" }, postmkt, news }, today).reasons.includes("flows-not-today"));
  chk("pm news 早班（<21:00）→ 未齊", summaryReady("pm", { flows, postmkt, news: { generated_at: "2026-07-21T19:07:00+08:00" } }, today).reasons.includes("news-evening-not-ready"));
  chk("pm postmkt 缺 → 未齊", summaryReady("pm", { flows, news, postmkt: null }, today).reasons.includes("postmkt-not-today"));
  chk("pm 全缺 → 三個 reasons", summaryReady("pm", { flows: null, postmkt: null, news: null }, today).reasons.length === 3);
  chk("am morning 今日 → ready", summaryReady("am", { morning: { generated_at: "2026-07-21T07:12:00+08:00" } }, today).ready === true);
  chk("am morning 昨日 → 未齊", summaryReady("am", { morning: { generated_at: "2026-07-20T07:12:00+08:00" } }, today).reasons.includes("morning-not-today"));
  chk("am morning 缺 → 未齊", summaryReady("am", { morning: null }, today).ready === false);
}

// ---- runSummaryDispatch：整合決策（mock KV / mock fetch）----
function fakeKV(init = {}) {
  const m = new Map(Object.entries(init));
  return {
    _m: m,
    async get(k, type) { const v = m.get(k); if (v === undefined) return null; return type === "json" ? (typeof v === "string" ? JSON.parse(v) : v) : v; },
    async put(k, v) { m.set(k, v); },
  };
}
const TP = { date: "2026-07-21", hour: 22, minute: 5, dow: 2 };   // 台北週二 22:05
const TRADING_KV = (extra = {}) => fakeKV({ "series:2026-07-21": [{ t: "09:00", amt: 1 }], ...extra });
// mock fetch：/dispatches → 204；URL → byUrl 表（undefined 代表 404）
const S = summarySources(ENV_BASE);
const mkFetch = (byUrl, spy = []) => async (u, init) => {
  const s = String(u).split("?")[0];
  if (s.includes("/dispatches")) { spy.push({ url: s, body: init && init.body }); return { status: 204 }; }
  const obj = byUrl[s];
  return { ok: obj != null, status: obj ? 200 : 404, json: async () => obj };
};
const SUM_URL = "https://raw.githubusercontent.com/shihpc/postmkt/main/data/summary/20260721-pm.json";
const READY_PM = {
  [S.flows]: { date: "2026-07-21" },
  [S.postmkt]: { date: "2026-07-21" },
  [S.news]: { generated_at: "2026-07-21T22:00:00+08:00" },
};

// 1) 無 token → 靜默
{
  const out = await runSummaryDispatch({ ...ENV_BASE, FLOW_KV: TRADING_KV() }, TP, "pm", mkFetch(READY_PM));
  chk("pm 無 token → skipped", out.skipped === "no-token");
}
// 2) pm 非交易日（無 series）→ skip
{
  const out = await runSummaryDispatch({ ...ENV_BASE, GH_DISPATCH_TOKEN: "T", FLOW_KV: fakeKV() }, TP, "pm", mkFetch(READY_PM));
  chk("pm 非交易日 → skipped", out.skipped === "non-trading-day");
}
// 3) am 週末（dow 6）→ skip；平日不看 series
{
  const out = await runSummaryDispatch({ ...ENV_BASE, GH_DISPATCH_TOKEN: "T", FLOW_KV: fakeKV() },
    { date: "2026-07-25", hour: 7, minute: 5, dow: 6 }, "am", mkFetch({}));
  chk("am 週末 → skipped", out.skipped === "non-trading-day");
  const out2 = await runSummaryDispatch({ ...ENV_BASE, GH_DISPATCH_TOKEN: "T", FLOW_KV: fakeKV() },
    { date: "2026-07-21", hour: 7, minute: 5, dow: 2 }, "am",
    mkFetch({ [S.morning]: { generated_at: "2026-07-21T07:01:00+08:00" } }));
  chk("am 平日無 series 不被擋（06-08 時 series 未誕生）", out2.fired === true);
}
// 4) 冪等：sumfired 已記 → skip
{
  const spy = [];
  const out = await runSummaryDispatch({ ...ENV_BASE, GH_DISPATCH_TOKEN: "T", FLOW_KV: TRADING_KV({ "sumfired:20260721:pm": "fired" }) },
    TP, "pm", mkFetch(READY_PM, spy));
  chk("pm 已 fired → skipped＋不打網路", out.skipped === "already-fired" && spy.length === 0);
}
// 5) 產物防重：線上已有本場檔 → 補記 KV、不 dispatch
{
  const spy = [];
  const kv = TRADING_KV();
  const out = await runSummaryDispatch({ ...ENV_BASE, GH_DISPATCH_TOKEN: "T", FLOW_KV: kv }, TP, "pm",
    mkFetch({ ...READY_PM, [SUM_URL]: { slot: "pm" } }, spy));
  chk("pm 已產出 → skipped already-produced", out.skipped === "already-produced");
  chk("已產出 → 補記 KV＋零 dispatch", JSON.parse(kv._m.get("sumfired:20260721:pm")).s === "produced" && spy.length === 0);
}
// 6) 三源未齊 → waiting、KV 不記、不 dispatch
{
  const spy = [];
  const kv = TRADING_KV();
  const out = await runSummaryDispatch({ ...ENV_BASE, GH_DISPATCH_TOKEN: "T", FLOW_KV: kv }, TP, "pm",
    mkFetch({ ...READY_PM, [S.postmkt]: { date: "2026-07-20" } }, spy));
  chk("pm 未齊 → waiting=[postmkt]", Array.isArray(out.waiting) && out.waiting.includes("postmkt-not-today"));
  chk("未齊 → KV 不記＋不 dispatch", kv._m.get("sumfired:20260721:pm") === undefined && spy.length === 0);
}
// 7) 三源全齊 → dispatch 帶 slot=pm、KV 記 fired
{
  const spy = [];
  const kv = TRADING_KV();
  const out = await runSummaryDispatch({ ...ENV_BASE, GH_DISPATCH_TOKEN: "T", FLOW_KV: kv }, TP, "pm", mkFetch(READY_PM, spy));
  chk("pm 全齊 → fired", out.fired === true);
  chk("dispatch 打 summary.yml", spy[0]?.url.includes("/postmkt/actions/workflows/summary.yml/dispatches"), spy[0]?.url);
  chk("dispatch body 帶 slot=pm", spy[0]?.body === JSON.stringify({ ref: "main", inputs: { slot: "pm" } }), spy[0]?.body);
  chk("KV 記 sumfired（JSON 狀態）", JSON.parse(kv._m.get("sumfired:20260721:pm")).s === "fired");
}
// 8) dry → wouldDispatch、不真發、KV 不記
{
  const spy = [];
  const kv = TRADING_KV();
  const out = await runSummaryDispatch({ ...ENV_BASE, GH_DISPATCH_TOKEN: "T", FLOW_KV: kv }, TP, "pm", mkFetch(READY_PM, spy), { dry: true });
  chk("dry → wouldDispatch＋零副作用", out.wouldDispatch === true && spy.length === 0 && kv._m.get("sumfired:20260721:pm") === undefined);
}

// ---- chainStep / runChain：diag→mktbal 依賴鏈 ----
const pipes = backupPipelines(ENV_BASE);
const diag = pipes.find((p) => p.name === "diag");
{
  const today = "2026-07-21";
  chk("自身今日 → fresh", chainStep(diag, { date: "2026-07-21" }, { date: "2026-07-21" }, today).action === "fresh");
  chk("上游今日、自身舊 → dispatch", chainStep(diag, { date: "2026-07-21" }, { date: "2026-07-20" }, today).action === "dispatch");
  chk("上游舊 → wait-dep", chainStep(diag, { date: "2026-07-20" }, { date: "2026-07-20" }, today).action === "wait-dep");
  chk("上游缺 → wait-dep", chainStep(diag, null, null, today).action === "wait-dep");
}
{
  // runChain：上游今日 → dispatch diag.yml＋KV 記
  const spy = [];
  const kv = TRADING_KV();
  const byUrl = { [diag.dep.url]: { date: "2026-07-21" } };   // diag.json 404、postmkt.json 今日
  const getP = (u) => mkFetch(byUrl, spy)(u).then((r) => (r.ok ? r.json() : null));
  const out = await runChain({ ...ENV_BASE, GH_DISPATCH_TOKEN: "T", FLOW_KV: kv }, TP, diag, getP, mkFetch(byUrl, spy));
  chk("chain 上游備 → fired diag", out.fired === true && spy.some((s) => s.url.includes("/diag.yml/dispatches")));
  // 2026-07-25：值改為 JSON 狀態 {s,ts,n}（dispatch 成功 ≠ job 成功，見 src bkGate）
  chk("chain KV 記 bkfired:diag（fired/第1次）", (() => {
    const st = JSON.parse(kv._m.get("bkfired:20260721:diag"));
    return st.s === "fired" && st.n === 1 && st.ts > 0;
  })(), kv._m.get("bkfired:20260721:diag"));
}
{
  // runChain：上游未備 → waiting、不 dispatch
  const spy = [];
  const kv = TRADING_KV();
  const byUrl = { [diag.dep.url]: { date: "2026-07-20" } };
  const getP = (u) => mkFetch(byUrl, spy)(u).then((r) => (r.ok ? r.json() : null));
  const out = await runChain({ ...ENV_BASE, GH_DISPATCH_TOKEN: "T", FLOW_KV: kv }, TP, diag, getP, mkFetch(byUrl, spy));
  chk("chain 上游未備 → waiting dep", out.waiting === "dep" && !spy.some((s) => s.url.includes("/dispatches")));
}

// ---- runAetf2：21:45 前等待、之後冪等單發 ----
{
  const spy = [];
  const early = await runAetf2({ ...ENV_BASE, GH_DISPATCH_TOKEN: "T", FLOW_KV: TRADING_KV() },
    { date: "2026-07-21", hour: 21, minute: 30, dow: 2 }, mkFetch({}, spy));
  chk("21:30 → waiting", early.waiting === "before-21:45" && spy.length === 0);
  const kv = TRADING_KV();
  const late = await runAetf2({ ...ENV_BASE, GH_DISPATCH_TOKEN: "T", FLOW_KV: kv },
    { date: "2026-07-21", hour: 21, minute: 50, dow: 2 }, mkFetch({}, spy));
  chk("21:50 → fired aetf.yml", late.fired === true && spy[0]?.url.includes("/aetf.yml/dispatches"));
  const again = await runAetf2({ ...ENV_BASE, GH_DISPATCH_TOKEN: "T", FLOW_KV: kv },
    { date: "2026-07-21", hour: 22, minute: 0, dow: 2 }, mkFetch({}, spy));
  chk("再喚醒 → already-fired", again.skipped === "already-fired" && spy.length === 1);
}

// ---- runEvening：整合（全齊之夜一次喚醒 → summary+diag 齊發、mktbal 等 diag、aetf2 看時間）----
{
  const spy = [];
  const kv = TRADING_KV();
  const mktbal = pipes.find((p) => p.name === "mktbal");
  const byUrl = {
    ...READY_PM,                                   // 三源今日
    [diag.dep.url]: { date: "2026-07-21" },        // postmkt.json 今日（與 READY_PM 同 URL，覆蓋一致）
    // diag.json（diag 自身/mktbal dep）404 → diag 該發、mktbal 等
  };
  const out = await runEvening({ ...ENV_BASE, GH_DISPATCH_TOKEN: "T", FLOW_KV: kv },
    { date: "2026-07-21", hour: 22, minute: 5, dow: 2 }, mkFetch(byUrl, spy));
  chk("evening: summary fired", out.summary?.fired === true);
  chk("evening: diag fired", out.diag?.fired === true);
  chk("evening: mktbal 等 diag", out.mktbal?.waiting === "dep");
  chk("evening: aetf2 fired（22:05 > 21:45）", out.aetf2?.fired === true);
  const dispatched = spy.filter((s) => s.url.includes("/dispatches")).map((s) => s.url);
  chk("evening: 恰 3 個 dispatch（summary/diag/aetf2）", dispatched.length === 3, dispatched.join(" | "));
}
// 非交易日 → 整段 skip 零網路
{
  const spy = [];
  const out = await runEvening({ ...ENV_BASE, GH_DISPATCH_TOKEN: "T", FLOW_KV: fakeKV() },
    { date: "2026-07-26", hour: 22, minute: 5, dow: 0 }, mkFetch({}, spy));
  chk("evening 非交易日 → skipped＋零網路", out.skipped === "non-trading-day" && spy.length === 0);
}

console.log(`\n${fail === 0 ? "PASS" : "FAIL"}  ${pass} 通過 / ${fail} 失敗`);
process.exit(fail === 0 ? 0 : 1);
