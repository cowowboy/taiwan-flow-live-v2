// 排程備援離線單元測試（無需 token、不打真實網路——fetch 全用 mock）
// 執行：cd worker && node test/backup.mjs
import { backupPipelines, backupPipelineForCron, BACKUP_CRONS, bkfiredKey,
  productFresh, runBackup, parseBkState, bkGate, bkStateValue, alertedKey,
  BK_RECHECK_MS, BK_MAX_ATTEMPTS, HEALTH_CRONS, jobstatKey } from "../src/index.js";

let pass = 0, fail = 0;
function chk(name, ok, detail) {
  if (ok) { pass++; } else { fail++; console.log(`  x ${name}  ${detail || ""}`); }
}

const ENV = { DATA_BASE: "https://raw.githubusercontent.com/shihpc/taiwan-flow-live-v2/main/data" };
const pipes = backupPipelines(ENV);
const byName = Object.fromEntries(pipes.map((p) => [p.name, p]));

// ---- 設定表：七條班、repo、workflow 檔、產物 URL、日期欄、模式、交易日守門 ----
{
  chk("七條班齊全", pipes.length === 7 && ["daysummary","aetf","baseline","us","intraday","diag","mktbal"].every((n) => byName[n]));
  chk("daysummary 設定", byName.daysummary.wf === "daysummary.yml" && byName.daysummary.field === "date" &&
    byName.daysummary.url.endsWith("/daysummary/latest.json") && byName.daysummary.repo === "taiwan-flow-live-v2");
  chk("aetf 用 run_date 欄", byName.aetf.field === "run_date" && byName.aetf.wf === "aetf.yml");
  chk("baseline 用 date 欄", byName.baseline.field === "date" && byName.baseline.url.endsWith("/baseline.json"));
  chk("us genToday＋非TW守門", byName.us.mode === "genToday" && byName.us.field === "generated_at" && byName.us.tw === false);
  chk("intraday {date} 佔位＋tw 守門", byName.intraday.url.endsWith("/intraday/{date}.json") &&
    byName.intraday.wf === "intraday.yml" && byName.intraday.tw === true);
  chk("diag 跨 repo postmkt＋dep=postmkt.json", byName.diag.repo === "postmkt" && byName.diag.wf === "diag.yml" &&
    byName.diag.url === "https://raw.githubusercontent.com/shihpc/postmkt/main/data/diag/diag.json" &&
    byName.diag.dep?.url.endsWith("/data/postmkt.json") && byName.diag.dep?.field === "date");
  chk("mktbal 跨 repo＋latest_date 欄＋dep=diag.json", byName.mktbal.repo === "postmkt" && byName.mktbal.wf === "mktbal.yml" &&
    byName.mktbal.field === "latest_date" &&
    byName.mktbal.url === "https://raw.githubusercontent.com/shihpc/postmkt/main/data/market_balance_history.json" &&
    byName.mktbal.dep?.url.endsWith("/data/diag/diag.json") && byName.mktbal.dep?.field === "date");
  chk("TW 班皆 tw:true", ["daysummary","aetf","baseline","intraday","diag","mktbal"].every((n) => byName[n].tw === true));
}

// ---- event.cron → pipeline 對應（2026-07-22 主排程化：單體班五條；diag/mktbal 併入晚場協調班無專屬 cron）
//      2026-07-25：每班再加一條 recheck cron → 共十條 ----
{
  chk("BACKUP_CRONS 十條（五班 × 主觸發＋recheck）", Object.keys(BACKUP_CRONS).length === 10, String(Object.keys(BACKUP_CRONS).length));
  for (const n of ["daysummary", "intraday", "aetf", "baseline", "us"]) {
    const crons = Object.entries(BACKUP_CRONS).filter(([, v]) => v === n).map(([k]) => k);
    chk(`${n} 恰兩條 cron（主＋recheck）`, crons.length === 2, crons.join(" | "));
  }
  chk("recheck cron 命中 daysummary（台北 14:05）", backupPipelineForCron("5 6 * * 1-5", ENV)?.name === "daysummary");
  chk("recheck cron 命中 intraday（台北 15:10）", backupPipelineForCron("10 7 * * 1-5", ENV)?.name === "intraday");
  chk("recheck cron 命中 aetf（台北 19:00）", backupPipelineForCron("0 11 * * 1-5", ENV)?.name === "aetf");
  chk("recheck cron 命中 baseline（台北 20:55）", backupPipelineForCron("55 12 * * 1-5", ENV)?.name === "baseline");
  chk("recheck cron 命中 us（台北 05:35，dow *）", backupPipelineForCron("35 21 * * *", ENV)?.name === "us");
  // cron 字串不得與既有任何一條重複（重複＝覆蓋掉別班的路由）
  const all = ["* 1-5 * * 1-5", "*/5 9-14 * * 1-5", "7,47 0-14,22-23 * * *", "*/5 13-15 * * 1-5",
    "50,55 22 * * *", "*/5 23 * * *", "*/10 0 * * *", ...Object.keys(BACKUP_CRONS), ...Object.keys(HEALTH_CRONS)];
  chk("全部 cron 字串互不重複（含健檢班共 19 條）", new Set(all).size === all.length && all.length === 19, String(all.length));
  chk("cron 命中 daysummary（主觸發 13:35）", backupPipelineForCron("35 5 * * 1-5", ENV)?.name === "daysummary");
  chk("cron 命中 intraday（備援 14:40）", backupPipelineForCron("40 6 * * 1-5", ENV)?.name === "intraday");
  chk("cron 命中 aetf（主觸發 18:35）", backupPipelineForCron("35 10 * * 1-5", ENV)?.name === "aetf");
  chk("cron 命中 baseline（主觸發 20:05）", backupPipelineForCron("5 12 * * 1-5", ENV)?.name === "baseline");
  chk("cron 命中 us（dow *，CF 拒收 0-4）", backupPipelineForCron("5 21 * * *", ENV)?.name === "us");
  chk("舊 diag 備援 cron 已除役 → null", backupPipelineForCron("35 14 * * 1-5", ENV) === null);
  chk("舊 mktbal 備援 cron 已除役 → null", backupPipelineForCron("45 14 * * 1-5", ENV) === null);
  chk("既有 frame cron → null（不誤判備援）", backupPipelineForCron("* 1-5 * * 1-5", ENV) === null);
  chk("既有哨兵 cron → null", backupPipelineForCron("*/5 9-14 * * 1-5", ENV) === null);
  chk("既有新聞/晨報 cron → null", backupPipelineForCron("7,47 0-14,22-23 * * *", ENV) === null);
  chk("未知 cron → null", backupPipelineForCron("0 0 * * *", ENV) === null);
}

// ---- bkfiredKey 格式 ----
{
  chk("bkfiredKey 格式", bkfiredKey("2026-07-20", "aetf") === "bkfired:20260720:aetf", bkfiredKey("2026-07-20", "aetf"));
}

// ---- productFresh：新鮮度純函式 ----
{
  const today = "2026-07-20";
  chk("date 欄=今日 → fresh", productFresh({ date: "2026-07-20" }, byName.daysummary, today) === true);
  chk("date 欄=昨日 → 非fresh", productFresh({ date: "2026-07-17" }, byName.daysummary, today) === false);
  chk("run_date 欄=今日 → fresh", productFresh({ run_date: "2026-07-20T20:29:46+08:00" }, byName.aetf, today) === true);
  chk("latest_date 欄=今日 → fresh", productFresh({ latest_date: "2026-07-20" }, byName.mktbal, today) === true);
  chk("obj null → 非fresh", productFresh(null, byName.baseline, today) === false);
  chk("欄位缺 → 非fresh", productFresh({}, byName.baseline, today) === false);
  // genToday：us generated_at 帶 +08:00，取台北日比對
  chk("genToday generated_at=今日(+08:00) → fresh",
    productFresh({ generated_at: "2026-07-20T05:49:58.483735+08:00", date: "2026-07-17" }, byName.us, today) === true);
  chk("genToday generated_at=昨日 → 非fresh",
    productFresh({ generated_at: "2026-07-19T05:49:58+08:00", date: "2026-07-17" }, byName.us, today) === false);
  chk("genToday 跨日 UTC 正規化（07-19T23:30Z=台北07-20 07:30）→ fresh",
    productFresh({ generated_at: "2026-07-19T23:30:00Z" }, byName.us, today) === true);
  chk("genToday generated_at 缺 → 非fresh", productFresh({ date: "2026-07-20" }, byName.us, today) === false);
}

// ---- runBackup：整合決策（mock env / mock fetch，不打真實網路）----
function fakeKV(init = {}) {
  const m = new Map(Object.entries(init));
  return {
    _m: m,
    async get(k, type) { const v = m.get(k); if (v === undefined) return null; return type === "json" ? (typeof v === "string" ? JSON.parse(v) : v) : v; },
    async put(k, v) { m.set(k, v); },
  };
}
// mock fetch：/dispatches → status；其餘視為產物 URL → 回 productObj（null 代表 404）
const mkFetch = (productObj, dispatchStatus = 204, spy) => async (u, init) => {
  if (String(u).includes("/dispatches")) { if (spy) spy.push(String(u)); return { status: dispatchStatus }; }
  return { ok: productObj != null, status: productObj ? 200 : 404, json: async () => productObj };
};
const TP = { date: "2026-07-20", dow: 1 };   // 2026-07-20 = 週一（台北 dow 1）
const TRADING_KV = () => fakeKV({ "series:2026-07-20": [{ t: "09:00", amt: 100 }] });
const NOW = Date.UTC(2026, 6, 20, 12, 0, 0);   // 固定時鐘（opts.nowMs 注入），冷卻期判定可重現

// 1) GH_DISPATCH_TOKEN 未設 → 靜默跳過（不打任何網路）
{
  const spy = [];
  const out = await runBackup({ FLOW_KV: TRADING_KV() }, TP, byName.aetf, mkFetch({ run_date: "2026-07-17" }, 204, spy));
  chk("無 token → skipped no-token", out.skipped === "no-token");
  chk("無 token → 不打 dispatch", spy.length === 0);
}
// 2) 非交易日（無當日 series）TW 班 → 不補發
{
  const spy = [];
  const out = await runBackup({ GH_DISPATCH_TOKEN: "T", FLOW_KV: fakeKV() }, TP, byName.baseline, mkFetch({ date: "2026-07-17" }, 204, spy));
  chk("非交易日 → skipped non-trading-day", out.skipped === "non-trading-day");
  chk("非交易日 → 不打 dispatch", spy.length === 0);
}
// 3) 冪等：今日已發過且仍在冷卻期內 → 不再發
{
  const spy = [];
  const kv = fakeKV({ "series:2026-07-20": [{ t: "09:00" }],
    "bkfired:20260720:baseline": bkStateValue("fired", 1, NOW - 5 * 60e3) });
  const out = await runBackup({ GH_DISPATCH_TOKEN: "T", FLOW_KV: kv }, TP, byName.baseline,
    mkFetch({ date: "2026-07-17" }, 204, spy), { nowMs: NOW });
  chk("冷卻期內 → skipped already-fired", out.skipped === "already-fired", JSON.stringify(out));
  chk("冷卻期內 → 不打 dispatch", spy.length === 0);
}
// 4) 產物已今日 → fresh、不補發、KV 不記
{
  const spy = [];
  const kv = TRADING_KV();
  const out = await runBackup({ GH_DISPATCH_TOKEN: "T", FLOW_KV: kv }, TP, byName.baseline, mkFetch({ date: "2026-07-20" }, 204, spy));
  chk("產物今日 → fresh:true", out.fresh === true);
  chk("產物今日 → 不打 dispatch", spy.length === 0);
  chk("產物今日 → KV 不記 bkfired", kv._m.get("bkfired:20260720:baseline") === undefined);
}
// 5) 產物非今日（交易日、未補過）→ 補發 + KV 記
{
  const spy = [];
  const kv = TRADING_KV();
  const out = await runBackup({ GH_DISPATCH_TOKEN: "T", FLOW_KV: kv }, TP, byName.baseline, mkFetch({ date: "2026-07-17" }, 204, spy));
  chk("產物非今日 → fired:true", out.fired === true);
  chk("補發打對 workflow URL", spy.length === 1 && spy[0].includes("/shihpc/taiwan-flow-live-v2/actions/workflows/baseline.yml/dispatches"), spy[0]);
  const st5 = parseBkState(kv._m.get("bkfired:20260720:baseline"));
  chk("補發後 KV 記 fired 狀態＋次數 1＋時戳", st5.s === "fired" && st5.n === 1 && st5.ts > 0, JSON.stringify(st5));
  chk("首發不告警（第 2 次才告警）", out.attempt === 1 && kv._m.get(alertedKey("2026-07-20", "bk-recheck-baseline")) === undefined);
}
// 5b) 跨 repo：diag 產物非今日 → 補發打 postmkt
{
  const spy = [];
  const out = await runBackup({ GH_DISPATCH_TOKEN: "T", FLOW_KV: TRADING_KV() }, TP, byName.diag, mkFetch({ date: "2026-07-17" }, 204, spy));
  chk("diag 跨 repo 補發打 postmkt/diag.yml",
    out.fired === true && spy[0].includes("/shihpc/postmkt/actions/workflows/diag.yml/dispatches"), spy[0]);
}
// 6) us（tw:false）：不看 series；generated_at 昨日 → 補發（即使無 series 也不被守門擋）
{
  const spy = [];
  const out = await runBackup({ GH_DISPATCH_TOKEN: "T", FLOW_KV: fakeKV() }, TP, byName.us,
    mkFetch({ generated_at: "2026-07-19T05:49:58+08:00", date: "2026-07-17" }, 204, spy));
  chk("us 無 series 仍不被守門擋（tw:false）", out.fired === true);
  chk("us 補發打 us.yml", spy[0].includes("/us.yml/dispatches"), spy[0]);
}
// 6a2) us 週末守門：台北 dow=6（週六晨）→ 不補發（cron dow * 由 runBackup 台北 dow 擋週末）
{
  const spy = [];
  const out = await runBackup({ GH_DISPATCH_TOKEN: "T", FLOW_KV: fakeKV() }, { date: "2026-07-25", dow: 6 }, byName.us,
    mkFetch({ generated_at: "2026-07-24T05:49:58+08:00", date: "2026-07-17" }, 204, spy));
  chk("us 週末（台北 dow6）→ skipped non-trading-day", out.skipped === "non-trading-day");
  chk("us 週末 → 不打 dispatch", spy.length === 0);
}
// 6b) us generated_at 今日 → fresh
{
  const spy = [];
  const out = await runBackup({ GH_DISPATCH_TOKEN: "T", FLOW_KV: fakeKV() }, TP, byName.us,
    mkFetch({ generated_at: "2026-07-20T05:49:58+08:00", date: "2026-07-17" }, 204, spy));
  chk("us 今天已跑 → fresh:true", out.fresh === true && spy.length === 0);
}
// 7) dry 模式：非今日但只回決策、不真的 dispatch、KV 不記
{
  const spy = [];
  const kv = TRADING_KV();
  const out = await runBackup({ GH_DISPATCH_TOKEN: "T", FLOW_KV: kv }, TP, byName.aetf, mkFetch({ run_date: "2026-07-17" }, 204, spy), { dry: true });
  chk("dry → wouldDispatch 不真的發", out.wouldDispatch === true && out.fired === undefined);
  chk("dry → 不打 dispatch", spy.length === 0);
  chk("dry → KV 不記", kv._m.get("bkfired:20260720:aetf") === undefined);
}
// 8) 產物抓取失敗（404）視為非今日 → 補發（漏發時線上根本沒檔的情境）
{
  const spy = [];
  const out = await runBackup({ GH_DISPATCH_TOKEN: "T", FLOW_KV: TRADING_KV() }, TP, byName.aetf, mkFetch(null, 204, spy));
  chk("產物抓不到 → 補發", out.fired === true && spy.length === 1);
}
// 9) dispatch 兩次都失敗 → 回 error、KV 不記（不誤標已補發，保留重試機會）
{
  const spy = [];
  const kv = TRADING_KV();
  const out = await runBackup({ GH_DISPATCH_TOKEN: "T", FLOW_KV: kv }, TP, byName.baseline, mkFetch({ date: "2026-07-17" }, 401, spy), { sleepFn: async () => {} });
  chk("dispatch 失敗 → 回 error", !!out.error);
  chk("dispatch 失敗 → KV 不記 bkfired（保留重試）", kv._m.get("bkfired:20260720:baseline") === undefined);
}

// 10) intraday {date} 佔位：產物 URL 以今日代入；當日檔 404 → 補發
{
  const spy = [], prodUrls = [];
  const f = async (u, init) => {
    if (String(u).includes("/dispatches")) { spy.push(String(u)); return { status: 204 }; }
    prodUrls.push(String(u));
    return { ok: false, status: 404, json: async () => null };
  };
  const out = await runBackup({ GH_DISPATCH_TOKEN: "T", FLOW_KV: TRADING_KV() }, TP, byName.intraday, f);
  chk("intraday URL 代入今日", prodUrls[0]?.includes("/intraday/2026-07-20.json"), prodUrls[0]);
  chk("intraday 當日檔 404 → 補發 intraday.yml", out.fired === true && spy[0].includes("/intraday.yml/dispatches"), spy[0]);
}

// ---- 2026-07-25：冪等狀態升級（dispatch 成功 ≠ job 成功）＋ recheck 補發 ＋ 排程告警 ----
// mock fetch 二代：另收告警 webhook（host hook.test）以驗「失敗有人知道」
const ALERT_URL = "https://hook.test/notify";
const mkFetch2 = (productObj, dispatchStatus, spy, alerts) => async (u, init) => {
  const s = String(u);
  if (s.includes("/dispatches")) { spy.push(s); return { status: dispatchStatus }; }
  if (s.startsWith("https://hook.test/")) { alerts.push(JSON.parse(init.body).content); return { ok: true, status: 200 }; }
  return { ok: productObj != null, status: productObj ? 200 : 404, json: async () => productObj };
};
const ALERT_ENV = (kv) => ({ GH_DISPATCH_TOKEN: "T", FLOW_KV: kv, ALERT_WEBHOOK: ALERT_URL });

// 11) parseBkState：新舊格式相容（部署當日 KV 裡還是舊字串）
{
  chk("parseBkState null → null", parseBkState(null) === null);
  const legacy = parseBkState("fired");
  chk("舊字串 fired → ts=0/n=1（視為冷卻已過，可 recheck）", legacy.s === "fired" && legacy.ts === 0 && legacy.n === 1);
  chk("舊字串 produced → s=produced", parseBkState("produced").s === "produced");
  const j = parseBkState(bkStateValue("fired", 2, 12345));
  chk("新 JSON 格式往返", j.s === "fired" && j.n === 2 && j.ts === 12345);
  chk("壞 JSON → 當舊字串處理不炸", parseBkState("{壞").s === "{壞");
}

// 12) bkGate：閘門純函式
{
  chk("無狀態 → go（第1次）", (() => { const g = bkGate(null, NOW); return g.act === "go" && g.n === 1; })());
  chk("produced → 永遠不再發", bkGate({ s: "produced", ts: NOW, n: 1 }, NOW).why === "already-produced");
  chk("冷卻期內 → already-fired", bkGate({ s: "fired", ts: NOW - 60e3, n: 1 }, NOW).why === "already-fired");
  chk("冷卻期過 → recheck（第2次）", (() => {
    const g = bkGate({ s: "fired", ts: NOW - BK_RECHECK_MS - 1, n: 1 }, NOW);
    return g.act === "recheck" && g.n === 2;
  })());
  chk("已達次數上限 → max-attempts", bkGate({ s: "fired", ts: 0, n: BK_MAX_ATTEMPTS }, NOW).why === "max-attempts");
  chk("舊字串狀態（ts=0）冷卻視為已過 → recheck", bkGate(parseBkState("fired"), NOW).act === "recheck");
}

// 13) recheck 核心情境：首發後產物仍非今日 → 補發第 2 次 ＋ 告警
{
  const spy = [], alerts = [];
  const kv = fakeKV({ "series:2026-07-20": [{ t: "09:00" }],
    "bkfired:20260720:baseline": bkStateValue("fired", 1, NOW - 30 * 60e3) });
  const out = await runBackup(ALERT_ENV(kv), TP, byName.baseline,
    mkFetch2({ date: "2026-07-17" }, 204, spy, alerts), { nowMs: NOW });
  chk("recheck 產物仍舊 → 補發第 2 次", out.fired === true && out.attempt === 2, JSON.stringify(out));
  chk("recheck 補發打對 workflow", spy.length === 1 && spy[0].includes("/baseline.yml/dispatches"));
  chk("recheck KV 記 n=2", parseBkState(kv._m.get("bkfired:20260720:baseline")).n === 2);
  chk("recheck 發出告警", alerts.length === 1 && alerts[0].includes("baseline") && alerts[0].includes("第 2 次"), alerts[0]);
  chk("告警每日每 tag 至多一則（KV 記號）", kv._m.get(alertedKey("2026-07-20", "bk-recheck-baseline")) === "1");
}

// 14) recheck 發現產物已落地 → 標 produced、不重發（首發那輪其實成功了）
{
  const spy = [], alerts = [];
  const kv = fakeKV({ "series:2026-07-20": [{ t: "09:00" }],
    "bkfired:20260720:baseline": bkStateValue("fired", 1, NOW - 30 * 60e3) });
  const out = await runBackup(ALERT_ENV(kv), TP, byName.baseline,
    mkFetch2({ date: "2026-07-20" }, 204, spy, alerts), { nowMs: NOW });
  chk("recheck 產物今日 → fresh、不重發", out.fresh === true && spy.length === 0);
  chk("recheck 落地 → KV 改標 produced", parseBkState(kv._m.get("bkfired:20260720:baseline")).s === "produced");
  chk("recheck 落地 → 不告警", alerts.length === 0);
}

// 15) 已補發到上限 → 不再發（留給 GH 兜底 cron，不無限重試）
{
  const spy = [], alerts = [];
  const kv = fakeKV({ "series:2026-07-20": [{ t: "09:00" }],
    "bkfired:20260720:baseline": bkStateValue("fired", BK_MAX_ATTEMPTS, NOW - 60 * 60e3) });
  const out = await runBackup(ALERT_ENV(kv), TP, byName.baseline,
    mkFetch2({ date: "2026-07-17" }, 204, spy, alerts), { nowMs: NOW });
  chk("達上限 → skipped max-attempts", out.skipped === "max-attempts", JSON.stringify(out));
  chk("達上限 → 不打 dispatch", spy.length === 0);
}

// 16) produced → 當日短路（連產物都不抓）
{
  const spy = [], prod = [];
  const kv = fakeKV({ "series:2026-07-20": [{ t: "09:00" }],
    "bkfired:20260720:baseline": bkStateValue("produced", 1, NOW - 60e3) });
  const f = async (u, init) => {
    if (String(u).includes("/dispatches")) { spy.push(String(u)); return { status: 204 }; }
    prod.push(String(u)); return { ok: true, status: 200, json: async () => ({ date: "2026-07-17" }) };
  };
  const out = await runBackup(ALERT_ENV(kv), TP, byName.baseline, f, { nowMs: NOW });
  chk("produced → skipped already-produced", out.skipped === "already-produced");
  chk("produced → 不抓產物也不 dispatch", prod.length === 0 && spy.length === 0);
}

// 17) dispatch 兩次都失敗 → 告警；同日再撞同一故障不重複轟炸
{
  const spy = [], alerts = [];
  const kv = TRADING_KV();
  const env2 = ALERT_ENV(kv);
  const opts = { nowMs: NOW, sleepFn: async () => {} };
  const out = await runBackup(env2, TP, byName.baseline, mkFetch2({ date: "2026-07-17" }, 401, spy, alerts), opts);
  chk("dispatch 失敗 → 回 error", !!out.error);
  chk("dispatch 失敗 → 發告警", alerts.length === 1 && alerts[0].includes("dispatch 失敗"), alerts[0]);
  chk("dispatch 失敗 → KV 不記 bkfired（保留重試）", kv._m.get("bkfired:20260720:baseline") === undefined);
  // 第二輪同故障（recheck cron 或下一次喚醒）：仍重試 dispatch，但告警不再重發
  await runBackup(env2, TP, byName.baseline, mkFetch2({ date: "2026-07-17" }, 401, spy, alerts), opts);
  chk("同日同 tag 告警去重", alerts.length === 1, `alerts=${alerts.length}`);
}

// 18) 通道未設 → 告警靜默（不打任何外部請求），排程主體照常
{
  const spy = [], alerts = [];
  const kv = TRADING_KV();
  const out = await runBackup({ GH_DISPATCH_TOKEN: "T", FLOW_KV: kv }, TP, byName.baseline,
    mkFetch2({ date: "2026-07-17" }, 401, spy, alerts), { nowMs: NOW, sleepFn: async () => {} });
  chk("無告警通道 → 不打 webhook", alerts.length === 0);
  chk("無告警通道 → 排程決策照常回 error", !!out.error);
}

// 19) jobstat：只記狀態轉換（fired/landed/fresh/error），不記輪詢 skip 噪音
{
  const spy = [], alerts = [], kv = TRADING_KV();
  const f = (prod) => mkFetch2(prod, 204, spy, alerts);
  await runBackup(ALERT_ENV(kv), TP, byName.baseline, f({ date: "2026-07-17" }), { nowMs: NOW });
  let ev = JSON.parse(kv._m.get(jobstatKey("2026-07-20")));
  chk("fired 記一筆 fired#1", ev.length === 1 && ev[0].n === "baseline" && ev[0].r === "fired#1", JSON.stringify(ev));
  await runBackup(ALERT_ENV(kv), TP, byName.baseline, f({ date: "2026-07-17" }), { nowMs: NOW + 60e3 });
  ev = JSON.parse(kv._m.get(jobstatKey("2026-07-20")));
  chk("冷卻期 skip → 不寫 jobstat", ev.length === 1, JSON.stringify(ev));
  await runBackup(ALERT_ENV(kv), TP, byName.baseline, f({ date: "2026-07-20" }), { nowMs: NOW + 40 * 60e3 });
  ev = JSON.parse(kv._m.get(jobstatKey("2026-07-20")));
  chk("recheck 確認落地 → 記 landed", ev.length === 2 && ev[1].r === "landed", JSON.stringify(ev));
}

console.log(`\n${fail === 0 ? "PASS" : "FAIL"}  ${pass} 通過 / ${fail} 失敗`);
process.exit(fail === 0 ? 0 : 1);
