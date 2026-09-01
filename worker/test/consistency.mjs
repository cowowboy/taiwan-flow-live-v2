// wrangler.toml 的 DATA_BASE 與 src/index.js 的 RAW_ORG 必須指向同一個組織/主機。
//
// 兩者是分開的換址點(TOML 沒有變數插值,vars 無法由程式碼衍生),忘記同步的
// 後果是 Worker 一半讀新家、一半讀舊家,而且不會報錯——只會拿到過期或 404 的
// 資料,在 /health 上表現成「某幾站永遠 red」。這支測試把它變成部署前就會擋下來的失敗。
import { readFileSync } from "node:fs";
import { RAW_ORG, raw, rawBase } from "../src/index.js";

const toml = readFileSync(new URL("../wrangler.toml", import.meta.url), "utf-8");
const m = toml.match(/^\s*DATA_BASE\s*=\s*"([^"]+)"/m);
if (!m) { console.error("❌ wrangler.toml 找不到 DATA_BASE"); process.exit(1); }
const dataBase = m[1];

let fail = 0;
const check = (ok, msg) => { if (!ok) { console.error("❌ " + msg); fail++; } };

check(dataBase.startsWith(RAW_ORG + "/"),
  `DATA_BASE 與 RAW_ORG 不同源\n   RAW_ORG   = ${RAW_ORG}\n   DATA_BASE = ${dataBase}`);
check(dataBase === `${RAW_ORG}/taiwan-flow-live-v2/main/data`,
  `DATA_BASE 應為 \${RAW_ORG}/taiwan-flow-live-v2/main/data,實際 ${dataBase}`);
check(rawBase("postmkt") === `${RAW_ORG}/postmkt/main`, "rawBase() 組法不符");
check(raw("taiwan-flows", "data/latest.json") === `${RAW_ORG}/taiwan-flows/main/data/latest.json`,
  "raw() 組法不符");

// 防回歸:除了 RAW_ORG 那一行,程式碼裡不該再出現寫死的 raw 網址
const src = readFileSync(new URL("../src/index.js", import.meta.url), "utf-8");
const hard = src.split("\n").filter((l) =>
  l.includes("raw.githubusercontent.com/") && !l.trimStart().startsWith("//") && !l.includes("RAW_ORG ="));
check(hard.length === 0,
  `還有 ${hard.length} 處寫死的 raw 網址(應改用 raw()/rawBase()):\n   ` + hard.map(l=>l.trim()).join("\n   "));

if (fail) process.exit(1);
console.log(`✅ PASS  換址點一致(RAW_ORG = ${RAW_ORG})`);
