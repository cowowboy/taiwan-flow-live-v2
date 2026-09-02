// us 晨間補跑班（2026-08-13）離線單元測試（無需 token/網路——fetch/KV 全 mock）
// 執行：cd worker && node test/uscatchup.mjs
// 覆蓋：① lastExpectedUsTradingDate 資料日判準（週二~六/週日/週一/美國假日誤判情境）
//       ② runUsCatchup 時窗（07:00 前、08:05 後不觸發）、週日/週一守門、
//          20 分 KV dedup、新鮮短路、dispatch 帶 inputs.rounds=2、失敗不寫 KV
import { lastExpectedUsTradingDate, runUsCatchup, usCatchupKey,
  US_CATCHUP_AFTER_MIN, US_CATCHUP_UNTIL_MIN, jobstatKey, RAW_ORG } from "../src/index.js";

let pass = 0, fail = 0;
function chk(name, ok, detail) {
  if (ok) { pass++; } else { fail++; console.log(`  x ${name}  ${detail || ""}`); }
}

// ---- ① lastExpectedUsTradingDate（台北視角的最近預期美股交易日）----
{
  // 2026-08-10=週一、08-11=週二 … 08-15=週六、08-16=週日
  chk("週二 → 台北昨日（週一）", lastExpectedUsTradingDate("2026-08-11") === "2026-08-10");
  chk("週三 → 台北昨日", lastExpectedUsTradingDate("2026-08-12") === "2026-08-11");
  chk("週四 → 台北昨日", lastExpectedUsTradingDate("2026-08-13") === "2026-08-12");
  chk("週五 → 台北昨日", lastExpectedUsTradingDate("2026-08-14") === "2026-08-13");
  chk("週六 → 台北昨日（週五；美股週五收盤台北週六晨入庫）",
    lastExpectedUsTradingDate("2026-08-15") === "2026-08-14");
  chk("週日 → 上週五（-2）", lastExpectedUsTradingDate("2026-08-16") === "2026-08-14");
  chk("週一 → 上週五（-3）", lastExpectedUsTradingDate("2026-08-10") === "2026-08-07");
  chk("跨月：9/1 週二 → 8/31", lastExpectedUsTradingDate("2026-09-01") === "2026-08-31");
  // 美國國定假日（設計已知誤差，不處理）：例 2026-07-03（五）美國獨立日補假休市，
  // 台北週六 07-04 預期仍算 07-03——資料日停在 07-02 會被判 stale（觸發補跑但拿不到
  // 新資料、無害；AM us 卡當天缺席）。此測試把誤判行為釘住為「已知」而非回歸。
  chk("美國假日誤判情境：假日次晨預期日仍為假日當天（已知可接受）",
    lastExpectedUsTradingDate("2026-07-04") === "2026-07-03");
}

// ---- mock 基座（沿用 test/backup.mjs 慣例）----
function fakeKV(init = {}) {
  const m = new Map(Object.entries(init));
  return {
    _m: m,
    async get(k, type) { const v = m.get(k); if (v === undefined) return null; return type === "json" ? (typeof v === "string" ? JSON.parse(v) : v) : v; },
    async put(k, v) { m.set(k, v); },
  };
}
const ENV_BASE = { DATA_BASE: `${RAW_ORG}/taiwan-flow-live-v2/main/data` };
const ENV = (kv) => ({ ...ENV_BASE, GH_DISPATCH_TOKEN: "T", FLOW_KV: kv });
// mock fetch：/dispatches → 收 spy（含 body）；其餘視為 us.json（null 代表 404）
const mkFetch = (usObj, spy = [], dispatchStatus = 204) => async (u, init) => {
  const s = String(u);
  if (s.includes("/dispatches")) {
    spy.push({ url: s, body: JSON.parse(init.body) });
    return { status: dispatchStatus };
  }
  return { ok: usObj != null, status: usObj ? 200 : 404, json: async () => usObj };
};
// 台北 2026-08-11（週二）晨：預期美股交易日 = 08-10（週一）
const TUE = (hour, minute) => ({ date: "2026-08-11", dow: 2, hour, minute });
const STALE = { date: "2026-08-07", generated_at: "2026-08-11T05:10:00+08:00" };   // 空轉：gen 今日、date 落後
const FRESH = { date: "2026-08-10", generated_at: "2026-08-11T07:52:00+08:00" };

// ---- ② 時窗與守門 ----
{
  chk("時窗常數 07:00–08:05", US_CATCHUP_AFTER_MIN === 7 * 60 && US_CATCHUP_UNTIL_MIN === 8 * 60 + 5);
  const spy = [];
  const early = await runUsCatchup(ENV(fakeKV()), TUE(6, 55), mkFetch(STALE, spy));
  chk("06:55（起手窗喚醒）→ 窗外不動作、零網路", early.waiting === "outside-07:00-08:05" && spy.length === 0,
    JSON.stringify(early));
  const late = await runUsCatchup(ENV(fakeKV()), TUE(8, 10), mkFetch(STALE, spy));
  chk("08:10 → 窗外（不跟 08:10 渲染搶）", late.waiting === "outside-07:00-08:05" && spy.length === 0);
  const edge = await runUsCatchup(ENV(fakeKV()), TUE(8, 5), mkFetch(STALE, spy));
  chk("08:05 → 仍在窗內（inclusive）", edge.fired === true, JSON.stringify(edge));
  const t0800 = await runUsCatchup(ENV(fakeKV()), TUE(8, 0), mkFetch(STALE, []));
  chk("08:00（尾窗 cron 實際末輪）→ 窗內", t0800.fired === true);
}
{
  // 台北週日/週一早上不跑（美股週末無新資料）；週六照跑（美股週五收盤正是週六晨入庫）
  const spy = [];
  const sun = await runUsCatchup(ENV(fakeKV()), { date: "2026-08-16", dow: 0, hour: 7, minute: 30 },
    mkFetch(STALE, spy));
  chk("週日 → skipped、零網路", sun.skipped === "no-new-us-data-sun-mon" && spy.length === 0, JSON.stringify(sun));
  const mon = await runUsCatchup(ENV(fakeKV()), { date: "2026-08-10", dow: 1, hour: 7, minute: 30 },
    mkFetch(STALE, spy));
  chk("週一 → skipped", mon.skipped === "no-new-us-data-sun-mon");
  const sat = await runUsCatchup(ENV(fakeKV()), { date: "2026-08-15", dow: 6, hour: 7, minute: 30 },
    mkFetch({ date: "2026-08-13" }, spy));
  chk("週六 date 停週四 → 照補發", sat.fired === true, JSON.stringify(sat));
}
{
  // token 未設 → 靜默（同其他班慣例）
  const spy = [];
  const out = await runUsCatchup({ ...ENV_BASE, FLOW_KV: fakeKV() }, TUE(7, 30), mkFetch(STALE, spy));
  chk("無 token → skipped no-token、零網路", out.skipped === "no-token" && spy.length === 0);
}

// ---- ③ 判準與 dispatch ----
{
  // 資料日已達預期 → fresh 短路、不 dispatch
  const spy = [];
  const kv = fakeKV();
  const out = await runUsCatchup(ENV(kv), TUE(7, 30), mkFetch(FRESH, spy));
  chk("資料日達預期 → fresh、不 dispatch", out.fresh === true && spy.length === 0, JSON.stringify(out));
  chk("fresh → KV 不寫 dedup 鍵", kv._m.size === 0);
}
{
  // 空轉情境（generated_at 今日、date 落後）→ 照補發；dispatch 帶 inputs.rounds="2"
  const spy = [];
  const kv = fakeKV();
  const out = await runUsCatchup(ENV(kv), TUE(7, 30), mkFetch(STALE, spy));
  chk("date 未達預期 → fired＋預期日正確", out.fired === true && out.expected === "2026-08-10",
    JSON.stringify(out));
  chk("dispatch 對 us.yml 且 inputs.rounds=2", spy.length === 1
    && spy[0].url.includes("/taiwan-flow-live-v2/actions/workflows/us.yml/dispatches")
    && spy[0].body.inputs && spy[0].body.inputs.rounds === "2", JSON.stringify(spy[0]));
  chk("fired → KV 記 20 分時段桶鍵", kv._m.has(usCatchupKey("2026-08-11", 7 * 60 + 30)),
    [...kv._m.keys()].join(","));
  const js = (await kv.get(jobstatKey("2026-08-11"), "json")) || [];
  chk("fired → jobstat 記 us-catchup", js.some((j) => j.n === "us-catchup" && j.r === "fired"), JSON.stringify(js));
  // us.json 缺檔 → 同樣補發
  const gone = await runUsCatchup(ENV(fakeKV()), TUE(7, 30), mkFetch(null, []));
  chk("us.json 缺檔 → 照補發", gone.fired === true && gone.usDate === null, JSON.stringify(gone));
}

// ---- ④ 20 分 KV dedup ----
{
  chk("dedup 鍵含當日＋20 分時段桶", usCatchupKey("2026-08-11", 7 * 60 + 5) === "bkfired:20260811:uscatchup:21"
    && usCatchupKey("2026-08-11", 7 * 60 + 25) === "bkfired:20260811:uscatchup:22",
    usCatchupKey("2026-08-11", 7 * 60 + 5));
  const spy = [];
  const kv = fakeKV();
  await runUsCatchup(ENV(kv), TUE(7, 0), mkFetch(STALE, spy));
  const dup = await runUsCatchup(ENV(kv), TUE(7, 5), mkFetch(STALE, spy));
  chk("同 20 分桶再喚醒（07:00→07:05）→ deduped", dup.skipped === "deduped-20min" && spy.length === 1,
    JSON.stringify(dup));
  const next = await runUsCatchup(ENV(kv), TUE(7, 20), mkFetch(STALE, spy));
  chk("下一個 20 分桶（07:20）→ 再補發一次", next.fired === true && spy.length === 2, JSON.stringify(next));
  // 窗內 07:00-08:05 共跨 4 個桶（21~24）→ 一晨至多 4 次 dispatch
  const buckets = new Set();
  for (let m = 7 * 60; m <= 8 * 60 + 5; m++) buckets.add(Math.floor(m / 20));
  chk("整窗至多 4 個桶＝至多 4 次 dispatch", buckets.size === 4, [...buckets].join(","));
}

// ---- ⑤ dispatch 失敗 → 不寫 KV（下一桶自動重試）、回 error 不拋 ----
{
  const spy = [];
  const kv = fakeKV();
  const out = await runUsCatchup(ENV(kv), TUE(7, 30), mkFetch(STALE, spy, 401), { sleepFn: async () => {} });
  chk("dispatch 失敗 → 回 error", typeof out.error === "string", JSON.stringify(out));
  chk("dispatch 失敗 → dedup 鍵不寫（保留下一桶重試）", !kv._m.has(usCatchupKey("2026-08-11", 7 * 60 + 30)));
  const js = (await kv.get(jobstatKey("2026-08-11"), "json")) || [];
  chk("dispatch 失敗 → jobstat 記 error", js.some((j) => j.n === "us-catchup" && j.r === "error"), JSON.stringify(js));
}
// dry 模式：只回決策、不 dispatch、不寫 KV
{
  const spy = [];
  const kv = fakeKV();
  const out = await runUsCatchup(ENV(kv), TUE(7, 30), mkFetch(STALE, spy), { dry: true });
  chk("dry → wouldDispatch、零 dispatch、KV 不寫", out.wouldDispatch === true && spy.length === 0 && kv._m.size === 0,
    JSON.stringify(out));
}

console.log(`\n${fail === 0 ? "PASS" : "FAIL"}  ${pass} 通過 / ${fail} 失敗`);
process.exit(fail === 0 ? 0 : 1);
