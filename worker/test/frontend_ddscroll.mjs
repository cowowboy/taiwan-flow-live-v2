// 下鑽捲動的守門測試（2026-09-03 新增）。
//
// 為什麼需要：使用者回報「點輪動雷達／象限都會跳到下面」。
// 根因是 renderOverview()／render()／renderFlow() 三處在結尾都無條件做
//   if(open) document.getElementById("dd…").scrollIntoView(...)
// 而 render() 會被輪動雷達的分頁與選取、象限下鑽、視角 pill、以及 pull() 的
// 20 秒自動刷新全部觸發。只要先在下方表格展開過一個次產業，state.sub[tab].open
// 就一直留著 → 之後在上方點任何東西（甚至什麼都不點、等 20 秒）都會被 smooth-scroll
// 拉到頁面最下面的下鑽區塊。
//
// 這支把 ddScroll() 的「只在換對象時才捲」釘住。用 index.html 的實際實作跑，
// 不是複製一份——複製的話改壞了照樣全綠。
//
// 執行：cd worker && node test/frontend_ddscroll.mjs

import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";

const ROOT = path.resolve(import.meta.dirname, "..", "..");
const html = fs.readFileSync(path.join(ROOT, "index.html"), "utf8");

let pass = 0, fail = 0;
function chk(name, ok, detail) {
  if (ok) { pass++; } else { fail++; console.log(`  x ${name}  ${detail || ""}`); }
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

// 假 DOM：只記錄 scrollIntoView 被呼叫幾次
let scrolls = [];
const sandbox = {
  Object, console,
  DD_SCROLLED: {},
  document: { getElementById: (id) => ({ scrollIntoView: () => scrolls.push(id) }) },
};
vm.createContext(sandbox);
vm.runInContext(pickFunc("ddScroll") + "\nglobalThis.ddScroll=ddScroll;", sandbox);
const { ddScroll } = sandbox;

const reset = () => { scrolls = []; for (const k in sandbox.DD_SCROLLED) delete sandbox.DD_SCROLLED[k]; };

// ---- 使用者實際回報的情境 ----
reset();
ddScroll("ov", "ddOv", "IC設計");                       // 點次產業展開
chk("展開次產業會捲過去", scrolls.length === 1, `實得 ${scrolls.length}`);

for (let i = 0; i < 5; i++) ddScroll("ov", "ddOv", "IC設計");  // 點輪動雷達／象限／自動刷新
chk("展開狀態下重繪 5 次都不再捲（就是回報的那個 bug）",
    scrolls.length === 1, `實得 ${scrolls.length} 次`);

ddScroll("ov", "ddOv", "光通訊");                        // 換一個次產業
chk("換展開對象要重新捲", scrolls.length === 2, `實得 ${scrolls.length}`);

ddScroll("ov", "ddOv", null);                            // 再點一次收合
ddScroll("ov", "ddOv", null);
chk("收合時不捲", scrolls.length === 2, `實得 ${scrolls.length}`);

ddScroll("ov", "ddOv", "光通訊");                        // 收合後重新展開同一個
chk("收合後重新展開同一個要捲", scrolls.length === 3, `實得 ${scrolls.length}`);

// ---- 各 tab 互不干擾（#dd 是 chain/exchange/flow 共用的 id）----
reset();
ddScroll("chain", "dd", "半導體");
ddScroll("exchange", "dd", "半導體");
chk("不同 tab 展開同名對象各自要捲", scrolls.length === 2, `實得 ${scrolls.length}`);
ddScroll("chain", "dd", "半導體");
chk("回到已捲過的 tab 不重捲", scrolls.length === 2, `實得 ${scrolls.length}`);

// ---- 沒有殘留的舊寫法 ----
const legacy = [...html.matchAll(/getElementById\("dd(Ov)?"\)\s*;?\s*if\s*\(\s*dd\s*\)\s*dd\.scrollIntoView/g)];
chk("三處呼叫點都改用 ddScroll，沒有殘留的無條件捲動", legacy.length === 0,
    `殘留 ${legacy.length} 處`);
chk("ddScroll 有被三處呼叫", (html.match(/\bddScroll\(/g) || []).length === 4,
    `實得 ${(html.match(/\bddScroll\(/g) || []).length} 次（含 1 次定義）`);

console.log(`${fail ? "✗" : "✓"} frontend_ddscroll：${pass} 通過${fail ? `、${fail} 失敗` : ""}`);
process.exit(fail ? 1 : 0);
