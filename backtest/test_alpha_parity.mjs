// backtest/test_alpha_parity.mjs — 技術指標「JS 生產實作 ↔ Python 回測移植」一致性守門
//
// 為什麼需要這支：run_alpha_sweep.py 的 AS-07~14 用的是 worker/src/index.js 那五個純函式
// （sma/kd/macd/rsi/boll）與三個狀態描述（kdState/macdState/bollState）的 Python 移植。
// 同一套口徑的兩份實作，沒有守門就會靜默漂移（taiwan-flows 2026-07 的 bias20/四捨五入/
// tie-break 三次漂移就是這樣被 tests/parity.py 抓出來的）。
//
// 與 taiwan-flows/tests/extract_js.mjs 的差別（刻意）：那邊的來源是 index.html，
// 不是 module，只能用正則＋大括號配對把函式挖出來丟 vm。這裡 worker/src/index.js 本身
// 就是 ES module 且五個指標函式都 `export`（index.js:1787/1796/1808/1824/1839/1855），
// 所以**直接 import 真正在線上跑的那份**，不做文字抽取——少一層「抽錯了也不知道」的風險。
// 只有三個狀態函式沒 export（index.js:1898/1908/1926），才沿用 extract_js 的大括號配對法。
//
// 比對方式：合成序列 → 對每個前綴 index i 各算一次（等同生產 buildTechnical 對「當日」
// 與「前一日」各算一次的用法），Python 端則是一次算完整條路徑，逐 index 斷言零差異。
// 這同時驗證了 Python 端「路徑一次算完 == JS 逐前綴各算一次」這個前綴穩定性假設。
//
// 用法：node backtest/test_alpha_parity.mjs   （免 token、免網路、免快取；需要 python3）

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import vm from "node:vm";
import { spawnSync } from "node:child_process";
import { sma, kd, macd, rsi, boll } from "../worker/src/index.js";

const ROOT = path.resolve(import.meta.dirname, "..");

// ── 1. 抽出未 export 的三個狀態函式（大括號配對，沿用 taiwan-flows/tests/extract_js.mjs）──
const src = fs.readFileSync(path.join(ROOT, "worker", "src", "index.js"), "utf8");
function pickFunc(name) {
  const start = src.indexOf(`function ${name}(`);
  if (start < 0) throw new Error(`worker/src/index.js 找不到 function ${name}`);
  const open = src.indexOf("{", start);
  let depth = 0, inStr = null;
  for (let i = open; i < src.length; i++) {
    const c = src[i];
    if (inStr) {
      if (c === "\\") { i++; continue; }
      if (c === inStr) inStr = null;
    } else if (c === '"' || c === "'" || c === "`") {
      inStr = c;
    } else if (c === "{") {
      depth++;
    } else if (c === "}") {
      depth--;
      if (depth === 0) return src.slice(start, i + 1);
    }
  }
  throw new Error(`function ${name} 大括號未配對`);
}
const sandbox = { Math };
vm.createContext(sandbox);
new vm.Script(["kdState", "macdState", "bollState"].map(pickFunc).join("\n\n")).runInContext(sandbox);
const { kdState, macdState, bollState } = sandbox;

// ── 2. 合成序列（決定性 LCG，不用 Math.random；Python 端讀同一份 JSON）──────────
function lcg(seed) {
  let s = seed >>> 0;
  return () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296; };
}
function walk(n, seed, { base = 100, vol = 0.03, decimals = 2, drift = 0 } = {}) {
  const rnd = lcg(seed), h = [], l = [], c = [];
  let p = base;
  for (let i = 0; i < n; i++) {
    p = Math.max(1, p * (1 + drift + (rnd() - 0.5) * vol * 2));
    const px = decimals == null ? p : Number(p.toFixed(decimals));
    const hi = decimals == null ? px * (1 + rnd() * 0.02) : Number((px * (1 + rnd() * 0.02)).toFixed(decimals));
    const lo = decimals == null ? px * (1 - rnd() * 0.02) : Number((px * (1 - rnd() * 0.02)).toFixed(decimals));
    c.push(px); h.push(Math.max(hi, px)); l.push(Math.min(lo, px));
  }
  return { h, l, c };
}
function ramp(n, from, step) {
  const c = Array.from({ length: n }, (_, i) => Number((from + i * step).toFixed(4)));
  return { h: c.map((x) => x * 1.01), l: c.map((x) => x * 0.99), c };
}
function flat(n, v) {
  const c = Array(n).fill(v);
  return { h: [...c], l: [...c], c };
}

const CASES = {
  // 主力案例：一般隨機走勢（含 KD 交叉、MACD 翻正/翻負、RSI 極值、布林觸軌）
  walk_a: walk(180, 12345),
  walk_b: walk(180, 777, { base: 33.5, vol: 0.06 }),
  // 未取小數：逼出 r2 四捨五入的邊界（JS Math.round vs Python banker's rounding）
  walk_raw: walk(120, 424242, { base: 512.345678, vol: 0.045, decimals: null }),
  // 常數序列：KD rng=0→RSV=50、RSI 無波動=50、布林 rng=0→pb=0.5、MACD 全 0
  flat: flat(80, 42),
  // 單調上下：RSI 全漲=100 / 全跌=0 的特例分支
  up: ramp(80, 10, 0.5),
  down: ramp(80, 100, -0.5),
  // 長度邊界：布林 20、MACD slow+signal=35、RSI5 需 6 筆、KD 需 9 筆
  len5: walk(5, 9),
  len6: walk(6, 9),
  len9: walk(9, 9),
  len20: walk(20, 9),
  len34: walk(34, 9),
  len35: walk(35, 9),
};

// ── 3. JS 端：對每個前綴 index 各算一次（＝生產 buildTechnical 的用法）────────
function jsDump({ h, l, c }) {
  const out = [];
  for (let i = 0; i < c.length; i++) {
    const H = h.slice(0, i + 1), L = l.slice(0, i + 1), C = c.slice(0, i + 1);
    const cur = kd(H, L, C);
    const prev = i > 0 ? kd(h.slice(0, i), l.slice(0, i), c.slice(0, i)) : null;
    const m = macd(C);
    const b = boll(C);
    out.push({
      i,
      sma5: sma(C, 5), sma20: sma(C, 20),
      kd: cur,
      kd_state: cur ? kdState(cur, prev ? prev.k : null, prev ? prev.d : null) : null,
      macd: m,
      macd_state: m ? macdState(m) : null,
      rsi5: rsi(C, 5), rsi10: rsi(C, 10),
      boll: b,
      boll_state: b ? bollState(b.pb) : null,
    });
  }
  return out;
}

// ── 4. Python 端：呼叫 run_alpha_sweep.dump_indicators ─────────────────────
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "alpha-parity-"));
const inFile = path.join(tmp, "series.json"), outFile = path.join(tmp, "py.json");
fs.writeFileSync(inFile, JSON.stringify(CASES), "utf8");
const pyCode = `
import json, sys
sys.path.insert(0, ${JSON.stringify(path.join(ROOT, "backtest"))})
import run_alpha_sweep as a
data = json.load(open(sys.argv[1], encoding="utf-8"))
out = {k: a.dump_indicators(v) for k, v in data.items()}
json.dump(out, open(sys.argv[2], "w", encoding="utf-8"))
`;
const proc = spawnSync("python3", ["-c", pyCode, inFile, outFile], { encoding: "utf8" });
if (proc.status !== 0) {
  console.error("python3 端執行失敗：\n" + (proc.stderr || proc.stdout || proc.error));
  process.exit(1);
}
const pyAll = JSON.parse(fs.readFileSync(outFile, "utf8"));

// ── 5. 逐 index 逐欄比對（零容差；-0 與 0 在 === 下相等，不另處理）──────────
let checked = 0;
const fails = [];
function cmp(where, a, b) {
  if (a === null || a === undefined || b === null || b === undefined) {
    if ((a ?? null) !== (b ?? null)) fails.push(`${where}: JS=${JSON.stringify(a)} PY=${JSON.stringify(b)}`);
    return;
  }
  if (typeof a === "object") {
    const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
    for (const k of keys) cmp(`${where}.${k}`, a[k], b[k]);
    return;
  }
  checked++;
  if (a !== b) fails.push(`${where}: JS=${JSON.stringify(a)} PY=${JSON.stringify(b)}`);
}

for (const [name, series] of Object.entries(CASES)) {
  const js = jsDump(series), py = pyAll[name];
  if (!py || py.length !== js.length) {
    fails.push(`${name}: 長度不符 JS=${js.length} PY=${py ? py.length : "缺"}`);
    continue;
  }
  for (let i = 0; i < js.length; i++) {
    for (const k of Object.keys(js[i])) cmp(`${name}[${i}].${k}`, js[i][k], py[i][k]);
  }
}

// 覆蓋率自檢：合成序列必須真的踩到那些訊號分支，否則「零差異」是因為兩邊都沒算東西
const cover = { golden: 0, dead: 0, up: 0, dn: 0, rsiLo: 0, rsiHi: 0, bollUp: 0, bollDn: 0 };
for (const series of Object.values(CASES)) {
  for (const r of jsDump(series)) {
    if (r.kd_state && r.kd_state.startsWith("黃金交叉") && r.kd.k < 50) cover.golden++;
    if (r.kd_state && r.kd_state.startsWith("死亡交叉") && r.kd.k > 50) cover.dead++;
    if (r.macd_state && r.macd_state.startsWith("柱狀翻正")) cover.up++;
    if (r.macd_state && r.macd_state.startsWith("柱狀翻負")) cover.dn++;
    if (r.rsi5 != null && r.rsi5 < 20) cover.rsiLo++;
    if (r.rsi5 != null && r.rsi5 > 80) cover.rsiHi++;
    if (r.boll_state === "觸/破上軌") cover.bollUp++;
    if (r.boll_state === "觸/破下軌") cover.bollDn++;
  }
}
const uncovered = Object.entries(cover).filter(([, v]) => v === 0).map(([k]) => k);

fs.rmSync(tmp, { recursive: true, force: true });

console.log(`比對案例 ${Object.keys(CASES).length} 組、數值欄位 ${checked} 個`);
console.log("AS-07~14 分支覆蓋：" + Object.entries(cover).map(([k, v]) => `${k}=${v}`).join(" "));
if (uncovered.length) {
  console.error(`✗ 合成序列未覆蓋分支：${uncovered.join(", ")}——這組序列驗不到那些訊號`);
  process.exit(1);
}
if (fails.length) {
  console.error(`✗ ${fails.length} 處不一致（前 20 筆）：`);
  for (const f of fails.slice(0, 20)) console.error("  " + f);
  process.exit(1);
}
console.log("✓ JS ↔ Python 指標與狀態描述零差異");
