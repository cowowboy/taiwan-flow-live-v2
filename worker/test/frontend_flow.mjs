// 前端 flow 判準的守門測試（2026-07-30 新增）。
//
// 為什麼需要：`flowDegenerate` 這套判準前後端各有一份實作（前端 index.html、
// worker/src/index.js），註解寫「改一邊要改另一邊」但**沒有任何機制守著**——
// 驗收時把前端判準改回 `return f!=null` 的突變全綠存活，因為整個 repo 沒有任何
// 測試讀 index.html。姊妹站 taiwan-flows 用 tests/extract_js.mjs 做這件事，這裡比照。
//
// 做法：按名稱從 index.html 抽出宣告，在 vm 沙箱跑（這些都是純函式），
// 與 worker 端同名函式對同一組輸入逐一比對。
//
// 執行：cd worker && node test/frontend_flow.mjs

import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { flowDegenerate as wFlowDegenerate } from "../src/index.js";

const ROOT = path.resolve(import.meta.dirname, "..", "..");
const html = fs.readFileSync(path.join(ROOT, "index.html"), "utf8");

let pass = 0, fail = 0;
function chk(name, ok, detail) {
  if (ok) { pass++; } else { fail++; console.log(`  x ${name}  ${detail || ""}`); }
}

// ---- 抽宣告（單行 const 用行首錨定；function 用大括號配對）----
function pickConst(name) {
  const m = html.match(new RegExp(`^const ${name}\\s*=.*$`, "m"));
  if (!m) throw new Error(`index.html 找不到 const ${name}`);
  return m[0];
}
function pickFunc(name) {
  const start = html.indexOf(`function ${name}(`);
  if (start < 0) throw new Error(`index.html 找不到 function ${name}`);
  const open = html.indexOf("{", start);
  let depth = 0, inStr = null;
  for (let i = open; i < html.length; i++) {
    const c = html[i];
    if (inStr) { if (c === "\\") { i++; continue; } if (c === inStr) inStr = null; }
    else if (c === '"' || c === "'" || c === "`") inStr = c;
    else if (c === "{") depth++;
    else if (c === "}") { depth--; if (depth === 0) return html.slice(start, i + 1); }
  }
  throw new Error(`function ${name} 大括號未配對`);
}

const sandbox = { Array, Object, Math, JSON, console };
vm.createContext(sandbox);
vm.runInContext([
  pickFunc("flowDegenerate"),
  pickConst("flowUsableForRank"),
  pickConst("flowUsableForQuad"),
  // const 宣告只存在於 script 的語彙範圍、不會掛到沙箱 global（function 才會），
  // 所以明確搬出來給下面取用
  "globalThis.flowDegenerate=flowDegenerate;",
  "globalThis.flowUsableForRank=flowUsableForRank;",
  "globalThis.flowUsableForQuad=flowUsableForQuad;",
].join("\n"), sandbox);
const { flowDegenerate, flowUsableForRank, flowUsableForQuad } = sandbox;

// ---- 四種時段的代表性 flow（欄位形狀照 computeFlow 實際產出）----
const SUBS = [{ name: "IC設計", n: 12, d_yi: 3.2, c1: 1.5, c2: 1.2, ret: 1.1, y: [1, 0] }];
const CASES = {
  "盤中健康 11:00": { degenerate: false, frames: { 10: "10:50", 30: "10:30" },
    subs: SUBS, mkt: { d10_yi: 88.8, d30_yi: 240.0 } },
  "開盤初期 09:15（只有10分窗，d30_yi=null）": { degenerate: false, frames: { 10: "09:05" },
    subs: SUBS, mkt: { d10_yi: 66.9, d30_yi: null } },
  "收盤殘影 13:50（兩窗不同格但起點≥13:30）": { degenerate: true, frames: { 10: "13:35", 30: "13:20" },
    subs: SUBS, mkt: { d10_yi: 1.6, d30_yi: 157.6 } },
  "收盤殘影 14:30（兩窗同格）": { degenerate: true, frames: { 10: "13:35", 30: "13:35" },
    subs: SUBS, mkt: { d10_yi: 0.3, d30_yi: 0.3 } },
  "null（盤前/週末 frame 過期）": null,
};

// ---- 1. 前後端 flowDegenerate 必須逐案一致 ----
for (const [name, f] of Object.entries(CASES)) {
  const a = flowDegenerate(f), b = wFlowDegenerate(f);
  chk(`前後端一致：${name}`, a === b, `前端=${a} worker=${b}`);
}
// 舊 payload（無 degenerate 欄）也要一致
for (const [name, f] of Object.entries({
  "舊payload 兩窗同格": { frames: { 10: "13:35", 30: "13:35" }, subs: SUBS, mkt: { d30_yi: 1 } },
  "舊payload 兩窗不同格": { frames: { 10: "13:20", 30: "13:00" }, subs: SUBS, mkt: { d30_yi: 1 } },
})) {
  chk(`前後端一致（相容路徑）：${name}`, flowDegenerate(f) === wFlowDegenerate(f));
}

// ---- 2. 兩個述詞的真值表（各消費者需要的欄位不同，不得互相誤傷）----
{
  const C = (k) => CASES[k];
  chk("盤中健康 → 排行可用", flowUsableForRank(C("盤中健康 11:00")));
  chk("盤中健康 → 象限可用", flowUsableForQuad(C("盤中健康 11:00")));

  // 這條是上一版的迴歸：開盤 09:10–09:30 d30_yi=null，湧入/退出只需要 10 分窗，
  // 不該因為 30 分窗還沒成形就退回顯示「昨日收盤定格」。
  const open = C("開盤初期 09:15（只有10分窗，d30_yi=null）");
  chk("開盤初期 → 排行**可用**（不得退昨日定格）", flowUsableForRank(open));
  chk("開盤初期 → 象限不可用（無 d30 當分母，退定格正確）", !flowUsableForQuad(open));

  for (const k of ["收盤殘影 13:50（兩窗不同格但起點≥13:30）", "收盤殘影 14:30（兩窗同格）"]) {
    chk(`${k} → 排行不可用`, !flowUsableForRank(C(k)));
    chk(`${k} → 象限不可用`, !flowUsableForQuad(C(k)));
  }
  chk("null → 兩者皆不可用", !flowUsableForRank(null) && !flowUsableForQuad(null));

  // subs 空（真的很冷的盤中）→ 排行沒東西可畫，但象限仍可用
  const noSubs = { ...C("盤中健康 11:00"), subs: [] };
  chk("subs 空 → 排行不可用", !flowUsableForRank(noSubs));
  chk("subs 空 → 象限仍可用（象限不讀 subs）", flowUsableForQuad(noSubs));
}

// ---- 3. 原始碼守門：象限/treemap 的分子分母必須同源 ----
// 這是實測偏差 127 倍的那個 bug：ovF30() 已改吃定格，分母 d30 卻留著即時值。
// 純函式測不到（它在 render 函式體內），故以原始碼樣式守門。
{
  chk("已無 `d30==null?ovFlowLast():null` 短路寫法",
    !html.includes("d30==null?ovFlowLast():null"));
  const sameSrc = html.match(/const d30=last\?last\.mkt\.d30_yi:\(fl&&fl\.mkt\?fl\.mkt\.d30_yi:null\);/g) || [];
  chk("兩個消費點都用同源寫法（ovTreemapBadge / ovQuadrantHtml）",
    sameSrc.length === 2, `找到 ${sameSrc.length} 處`);
}

console.log(`frontend_flow: pass=${pass} fail=${fail}`);
if (fail) process.exit(1);
