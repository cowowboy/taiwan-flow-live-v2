// LINE Flex 圖卡渲染層 離線單元測試（無需 token/網路）
// 執行：cd worker && node test/cards.mjs
// 守的規格：docs/line-cards-spec.md 第 2 節（Flex 硬限制）、第 4 節（誠實原則）、
//           3B.3（C 類白名單）、第 6 節驗收條件。
import { lineRequest, cardBubble, disclaimerBubble, buildCardCarousels,
  cardsFallbackText, FX_FORBIDDEN, FX_BLOCKED_CARDS, FX_COLORS } from "../src/index.js";

let pass = 0, fail = 0;
function chk(name, ok, detail) {
  if (ok) { pass++; } else { fail++; console.log(`  x ${name}  ${detail || ""}`); }
}

const mkCard = (i) => ({
  id: `t-${i}`, title: `測試卡${i}`, sub: "07-28 · 口徑說明",
  rows: [
    { l: "0001", m: "範例甲", r: "+4.8億", c: "up" },
    { l: "0002", m: "範例乙", r: "−2.1億", c: "down" },
    { l: "0003", m: "範例丙", r: "0.0億", c: "neutral" },
  ],
  note: "依範例金額降序", foot: "歷史分離度 0.92%、非單調",
});

// ---- lineRequest 相容性 ----
{
  const r1 = lineRequest("tk", "uid", "hello");
  const b1 = JSON.parse(r1.init.body);
  chk("lineRequest 字串→text message（既有呼叫端不變）",
    b1.messages.length === 1 && b1.messages[0].type === "text" && b1.messages[0].text === "hello");
  const r2 = lineRequest("tk", "uid", [{ type: "flex", altText: "a", contents: { type: "carousel", contents: [] } }]);
  const b2 = JSON.parse(r2.init.body);
  chk("lineRequest 陣列→原樣帶入 messages", b2.messages.length === 1 && b2.messages[0].type === "flex");
  chk("lineRequest 帶 Authorization", r2.init.headers.Authorization === "Bearer tk");
}

// ---- cardBubble 基本結構 ----
{
  const b = cardBubble(mkCard(1));
  chk("bubble type/size", b.type === "bubble" && b.size === "kilo");
  chk("body 為 vertical box", b.body.type === "box" && b.body.layout === "vertical");
  const js = JSON.stringify(b);
  chk("含標題與資料列", js.includes("測試卡1") && js.includes("範例甲"));
  chk("紅漲綠跌 hex", js.includes(FX_COLORS.up) && js.includes(FX_COLORS.down));
  chk("口徑註記在卡底", js.includes("依範例金額降序") && js.includes("非單調"));
}

// ---- wrap:true 全樹斷言（規格：所有 text 元件必須 wrap）----
{
  const walk = (n, out = []) => {
    if (n && typeof n === "object") {
      if (n.type === "text") out.push(n);
      for (const v of Object.values(n)) walk(v, out);
    }
    return out;
  };
  const texts = walk(buildCardCarousels([mkCard(1), mkCard(2)], "alt"));
  chk("樹內有 text 元件", texts.length > 0);
  chk("所有 text 元件 wrap:true", texts.every((t) => t.wrap === true),
    `未 wrap: ${texts.filter((t) => t.wrap !== true).length} 個`);
}

// ---- 誠實原則守門 ----
{
  let threw = null;
  try { cardBubble({ id: "bad", title: "本週最強股", paras: [] }); } catch (e) { threw = e.message; }
  chk("禁用字「最強」建構即擋", threw && threw.includes("最強"), threw);
  threw = null;
  try { cardBubble({ id: "bad2", title: "排行", note: "建議關注前三檔" }); } catch (e) { threw = e.message; }
  chk("禁用字「建議關注」在 note 也擋", threw && threw.includes("建議關注"), threw);
  chk("禁用字清單非空且含該買/必漲", FX_FORBIDDEN.includes("該買") && FX_FORBIDDEN.includes("必漲"));
}

// ---- C 類白名單守門（回測未過不得上線）----
{
  for (const id of ["flows-sync-1", "flows-sync-2", "flows-oppose-1", "flows-oppose-2", "pm-aetf-1"]) {
    let threw = false;
    try { cardBubble({ id, title: "x", paras: [] }); } catch { threw = true; }
    chk(`C 類 ${id} 被擋`, threw);
  }
  chk("白名單恰為 5 張", FX_BLOCKED_CARDS.size === 5);
}

// ---- carousel 切分與上限 ----
{
  const cards14 = Array.from({ length: 14 }, (_, i) => mkCard(i));
  const msgs = buildCardCarousels(cards14, "股市雷達 07-28");
  // 14 卡 + 1 免責 = 15 bubble → 12 + 3
  chk("15 bubble 切成 2 carousel", msgs.length === 2);
  chk("首個 carousel 恰 12 bubble", msgs[0].contents.contents.length === 12);
  chk("次個 carousel 3 bubble", msgs[1].contents.contents.length === 3);
  const last = msgs[1].contents.contents.at(-1);
  chk("免責卡固定壓底", JSON.stringify(last).includes("關於這份清單"));
  chk("每則有 altText 且 ≤1500", msgs.every((m) => m.altText && m.altText.length <= 1500));
  chk("同 carousel 內 size 一致",
    msgs.every((m) => new Set(m.contents.contents.map((b) => b.size)).size === 1));
  chk("每 carousel < 50KB", msgs.every((m) => JSON.stringify(m.contents).length < 50000));
  // 38 張（第一期實際卡數）→ 39 bubble → 4 carousel，仍 ≤5 message
  const msgs38 = buildCardCarousels(Array.from({ length: 38 }, (_, i) => mkCard(i)), "alt");
  chk("38 卡 → 4 carousel（12/12/12/3）", msgs38.length === 4
    && msgs38.map((m) => m.contents.contents.length).join(",") === "12,12,12,3");
  // 60 卡 → 61 bubble → 6 carousel → 超過 5 要炸
  let threw = false;
  try { buildCardCarousels(Array.from({ length: 60 }, (_, i) => mkCard(i)), "alt"); } catch { threw = true; }
  chk("超過 5 message 上限拋錯", threw);
}

// ---- bubble 30KB 上限 ----
{
  let threw = null;
  const big = { id: "big", title: "大", paras: [ "x".repeat(31000) ] };
  try { cardBubble(big); } catch (e) { threw = e.message; }
  chk("單 bubble >30KB 拋錯", threw && threw.includes("30KB"), threw);
}

// ---- 純文字降級版 ----
{
  const txt = cardsFallbackText([mkCard(1), mkCard(2)], "07-28");
  chk("降級版含標題與日期", txt.includes("盤後圖卡】07-28") && txt.includes("測試卡1"));
  chk("降級版含免責句", txt.includes("非買賣訊號"));
  chk("降級版是純字串無 JSON", typeof txt === "string" && !txt.includes('"type"'));
}

// ---- disclaimerBubble 自身合法 ----
{
  const d = disclaimerBubble();
  chk("免責卡可建構且含免責句", JSON.stringify(d).includes("非買賣訊號"));
}

console.log(`\nPASS  ${pass} 通過 / ${fail} 失敗`);
process.exit(fail ? 1 : 0);
