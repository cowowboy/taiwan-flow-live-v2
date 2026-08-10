// 晨間 LINE 圖卡（AM slot，2026-08-10）離線單元測試（無需 token/網路——fetch/KV 全 mock）
// 執行：cd worker && node test/morningcards.mjs
// 覆蓋：① fxCardMorningBrief 映射與容錯（life 缺漏、空 top3、全空 skip、中性化）
//       ② buildCardsData slot=am 新鮮度守門（晨報 date／morning generated_at，新鮮/不新鮮）
//       ③ pushMorningCards 視窗/通道/dedup/manifest gate/推播組成/失敗重試
//       ④ runMorning 接線（週末守門、渲染 dispatch 視窗與冪等、錯誤隔離）
import { fxCardMorningBrief, FX_AM_CARDS, FX_AM_LONGFORM_CARD, fxLongformCard,
  FX_LONGFORM_CARD, DAILY_BRIEF_URL, buildCardsData, cardSourceUrls,
  pushMorningCards, runMorning, runCardsRenderAm, alertedKey, bkfiredKey,
  CARDS_AM_RENDER_AFTER_MIN, CARDS_AM_RENDER_UNTIL_MIN,
  CARDS_AM_PUSH_AFTER_MIN, CARDS_AM_PUSH_UNTIL_MIN, jobstatKey } from "../src/index.js";

let pass = 0, fail = 0;
function chk(name, ok, detail) {
  if (ok) { pass++; } else { fail++; console.log(`  x ${name}  ${detail || ""}`); }
}

// ---- mock 基座（沿用 test/cardsend.mjs 慣例）----
function fakeKV(init = {}) {
  const m = new Map(Object.entries(init));
  return {
    _m: m,
    async get(k, type) { const v = m.get(k); if (v === undefined) return null; return type === "json" ? (typeof v === "string" ? JSON.parse(v) : v) : v; },
    async put(k, v) { m.set(k, v); },
  };
}
const TODAY = "2026-08-10";        // 週一
const YDAY = "2026-08-07";         // 上週五（morning 三卡的資料日）
const ENV_BASE = { DATA_BASE: "https://raw.githubusercontent.com/shihpc/taiwan-flow-live-v2/main/data" };
const ENV_LINE = { ...ENV_BASE, LINE_TOKEN: "tk", LINE_USER_ID: "U1" };
const URLS = cardSourceUrls(ENV_BASE, TODAY, "am");
const MF_URL = `${ENV_BASE.DATA_BASE}/cards/am/manifest.json`;
const DEDUP_KEY = alertedKey(TODAY, "cards-am");
const RENDER_KEY = bkfiredKey(TODAY, "cardsrender-am");
const TP_PUSH = { date: TODAY, hour: 8, minute: 20, dow: 1 };     // 推播窗下緣
const TP_DISPATCH = { date: TODAY, hour: 8, minute: 10, dow: 1 }; // 渲染 dispatch 窗內唯一一輪

const mkFetch = (byUrl, spy = [], mode = {}) => async (u, init) => {
  const s = String(u).split("?")[0];
  if (s.startsWith("https://api.line.me/")) {
    spy.push({ url: s, kind: "line", body: JSON.parse(init.body) });
    if (mode.lineFail) return { ok: false, status: 500 };
    return { ok: true, status: 200, json: async () => ({}) };
  }
  if (s.includes("/dispatches")) {
    spy.push({ url: s, kind: "dispatch", body: JSON.parse(init.body) });
    return { status: 204 };
  }
  spy.push({ url: s, kind: "product" });
  const obj = byUrl[s];
  return { ok: obj != null, status: obj ? 200 : 404, json: async () => obj };
};
const lineCalls = (spy) => spy.filter((c) => c.kind === "line");
const productUrls = (spy) => new Set(spy.filter((c) => c.kind === "product").map((c) => c.url));

// ---- fixtures ----
const BRIEF = (o = {}) => ({
  schema: 1, date: TODAY, edition: 4, generated_at: `${TODAY}T07:30:00+08:00`,
  top3: [
    { title: "台積電法說會登場", why: "外資聚焦先進製程展望" },
    { title: "美 CPI 數據今晚公布", why: "市場預期年增 2.9%" },
    { title: "新台幣升破 29 元", why: "出口商拋匯壓力升溫" },
  ],
  positioning: [
    { market: "台股", fact: "上週五收 24,100 點、量 4,200 億", view: "電子權值買盤最強，短線動能仍在" },
    { market: "美股", fact: "道瓊收漲 0.5%", view: "科技股領漲" },
  ],
  week_events: [
    { when: "週一 8/11", what: "台積電法說會" },
    { when: "週四 8/14", what: "美國 7 月 CPI" },
  ],
  quote: "紀律比預測重要。",
  life: [{ cat: "天氣", note: "午後雷陣雨" }],
  ...o,
});
const MORNING = (o = {}) => ({
  date: TODAY, generated_at: `${TODAY}T06:47:00+08:00`,
  chips: { inst: { foreign: 12.3, trust: -4.5, dealer: 0.1, date: YDAY },
    it3: [{ c: "2330", n: "台積電" }], it3_sell: [], aetf: ["主動ETF共加碼3檔"], aetf_date: YDAY },
  ...o,
});
const DAYSUMMARY = () => ({
  date: YDAY,
  index: { tse: { val: 24100, chg: 1.2 }, otc: { val: 250, chg: 0.8 } },
  share_top: { n: "晶圓製造", share_pct: 19.1 },
  pts_top: { n: "運算設備", pts: 55.2 },
});
const US = () => ({
  date: "2026-08-08", generated_at: `${TODAY}T05:10:00+08:00`,
  brief: "美股收漲，科技股買盤最大。",
  groups: [{ g: "指數", rows: [{ n: "道瓊", chg: 0.5 }, { n: "那斯達克", chg: 1.1 }] }],
});
const SRC = (o = {}) => ({
  [URLS.dailyBrief]: o.brief === undefined ? BRIEF() : o.brief,
  [URLS.morning]: o.morning === undefined ? MORNING() : o.morning,
  [URLS.daysummary]: o.daysummary === undefined ? DAYSUMMARY() : o.daysummary,
  [URLS.us]: o.us === undefined ? US() : o.us,
});
const AM_IMG = (id) =>
  `https://raw.githubusercontent.com/shihpc/taiwan-flow-live-v2/main/data/cards/am/${id}.png?d=${TODAY}`;
const MANIFEST = (o = {}) => ({
  date: TODAY, generated_at: `${TODAY}T08:12:00+08:00`,
  images: {
    [FX_AM_LONGFORM_CARD]: AM_IMG(FX_AM_LONGFORM_CARD),
    "news-morning-2": AM_IMG("news-morning-2"),
    "news-morning-3": AM_IMG("news-morning-3"),
    "news-morning-4": AM_IMG("news-morning-4"),
  },
  ratios: { "news-morning-2": "1040:900" },
  previews: { [FX_AM_LONGFORM_CARD]: `${AM_IMG(FX_AM_LONGFORM_CARD)}&p=1` },
  ...o,
});
const FULL = (o = {}) => ({ ...SRC(o), [MF_URL]: o.manifest === undefined ? MANIFEST() : o.manifest });

// ---- ① fxCardMorningBrief 映射與容錯 ----
{
  const c = fxCardMorningBrief({ dailyBrief: BRIEF() });
  chk("晨報卡 kind=longform＋標題", c.kind === "longform" && c.title === "每日晨報", JSON.stringify(c).slice(0, 120));
  chk("sub 帶日期與期數", c.sub.includes(TODAY) && c.sub.includes("第4期"), c.sub);
  const idx = (t) => c.paras.findIndex((p) => p.includes(t));
  chk("四段順序：三件事→定位→本週→一句話",
    idx("今日三件事") === 0 && idx("今日三件事") < idx("開盤前定位")
    && idx("開盤前定位") < idx("本週關鍵事件") && idx("本週關鍵事件") < idx("今日一句話"),
    c.paras.filter((p) => p.startsWith("##")).join(" | "));
  chk("top3 逐條編號＋why", c.paras.includes("1. 台積電法說會登場")
    && c.paras.some((p) => p.includes("外資聚焦先進製程展望")), c.paras.slice(1, 3).join(" / "));
  chk("positioning 帶市場標＋fact＋view", c.paras.some((p) =>
    p.startsWith("【台股】") && p.includes("24,100") && p.includes("解讀：")), c.paras[idx("開盤前定位") + 1]);
  chk("week_events 帶 when：what", c.paras.includes("週一 8/11：台積電法說會"));
  chk("quote 進今日一句話", c.paras[c.paras.length - 1] === "紀律比預測重要。");
  chk("life 欄不入卡（僅容錯不呈現）", !JSON.stringify(c.paras).includes("午後雷陣雨"));
  chk("中性化（買盤最強→買盤最大）", !JSON.stringify(c.paras).includes("最強")
    && JSON.stringify(c.paras).includes("買盤最大"));
  chk("note 標明不含操作建議", c.note.includes("不含買賣點位與操作建議"), c.note);
  chk("帶專屬 disclaimer", typeof c.disclaimer === "string" && c.disclaimer.includes("非投資建議"));
}
{
  // 容錯：life 缺／空、top3 空、全空、無日期、源缺
  const noLife = fxCardMorningBrief({ dailyBrief: BRIEF({ life: undefined }) });
  chk("life 缺 → 照組卡", noLife.kind === "longform");
  const emptyLife = fxCardMorningBrief({ dailyBrief: BRIEF({ life: [] }) });
  chk("life 空陣列 → 照組卡", emptyLife.kind === "longform");
  const noTop3 = fxCardMorningBrief({ dailyBrief: BRIEF({ top3: [] }) });
  chk("top3 空 → 無該段、其餘照出", !JSON.stringify(noTop3.paras).includes("今日三件事")
    && JSON.stringify(noTop3.paras).includes("開盤前定位"), JSON.stringify(noTop3.paras).slice(0, 100));
  const empty = fxCardMorningBrief({ dailyBrief: BRIEF({ top3: [], positioning: [], week_events: [], quote: "" }) });
  chk("四段全空 → skip", !!empty.skip, JSON.stringify(empty));
  const noDate = fxCardMorningBrief({ dailyBrief: BRIEF({ date: null }) });
  chk("無日期 → skip", !!noDate.skip, JSON.stringify(noDate));
  let threw = null;
  try { fxCardMorningBrief({ dailyBrief: null }); } catch (e) { threw = String(e && e.message); }
  chk("dailyBrief 缺 → 拋錯（外層 catch 成 skip）", threw && threw.includes("dailyBrief"), threw);
  const badTop3 = fxCardMorningBrief({ dailyBrief: BRIEF({ top3: "not-array", week_events: [{ when: "x" }] }) });
  chk("top3 非陣列／event 缺 what → 不炸、逐段容錯", badTop3.kind === "longform"
    && !JSON.stringify(badTop3.paras).includes("今日三件事")
    && !JSON.stringify(badTop3.paras).includes("本週關鍵事件"), JSON.stringify(badTop3.paras));
}
{
  chk("fxLongformCard slot 對照", fxLongformCard("am") === FX_AM_LONGFORM_CARD
    && fxLongformCard("pm") === FX_LONGFORM_CARD && FX_AM_LONGFORM_CARD === "am-brief-1");
  chk("FX_AM_CARDS ＝晨報＋morning2/3/4 共 4 張", FX_AM_CARDS.size === 4
    && ["am-brief-1", "news-morning-2", "news-morning-3", "news-morning-4"].every((id) => FX_AM_CARDS.has(id)));
}

// ---- ② buildCardsData slot=am 新鮮度守門 ----
{
  const spy = [];
  const out = await buildCardsData(ENV_BASE, TP_PUSH, mkFetch(SRC(), spy), { slot: "am" });
  chk("am 全新鮮 → 4 張卡", out.cards.length === 4, out.cards.map((c) => c.id).join(","));
  chk("am → date=今日、slot=am", out.date === TODAY && out.slot === "am", JSON.stringify({ d: out.date, s: out.slot }));
  chk("am 卡集合＝FX_AM_CARDS", out.cards.every((c) => FX_AM_CARDS.has(c.id)));
  chk("晨報卡在列且 kind=longform", out.cards.some((c) => c.id === FX_AM_LONGFORM_CARD && c.kind === "longform"));
  const got = productUrls(spy);
  chk("am 只抓 4 支源（不抓晚間 15 支）", got.size === 4
    && Object.values(URLS).every((u) => got.has(u)), [...got].join(","));
  chk("am 源含 dailyBrief（taiwan-stock-news raw）", URLS.dailyBrief === DAILY_BRIEF_URL
    && got.has(DAILY_BRIEF_URL));
}
{
  // 晨報不新鮮（date=昨日）→ 晨報卡不進 payload、morning 三卡照出、date 仍今日
  const stale = await buildCardsData(ENV_BASE, TP_PUSH,
    mkFetch(SRC({ brief: BRIEF({ date: YDAY }) }), []), { slot: "am" });
  chk("晨報非今日 → 晨報卡不出", !stale.cards.some((c) => c.id === FX_AM_LONGFORM_CARD)
    && stale.cards.length === 3, stale.cards.map((c) => c.id).join(","));
  chk("晨報非今日 → date=今日（morning gate 保證）", stale.date === TODAY, stale.date);
  // 晨報缺檔 → 同上
  const gone = await buildCardsData(ENV_BASE, TP_PUSH, mkFetch(SRC({ brief: null }), []), { slot: "am" });
  chk("晨報缺檔 → 3 張、不炸", gone.cards.length === 3 && gone.date === TODAY);
}
{
  // morning.json 不新鮮（generated_at 昨日）→ morning 三卡全擋、晨報照出
  const out = await buildCardsData(ENV_BASE, TP_PUSH,
    mkFetch(SRC({ morning: MORNING({ generated_at: `${YDAY}T06:47:00+08:00` }) }), []), { slot: "am" });
  chk("morning 非今晨 → 只剩晨報卡", out.cards.length === 1
    && out.cards[0].id === FX_AM_LONGFORM_CARD, out.cards.map((c) => c.id).join(","));
  chk("morning 非今晨 → date=晨報 date", out.date === TODAY);
  // 全部不新鮮 → 空卡清單＋date=null（Python 拒渲染）
  const none = await buildCardsData(ENV_BASE, TP_PUSH,
    mkFetch(SRC({ brief: BRIEF({ date: YDAY }),
      morning: MORNING({ generated_at: `${YDAY}T06:47:00+08:00` }) }), []), { slot: "am" });
  chk("全不新鮮 → 0 卡＋date=null", none.cards.length === 0 && none.date === null, JSON.stringify(none));
  // 守門用 generated_at 而非晚間 baseline gate：baseline 缺完全不影響 am
  chk("am 不依賴 baseline（源清單根本沒有它）", !("baseline" in URLS));
}
{
  // pm 路徑不受影響：預設 slot 走既有 15 支源
  const spy = [];
  await buildCardsData(ENV_BASE, TP_PUSH, mkFetch({}, spy));
  chk("pm 預設 → 仍抓 15 支晚間源", productUrls(spy).size === 15, `${productUrls(spy).size}`);
}

// ---- ③ pushMorningCards ----
{
  // 視窗守門
  const spy = [];
  const kv = fakeKV();
  const early = await pushMorningCards({ ...ENV_LINE, FLOW_KV: kv },
    { ...TP_PUSH, minute: 10 }, mkFetch(FULL(), spy));
  chk("08:10 → waiting before-08:20＋零網路", early.waiting === "before-08:20" && spy.length === 0, JSON.stringify(early));
  const late = await pushMorningCards({ ...ENV_LINE, FLOW_KV: kv },
    { ...TP_PUSH, minute: 55 }, mkFetch(FULL(), spy));
  chk("08:55 → skipped after-08:50", late.skipped === "after-08:50", JSON.stringify(late));
  chk("視窗常數 08:20–08:50", CARDS_AM_PUSH_AFTER_MIN === 8 * 60 + 20 && CARDS_AM_PUSH_UNTIL_MIN === 8 * 60 + 50);
  // 通道守門
  const noCh = await pushMorningCards({ ...ENV_BASE, FLOW_KV: fakeKV() }, TP_PUSH, mkFetch(FULL(), []));
  chk("無 LINE → skipped no-channel", noCh.skipped === "no-channel", JSON.stringify(noCh));
  // dedup
  const spy2 = [];
  const dup = await pushMorningCards({ ...ENV_LINE, FLOW_KV: fakeKV({ [DEDUP_KEY]: "pushed" }) },
    TP_PUSH, mkFetch(FULL(), spy2));
  chk("已推過 → skipped already-pushed＋零網路", dup.skipped === "already-pushed" && spy2.length === 0);
}
{
  // manifest gate：缺檔／非當日／零圖 → waiting、不寫 KV、零 LINE
  for (const [label, mf] of [
    ["manifest 缺檔", null],
    ["manifest 非當日", MANIFEST({ date: YDAY })],
    ["manifest 零圖", MANIFEST({ images: {} })],
    ["manifest images 非物件", MANIFEST({ images: 42 })],
  ]) {
    const spy = [];
    const kv = fakeKV();
    const out = await pushMorningCards({ ...ENV_LINE, FLOW_KV: kv }, TP_PUSH,
      mkFetch(FULL({ manifest: mf }), spy));
    chk(`${label} → waiting manifest-not-ready`, out.waiting === "manifest-not-ready", JSON.stringify(out));
    chk(`${label} → 不寫 KV、零 LINE`, !kv._m.has(DEDUP_KEY) && lineCalls(spy).length === 0);
  }
}
{
  // 正常推播：flex carousel（morning 三卡掛 hero）＋晨報長圖單獨 image message 壓尾
  const spy = [];
  const kv = fakeKV();
  const out = await pushMorningCards({ ...ENV_LINE, FLOW_KV: kv }, TP_PUSH, mkFetch(FULL(), spy));
  chk("正常 → sent＋3 張 carousel 卡＋imgs=3", out.sent === true && out.cards === 3 && out.imgs === 3,
    JSON.stringify(out));
  chk("正常 → 恰一次 LINE push", lineCalls(spy).length === 1);
  const msgs = lineCalls(spy)[0].body.messages;
  const last = msgs[msgs.length - 1];
  chk("最後一則＝晨報長圖 image message", last.type === "image"
    && last.originalContentUrl === AM_IMG(FX_AM_LONGFORM_CARD)
    && last.previewImageUrl === `${AM_IMG(FX_AM_LONGFORM_CARD)}&p=1`, JSON.stringify(last));
  chk("其餘皆 flex carousel", msgs.slice(0, -1).every((m) => m.type === "flex" && m.contents.type === "carousel"),
    msgs.map((m) => m.type).join(","));
  chk("altText＝晨間圖卡 N/M｜日期", msgs.slice(0, -1).every((m, i) =>
    m.altText === `股市雷達 晨間圖卡 ${i + 1}/${msgs.length - 1}｜${TODAY}`), msgs[0].altText);
  const bubbles = msgs.filter((m) => m.type === "flex").flatMap((m) => m.contents.contents);
  const heroed = bubbles.filter((b) => b.hero);
  chk("morning 三卡都掛 hero（am 目錄 URL）", heroed.length === 3
    && heroed.every((b) => b.hero.url.includes("/data/cards/am/")), `heroed=${heroed.length}`);
  chk("有 ratios 的卡用實際比例", heroed.some((b) => b.hero.aspectRatio === "1040:900"));
  chk("晨報卡不進 carousel", !JSON.stringify(msgs.filter((m) => m.type === "flex")).includes("每日晨報"));
  chk("成功 → KV 記 pushed", kv._m.get(DEDUP_KEY) === "pushed");
  const js = (await kv.get(jobstatKey(TODAY), "json")) || [];
  chk("成功 → jobstat 記 cards-am pushed", js.some((j) => j.n === "cards-am" && j.r === "pushed"
    && j.x.includes("cards=3") && j.x.includes("lf=attached")), JSON.stringify(js));
  // 再喚醒 → 去重
  const again = await pushMorningCards({ ...ENV_LINE, FLOW_KV: kv }, { ...TP_PUSH, minute: 30 }, mkFetch(FULL(), []));
  chk("推過再喚醒 → already-pushed", again.skipped === "already-pushed");
}
{
  // 晨報圖缺（manifest 無 previews）→ 只送 flex；晨報資料缺 → 同樣只送 flex
  const spy = [];
  const out = await pushMorningCards({ ...ENV_LINE, FLOW_KV: fakeKV() }, TP_PUSH,
    mkFetch(FULL({ manifest: MANIFEST({ previews: {} }) }), spy));
  chk("晨報 preview 缺 → 照推、零 image message", out.sent === true
    && !lineCalls(spy)[0].body.messages.some((m) => m.type === "image"), JSON.stringify(out));
  const spy2 = [];
  const out2 = await pushMorningCards({ ...ENV_LINE, FLOW_KV: fakeKV() }, TP_PUSH,
    mkFetch(FULL({ brief: null }), spy2));
  chk("晨報資料缺 → 照推 3 卡、零 image", out2.sent === true && out2.cards === 3
    && !lineCalls(spy2)[0].body.messages.some((m) => m.type === "image"), JSON.stringify(out2));
}
{
  // morning 三卡不新鮮、只剩晨報 → 只送 image message（無 carousel 也能推）
  const spy = [];
  const out = await pushMorningCards({ ...ENV_LINE, FLOW_KV: fakeKV() }, TP_PUSH,
    mkFetch(FULL({ morning: MORNING({ generated_at: `${YDAY}T06:47:00+08:00` }) }), spy));
  chk("只剩晨報 → sent、0 carousel 卡", out.sent === true && out.cards === 0, JSON.stringify(out));
  const msgs = lineCalls(spy)[0].body.messages;
  chk("payload 只有一則 image", msgs.length === 1 && msgs[0].type === "image", JSON.stringify(msgs));
}
{
  // 全部不新鮮（manifest 卻在——昨日渲染殘留不可能 date=今日，此為防禦性情境）→ skip-empty
  const kv = fakeKV();
  const out = await pushMorningCards({ ...ENV_LINE, FLOW_KV: kv }, TP_PUSH,
    mkFetch(FULL({ brief: BRIEF({ date: YDAY }),
      morning: MORNING({ generated_at: `${YDAY}T06:47:00+08:00` }) }), []));
  chk("卡全不新鮮 → skipped no-cards＋KV skip-empty", out.skipped === "no-cards"
    && kv._m.get(DEDUP_KEY) === "skip-empty", JSON.stringify(out));
}
{
  // LINE 失敗 → 拋錯、不寫 KV（下輪 10 分後重試）、jobstat 記 error
  const kv = fakeKV();
  let threw = null;
  try { await pushMorningCards({ ...ENV_LINE, FLOW_KV: kv }, TP_PUSH, mkFetch(FULL(), [], { lineFail: true })); }
  catch (e) { threw = String(e && e.message); }
  chk("LINE 失敗 → 拋錯", threw && threw.includes("LINE push 失敗"), threw);
  chk("LINE 失敗 → 不寫去重鍵", !kv._m.has(DEDUP_KEY), [...kv._m.keys()].join(","));
  const js = (await kv.get(jobstatKey(TODAY), "json")) || [];
  chk("LINE 失敗 → jobstat 記 error", js.some((j) => j.n === "cards-am" && j.r === "error"), JSON.stringify(js));
  // dry 模式
  const dry = await pushMorningCards({ ...ENV_LINE, FLOW_KV: fakeKV() }, TP_PUSH, mkFetch(FULL(), []), { dry: true });
  chk("dry → wouldPush/imgs/longform", dry.wouldPush === 3 && dry.imgs === 3 && dry.longform === "attached",
    JSON.stringify(dry));
}

// ---- ④ runMorning 接線＋runCardsRenderAm ----
{
  // 週末守門（summary-am cron dow 為 *，程式內以台北 dow 防禦）
  const spy = [];
  const out = await runMorning({ ...ENV_LINE, GH_DISPATCH_TOKEN: "T", FLOW_KV: fakeKV() },
    { date: "2026-08-09", hour: 8, minute: 20, dow: 0 }, mkFetch(FULL(), spy));
  chk("週日 → skipped non-trading-day＋零網路", out.skipped === "non-trading-day" && spy.length === 0,
    JSON.stringify(out));
}
{
  // 08:10（dispatch 窗內唯一一輪）：dispatch cards.yml inputs.slot=am；push 尚在等待
  const spy = [];
  const kv = fakeKV();
  const out = await runMorning({ ...ENV_LINE, GH_DISPATCH_TOKEN: "T", FLOW_KV: kv },
    TP_DISPATCH, mkFetch(FULL(), spy));
  chk("08:10 → render fired", out.render && out.render.fired === true, JSON.stringify(out.render));
  const d = spy.find((c) => c.kind === "dispatch");
  chk("dispatch 對 cards.yml 且 inputs.slot=am", d && d.url.includes("/cards.yml/")
    && d.body.inputs && d.body.inputs.slot === "am", d && JSON.stringify(d.body));
  chk("dispatch 冪等鍵 cardsrender-am 已寫", kv._m.has(RENDER_KEY), [...kv._m.keys()].join(","));
  chk("08:10 → push waiting（<08:20）", out.push && out.push.waiting === "before-08:20", JSON.stringify(out.push));
  // 同鍵再喚醒 → already-fired
  const again = await runCardsRenderAm({ ...ENV_LINE, GH_DISPATCH_TOKEN: "T", FLOW_KV: kv },
    TP_DISPATCH, mkFetch(FULL(), []));
  chk("再喚醒 → render already-fired", again.skipped === "already-fired", JSON.stringify(again));
  // 窗外（08:20）→ render 不動作
  const outside = await runCardsRenderAm({ ...ENV_LINE, GH_DISPATCH_TOKEN: "T", FLOW_KV: fakeKV() },
    TP_PUSH, mkFetch(FULL(), []));
  chk("08:20 → render 窗外不 dispatch", outside.waiting === "outside-08:05-08:15", JSON.stringify(outside));
  chk("render 窗常數 08:05–08:15", CARDS_AM_RENDER_AFTER_MIN === 8 * 60 + 5
    && CARDS_AM_RENDER_UNTIL_MIN === 8 * 60 + 15);
}
{
  // 08:20：render 窗外、push 實推；無 token 時 render skip、push 照常
  const spy = [];
  const kv = fakeKV();
  const out = await runMorning({ ...ENV_LINE, FLOW_KV: kv }, TP_PUSH, mkFetch(FULL(), spy));
  chk("無 token → render skipped no-token", out.render && out.render.skipped === "no-token", JSON.stringify(out.render));
  chk("08:20 → push sent", out.push && out.push.sent === true, JSON.stringify(out.push));
  chk("push 成功 → KV pushed", kv._m.get(DEDUP_KEY) === "pushed");
}
{
  // push 失敗 → runMorning 隔離錯誤＋alertJob（tag cards-am-err ≠ 去重鍵）
  const kv = fakeKV();
  const out = await runMorning({ ...ENV_LINE, GH_DISPATCH_TOKEN: "T", FLOW_KV: kv },
    TP_PUSH, mkFetch(FULL(), [], { lineFail: true }));
  chk("push 失敗 → error 被隔離不拋出", out.push && typeof out.push.error === "string", JSON.stringify(out.push));
  chk("push 失敗 → 告警鍵 cards-am-err", kv._m.has(alertedKey(TODAY, "cards-am-err")), [...kv._m.keys()].join(","));
  chk("push 失敗 → 去重鍵未寫（下輪重試）", !kv._m.has(DEDUP_KEY));
}

console.log(`\n${fail === 0 ? "PASS" : "FAIL"}  ${pass} 通過 / ${fail} 失敗`);
process.exit(fail === 0 ? 0 : 1);
