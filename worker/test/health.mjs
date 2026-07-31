// 日終/晨間健檢＋排程狀態軌跡 jobstat 離線單元測試（2026-07-25 P1）
// 無需 token、不打真實網路——fetch 全用 mock。執行：cd worker && node test/health.mjs
import { healthTargets, healthVerdict, healthUrl, runHealthCheck, HEALTH_CRONS,
  dispatchRoleForCron, recordJob, jobstatKey, hhmm, JOBSTAT_MAX, alertedKey } from "../src/index.js";

let pass = 0, fail = 0;
function chk(name, ok, detail) {
  if (ok) { pass++; } else { fail++; console.log(`  x ${name}  ${detail || ""}`); }
}

const ENV_BASE = { DATA_BASE: "https://raw.githubusercontent.com/shihpc/taiwan-flow-live-v2/main/data" };
const TP = { date: "2026-07-20", dow: 1, hour: 23, minute: 50 };   // 台北週一 23:50（日終健檢時點）
const T = healthTargets(ENV_BASE);
const ALL = [...T.eve, ...T.morn];

function fakeKV(init = {}) {
  const m = new Map(Object.entries(init));
  return {
    _m: m, _puts: [],
    async get(k, type) { const v = m.get(k); if (v === undefined) return null; return type === "json" ? (typeof v === "string" ? JSON.parse(v) : v) : v; },
    async put(k, v, o) { m.set(k, v); this._puts.push({ k, o }); },
  };
}
const TRADING_KV = () => fakeKV({ "series:2026-07-20": [{ t: "09:00", amt: 100 }] });
const ALERT_URL = "https://hook.test/notify";
const ALERT_ENV = (kv) => ({ ...ENV_BASE, FLOW_KV: kv, ALERT_WEBHOOK: ALERT_URL });
// mock fetch：freshNames 內的項目回「今日」，其餘回昨日（exists 模式回 404＝無檔）
const mkFetch = (freshNames, alerts = []) => async (u, init) => {
  const s = String(u);
  if (s.startsWith("https://hook.test/")) { alerts.push(JSON.parse(init.body).content); return { ok: true, status: 200 }; }
  const t = ALL.find((x) => s.startsWith(healthUrl(x, TP.date)));
  if (!t) return { ok: false, status: 404, json: async () => null };
  const fresh = freshNames.includes(t.name);
  if (t.mode === "exists")
    return fresh ? { ok: true, status: 200, json: async () => ({ ok: 1 }) } : { ok: false, status: 404, json: async () => null };
  const val = fresh
    ? (t.mode === "genToday" ? "2026-07-20T21:00:00+08:00" : "2026-07-20")
    : (t.mode === "genToday" ? "2026-07-17T21:00:00+08:00" : "2026-07-17");
  return { ok: true, status: 200, json: async () => ({ [t.field]: val }) };
};

// ---- 檢查表 ----
{
  chk("eve 十項（四單體班＋flows/postmkt/diag/mktbal/news/summary-pm）", T.eve.length === 10, String(T.eve.length));
  chk("morn 三項（morning/us/summary-am）", T.morn.length === 3 && T.morn.map((t) => t.name).join(",") === "morning,us,summary-am");
  chk("eve 涵蓋哨兵事件驅動類（recheck 管不到的）",
    ["flows", "postmkt", "news"].every((n) => T.eve.some((t) => t.name === n)));
  chk("summary 兩場用 exists 模式（當日檔存在即可）",
    T.eve.find((t) => t.name === "summary-pm").mode === "exists" && T.morn.find((t) => t.name === "summary-am").mode === "exists");
  chk("跨 repo URL 正確（flows/postmkt/news 各自 raw）",
    T.eve.find((t) => t.name === "flows").url.includes("/shihpc/taiwan-flows/") &&
    T.eve.find((t) => t.name === "postmkt").url.includes("/shihpc/postmkt/") &&
    T.eve.find((t) => t.name === "news").url.includes("/shihpc/taiwan-stock-news/"));
}

// ---- URL 佔位代入 ----
{
  const intra = T.eve.find((t) => t.name === "intraday");
  const sumpm = T.eve.find((t) => t.name === "summary-pm");
  chk("{date} 代入 YYYY-MM-DD", healthUrl(intra, "2026-07-20").endsWith("/intraday/2026-07-20.json"), healthUrl(intra, "2026-07-20"));
  chk("{ymd} 代入 YYYYMMDD", healthUrl(sumpm, "2026-07-20").endsWith("/summary/20260720-pm.json"), healthUrl(sumpm, "2026-07-20"));
}

// ---- healthVerdict 三模式 ----
{
  const today = "2026-07-20";
  const byName = Object.fromEntries(ALL.map((t) => [t.name, t]));
  chk("date 今日 → ok", healthVerdict(byName.baseline, { date: today }, today).ok === true);
  chk("date 昨日 → 不 ok 且帶產物日期", (() => {
    const v = healthVerdict(byName.baseline, { date: "2026-07-17" }, today);
    return v.ok === false && v.at === "2026-07-17";
  })());
  chk("mktbal 用 latest_date 欄", healthVerdict(byName.mktbal, { latest_date: today }, today).ok === true);
  chk("genToday 台北日今日 → ok", healthVerdict(byName.news, { generated_at: "2026-07-20T21:07:00+08:00" }, today).ok === true);
  chk("genToday 跨日 UTC 正規化", healthVerdict(byName.us, { generated_at: "2026-07-19T23:30:00Z" }, today).ok === true);
  chk("exists 有檔 → ok", healthVerdict(byName["summary-pm"], { any: 1 }, today).ok === true);
  chk("exists 無檔 → 不 ok、at=null", (() => {
    const v = healthVerdict(byName["summary-pm"], null, today);
    return v.ok === false && v.at === null;
  })());
  chk("抓不到檔 → 不 ok、at=null", healthVerdict(byName.baseline, null, today).at === null);
}

// ---- cron 路由 ----
{
  chk("HEALTH_CRONS 兩條", Object.keys(HEALTH_CRONS).length === 2);
  chk("台北 23:50 → eve", dispatchRoleForCron("50 15 * * 2-6")?.kind === "health" && dispatchRoleForCron("50 15 * * 2-6").slot === "eve");
  chk("台北 09:30 → morn", dispatchRoleForCron("30 1 * * 2-6")?.slot === "morn");
  chk("健檢 cron 不與備援班撞", dispatchRoleForCron("50 15 * * 2-6").name === undefined);
  chk("既有晚場班 cron 仍回 evening", dispatchRoleForCron("*/5 13-15 * * 2-6")?.kind === "evening");
  chk("既有 recheck cron 仍回 backup", dispatchRoleForCron("55 12 * * 2-6")?.kind === "backup");
  chk("未知 cron → null", dispatchRoleForCron("0 0 * * *") === null);
}

// ---- runHealthCheck ----
// 1) 無當日 series（休市 or frame 班故障）→ **照樣盤點照樣告警**，只在文字標註
//    （看門狗不能拿可能自己壞掉的訊號當守門——2026-07-24 就是這樣整天靜默）
{
  const alerts = [];
  const out = await runHealthCheck(ALERT_ENV(fakeKV()), TP, "eve", mkFetch([], alerts));
  chk("無 series → 不跳過、照樣盤點", out.skipped === undefined && out.checked === 10, JSON.stringify(out.skipped));
  chk("無 series → noSeries 旗標", out.noSeries === true);
  chk("無 series → 照樣告警且標註可能休市/故障", alerts.length === 1 && alerts[0].includes("無盤中 series"), alerts[0]);
}
// 1b) 有 series 時不加註記
{
  const alerts = [], kv = TRADING_KV();
  const out = await runHealthCheck(ALERT_ENV(kv), TP, "eve", mkFetch([], alerts));
  chk("有 series → noSeries=false、告警不加註記", out.noSeries === false && !alerts[0].includes("無盤中 series"), alerts[0]);
}
// 2) 全部落地 → 不告警、jobstat 記 all-ok
{
  const alerts = [], kv = TRADING_KV();
  const names = T.eve.map((t) => t.name);
  const out = await runHealthCheck(ALERT_ENV(kv), TP, "eve", mkFetch(names, alerts));
  chk("全綠 → missing 空", out.missing.length === 0 && out.checked === 10, JSON.stringify(out.missing));
  chk("全綠 → 不告警", alerts.length === 0);
  const ev = JSON.parse(kv._m.get(jobstatKey("2026-07-20")));
  chk("全綠 → jobstat 記 all-ok＋時分", ev.length === 1 && ev[0].r === "all-ok" && ev[0].t === "23:50" && ev[0].n === "health-eve", JSON.stringify(ev));
}
// 3) 缺件 → 告警一則，列出缺什麼；jobstat 記 missing 數
{
  const alerts = [], kv = TRADING_KV();
  const names = T.eve.map((t) => t.name).filter((n) => n !== "aetf" && n !== "summary-pm");
  const out = await runHealthCheck(ALERT_ENV(kv), TP, "eve", mkFetch(names, alerts));
  chk("缺兩件 → missing 兩項", out.missing.length === 2, JSON.stringify(out.missing));
  chk("缺件 → 發一則告警", alerts.length === 1, String(alerts.length));
  chk("告警內容含缺件名與產物日期", alerts[0].includes("aetf(2026-07-17)") && alerts[0].includes("summary-pm(無檔)") && alerts[0].includes("2/10"), alerts[0]);
  chk("告警走每日去重鍵", kv._m.get(alertedKey("2026-07-20", "health-eve")) === "1");
  const ev = JSON.parse(kv._m.get(jobstatKey("2026-07-20")));
  chk("jobstat 記 missing:2＋缺件清單", ev[0].r === "missing:2" && ev[0].x.includes("aetf"), JSON.stringify(ev));
}
// 4) dry → 只回結果，不告警、不記 jobstat
{
  const alerts = [], kv = TRADING_KV();
  const out = await runHealthCheck(ALERT_ENV(kv), TP, "eve", mkFetch([], alerts), { dry: true });
  chk("dry → 仍回完整盤點", out.checked === 10 && out.missing.length === 10);
  chk("dry → 不告警", alerts.length === 0);
  chk("dry → 不寫 jobstat", kv._m.get(jobstatKey("2026-07-20")) === undefined);
}
// 5) morn slot 只查三項
{
  const alerts = [], kv = TRADING_KV();
  const out = await runHealthCheck(ALERT_ENV(kv), { ...TP, hour: 9, minute: 30 }, "morn", mkFetch(["morning"], alerts));
  chk("morn 查三項", out.checked === 3);
  chk("morn 缺 us/summary-am → 告警", alerts.length === 1 && alerts[0].includes("晨間健檢") && alerts[0].includes("2/3"), alerts[0]);
}
// 6) 同日重複健檢 → 告警去重（不重複轟炸）
{
  const alerts = [], kv = TRADING_KV();
  const env = ALERT_ENV(kv);
  await runHealthCheck(env, TP, "eve", mkFetch([], alerts));
  await runHealthCheck(env, TP, "eve", mkFetch([], alerts));
  chk("同日同 slot 告警只發一次", alerts.length === 1, String(alerts.length));
}

// ---- recordJob / jobstat ----
{
  chk("hhmm 補零", hhmm({ hour: 9, minute: 5 }) === "09:05" && hhmm({ hour: 23, minute: 50 }) === "23:50");
  chk("jobstatKey 格式", jobstatKey("2026-07-20") === "jobstat:20260720");
}
{
  const kv = TRADING_KV();
  await recordJob({ FLOW_KV: kv }, TP, "baseline", "fired#1", "2026-07-17");
  await recordJob({ FLOW_KV: kv }, { ...TP, hour: 20, minute: 55 }, "baseline", "landed", "2026-07-20");
  const ev = JSON.parse(kv._m.get(jobstatKey("2026-07-20")));
  chk("依序 append 兩筆", ev.length === 2 && ev[0].r === "fired#1" && ev[1].r === "landed");
  chk("extra 存進 x 欄", ev[0].x === "2026-07-17" && ev[1].t === "20:55");
  chk("TTL 3 天", kv._puts.at(-1).o?.expirationTtl === 259200, JSON.stringify(kv._puts.at(-1).o));
}
{
  // 上限裁切：超過 JOBSTAT_MAX 丟最舊
  const seed = Array.from({ length: JOBSTAT_MAX }, (_, i) => ({ t: "09:00", n: `x${i}`, r: "fired" }));
  const kv = fakeKV({ [jobstatKey("2026-07-20")]: JSON.stringify(seed) });
  await recordJob({ FLOW_KV: kv }, TP, "new", "fired");
  const ev = JSON.parse(kv._m.get(jobstatKey("2026-07-20")));
  chk("陣列上限裁切", ev.length === JOBSTAT_MAX && ev.at(-1).n === "new" && ev[0].n === "x1", `${ev.length}/${ev[0].n}`);
}
{
  // 無 KV binding / KV 爆炸 → 回 false，不丟例外（絕不拖垮排程主體）
  chk("無 KV → false", (await recordJob({}, TP, "a", "b")) === false);
  const boom = { get: async () => { throw new Error("KV down"); }, put: async () => {} };
  chk("KV 異常 → 吞錯回 false", (await recordJob({ FLOW_KV: boom }, TP, "a", "b")) === false);
}

console.log(`\n${fail === 0 ? "PASS" : "FAIL"}  ${pass} 通過 / ${fail} 失敗`);
process.exit(fail === 0 ? 0 : 1);
