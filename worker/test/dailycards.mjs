// LINE 圖卡資料組裝層（Phase B1）離線單元測試（無需 token/網路）
// 執行：cd worker && node test/dailycards.mjs
// 守的規格：docs/line-cards-spec.md 第 9.3 節驗收條件。
// fixture 皆從 2026-07-24 真實 data/ 檔抄縮減版（每源 3~5 筆），baseline 用 Phase A 後 schema。
import { buildDailyCards, fxRegime, FX_CARD_BUILDERS,
  cardBubble, buildCardCarousels, cardsFallbackText } from "../src/index.js";

let pass = 0, fail = 0;
function chk(name, ok, detail) {
  if (ok) { pass++; } else { fail++; console.log(`  x ${name}  ${detail || ""}`); }
}

// ---- fixtures（縮減自真檔）----
// totals：21 個交易日、taiex 緩升（末日 > 20MA → bull）；含一筆 null（實檔 2026-06-30 為 null 的同款情境）
function mkTotals({ bear = false, days = 22 } = {}) {
  const dates = [], rows = {};
  for (let i = 0; i < days; i++) {
    const d = `2026-06-${String(i + 1).padStart(2, "0")}`;
    dates.push(d);
    const taiex = i === 3 ? null : (bear ? 46000 - i * 100 : 44000 + i * 100);
    rows[d] = {
      tse: { f_net_k: -60950489, t_net_k: 4763491, d_net_k: -11151495, turnover_k: 827129512 },
      otc: { f_net_k: -6252746, t_net_k: 1088025, d_net_k: -1234567, turnover_k: 60240000 },
      taiex,
    };
  }
  return { dates, rows };
}
const FIX = () => ({
  dateStr: "2026-07-24",
  baseline: {  // Phase A 後 schema：stocks [a5,it,fi,y1,y2,ints,nl,its,nh,a20]、subs_y [y1,y2,C,R]
    date: "2026-07-24", tot5: 1109333144542,
    stocks: {
      "3231": [5e9, 2, 3, 1, 0, 12.3, 0, 0, 0, 4e9],    // 土洋同買＋追高警示
      "2454": [9e9, 2, 2, 1, 1, 8.0, 0, 0, 0, 7e9],     // 土洋同買＋追高警示
      "2330": [5e10, 0, 1, 0, 0, 3.2, 0, 0, 1, 4.5e10], // 突破新高
      "3481": [8e8, 0, 0, 0, 0, -1.0, 1, 0, 0, 1e9],    // 跌破新低
      "6669": [6e9, 1, 0, -1, 0, -12.5, 0, 2, 0, 5e9],  // 退出＋法人賣
      "9997": [3e7, 2, 2, 1, 0, 89.4, 0, 0, 1, 2.5e7],  // 冷門股：全訊號命中但當日額<1億，必須被流動性過濾擋下
    },
    subs_y: { "運算設備": [1, 0, 2.1, 0.034], "晶圓製造": [1, 1, 1.8, 0.021], "金屬零件": [-1, 0, null, null] },
  },
  flowsDaily: {  // taiwan-flows data/daily/*.json 同款 cols（張/千元/%）
    date: "2026-07-24",
    cols: ["code", "close", "chg_pct", "vol", "amt", "t_net", "t_amt", "f_net", "f_amt", "d_net", "d_amt",
      "t_inv", "f_shares", "f_pct", "f_buy", "f_sell", "t_buy", "t_sell", "d_buy", "d_sell"],
    rows: [
      ["3231", 179, 3.17, 32435, 12000000, 100, 500000, 32435, 5805883, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
      ["2454", 1300, 1.2, 5000, 9000000, 50, 300000, 3000, 3290026, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
      ["2330", 2340, -2.9, 60000, 140000000, 0, 0, -190, -446500, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
      ["3481", 49.5, -3.0, 20000, 990000, 0, 0, -100, -49500, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
      ["6669", 3950, -1.5, 3000, 11850000, -50, -197500, -200, -790000, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
      ["9997", 30, 5.0, 100, 30000, 10, 300, 50, 1500, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    ],
  },
  totals: mkTotals(),
  daysummary: {
    date: "2026-07-24",
    index: { tse: { val: 43654.84, chgP: -1195.97, chg: -2.66, amt_yi: 7780.1 },
      otc: { val: 377.63, chgP: -14.48, chg: -3.69, amt_yi: 1862 } },
    breadth: { up: 612, down: 1483 },
    tone: "大盤 -1196.0 點（-2.66%）；廣度 漲612／跌1483；全日成交佔比最高 晶圓製造（19.1%），貢獻最強 電信服務業（+7.6 點）。",
    stocks_top5: [{ c: "6669", n: "緯穎", pts: 15.29 }, { c: "3231", n: "緯創", pts: 5.54 }],
    stocks_bot3: [{ c: "2330", n: "台積電", pts: -451.39 }, { c: "2308", n: "台達電", pts: -78.1 }],
    subs_top5: [{ n: "電信服務業", pts: 7.64, amt_yi: 55.6, share_pct: 0.7, n_stk: 4 },
      { n: "貨櫃航運", pts: 5.13, amt_yi: 46.7, share_pct: 0.6, n_stk: 5 }],
    subs_bot3: [{ n: "晶圓製造", pts: -544.89, amt_yi: 1483.9, share_pct: 19.1, n_stk: 15 }],
    chain_top5: [{ n: "電信網路", pts: 8.1, amt_yi: 60.2, share_pct: 0.8, n_stk: 6 }],   // Phase A 補欄
    chain_bot3: [{ n: "半導體", pts: -600.4, amt_yi: 2100.5, share_pct: 27.0, n_stk: 40 }],
    subs_all: [{ n: "電信服務業", pts: 7.64, amt_yi: 55.6, share_pct: 0.7, n_stk: 4 },
      { n: "貨櫃航運", pts: 5.13, amt_yi: 46.7, share_pct: 0.6, n_stk: 5 },
      { n: "晶圓製造", pts: -544.89, amt_yi: 1483.9, share_pct: 19.1, n_stk: 15 }],
    share_top: { n: "晶圓製造", pts: -544.89, amt_yi: 1483.9, share_pct: 19.1, n_stk: 15 },
    pts_top: { n: "電信服務業", pts: 7.64, amt_yi: 55.6, share_pct: 0.7, n_stk: 4 },
  },
  vix: { price: 26800, contract: "202608", vix: 38.2 },
  flowsLatest: {
    date: "2026-07-24",
    pages: {
      foreign: {
        buy_by_amt: [{ code: "3231", name: "緯創", net_amt_k: 5805883 }, { code: "6488", name: "環球晶", net_amt_k: 3290026 }],
        sell_by_amt: [{ code: "2330", name: "台積電", net_amt_k: -30160000 }],
        futures_card: { date: "2026-07-24", oi_net_lots: -76260, oi_net_amount_k: -668364,
          vs_prev_month_lots: 6803, prev_month_end: "2026-06-30" },
      },
      trust: {
        buy_by_amt: [{ code: "2454", name: "聯發科", net_amt_k: 1500000 }],
        sell_by_amt: [{ code: "2303", name: "聯電", net_amt_k: -800000 }],
      },
      etf: { stats: {
        all: { count: 343, mktcap_k: 10804111003, turnover_k: 69314575, f_amt_k: -7104642, t_amt_k: 146183, d_amt_k: -13071112 },
        nonbond: { count: 227, mktcap_k: 7920273917, turnover_k: 64565945, f_amt_k: -7404677, t_amt_k: 119372, d_amt_k: -12793246 },
        bond: { count: 116, mktcap_k: 2883837086, turnover_k: 4748630, f_amt_k: 300035, t_amt_k: 26811, d_amt_k: -277866 },
      } },
    },
  },
  foreignHistory: {
    latest_date: "2026-07-24",
    daily: {
      "2026-07-18": { tse: { net_k: 5000000 }, otc: { net_k: 100000 } },   // 週六前（上週五）
      "2026-07-21": { tse: { net_k: 17343993 }, otc: { net_k: -6258028 } },
      "2026-07-22": { tse: { net_k: 17343993 }, otc: { net_k: -6258028 } },
      "2026-07-23": { tse: { net_k: 6957913 }, otc: { net_k: -5264683 } },
      "2026-07-24": { tse: { net_k: -60950489 }, otc: { net_k: -6252746 } },
    },
  },
  lastweek: { week: "2026-07-13", generated_at: "2026-07-20T12:32:31+08:00",
    stocks: { "2330": 6e11, "3231": 9e10, "2454": 4.5e10 }, tot: { twse: 5.5e12, tpex: 1.16e12 } },
  aetfLatest: { run_date: "2026-07-24", etfs: {} },   // 組裝層目前不消費，佔位驗容錯
  aetfDiff: {
    primary_date: "2026/07/24",
    etfs: {
      "00400A": { name: "主動國泰動能高息", aum: null, twse_aum_yi: 261.89, est_flow: 0, n_buy: 0, n_sell: 0 },
      "00401A": { name: "主動摩根台灣鑫收", aum: 2702280029, twse_aum_yi: 27.0, est_flow: 0, n_buy: 1, n_sell: 1 },
      "00405A": { name: "主動統一台股增長", aum: null, twse_aum_yi: 380.5, est_flow: 0, n_buy: 1, n_sell: 1 },
    },
    stocks: [
      { c: "6669", n: "緯穎", zh: 140, val: 802200000, rzh: 140, rval: 802200000 },
      { c: "2330", n: "台積電", zh: -190, val: -446500000, rzh: -190, rval: -446500000 },
      { c: "8210", n: "勤誠", zh: -50, val: -54000000, rzh: -50, rval: -54000000 },
    ],
  },
  postmkt: {
    date: "2026-07-24",
    blocktrade: { date: "2026-07-24", rows: [
      { c: "3665", n: "貿聯-KY", type: "配對交易", price: 2320, vol: 252000, money: 584640000 },
      { c: "2330", n: "台積電", type: "配對交易", price: 2353.48, vol: 70500, money: 165920340 },
    ] },
    lending: { date: "2026-07-24", rows: [
      { c: "3481", n: "群創", plat_total: 959956, sbl_short_bal: 798919, margin_bal: 120000 },
      { c: "2330", n: "台積電", plat_total: 500000, sbl_short_bal: 300000, margin_bal: 30000 },
      { c: "2454", n: "聯發科", plat_total: 200000, sbl_short_bal: 900000, margin_bal: 250000 },
    ] },
  },
  mktbal: {
    latest_date: "2026-07-24",
    daily: [
      { date: "2026-07-23", margin_shares: 9349915, margin_money: 582626351000, sbl_short_shares: 30114887000, sbl_short_value: 3675432234940 },
      { date: "2026-07-24", margin_shares: 9354810, margin_money: 577059537000, sbl_short_shares: 29678608000, sbl_short_value: 3546326606150 },
    ],
  },
  morning: {
    date: "2026-07-24",
    chips: {
      inst: { date: "2026-07-23", foreign: 69.6, trust: 73.7, dealer: 42.0 },
      it3: [{ c: "2454", n: "聯發科" }, { c: "1303", n: "南亞" }],
      it3_sell: [{ c: "2303", n: "聯電" }],
      aetf: ["多檔同買：貿聯-KY、南亞塑膠", "次產業最大加碼：電池管理系統 10.4億"],
      aetf_date: "2026/07/23",
    },
  },
  us: {
    date: "2026-07-23",
    brief: "7/23美股大跌，費半-0.5%，台積ADR-1.3%(溢價+12%)，電子開盤中性，特斯拉-14.5%領跌。",
    groups: [
      { g: "指數", rows: [{ s: "^GSPC", n: "S&P 500", c: 7408.3, chg: -1.21, d: "2026-07-23" },
        { s: "^IXIC", n: "那斯達克", c: 25137.69, chg: -2.15, d: "2026-07-23" }] },
      { g: "台股ADR", rows: [{ s: "TSM", n: "台積電ADR", c: 415.58, chg: -1.34, d: "2026-07-23" }] },
    ],
  },
});

const ALL_IDS = FX_CARD_BUILDERS.map(([id]) => id);
const B_IDS = ["v2-rank-1", "flows-foreign-1", "flows-trust-1", "pm-aetf-2", "pm-aetf-4",
  "pm-aetf-5", "pm-lending-3", "pm-lending-4", "pm-lending-6"];
const SIG_IDS = ["sig-sub-surge", "sig-dual-buy", "sig-new-high", "sig-new-low", "sig-exit-sell", "sig-surge-warn"];

// ---- 1. 完整 fixture → 33 張全產出 ----
{
  const { cards, skipped } = buildDailyCards(FIX());
  chk("builder 表共 33 張", FX_CARD_BUILDERS.length === 33, `${FX_CARD_BUILDERS.length}`);
  chk("完整 fixture 33 張全產出", cards.length === 33, `cards=${cards.length} skipped=${JSON.stringify(skipped)}`);
  chk("skipped 空", skipped.length === 0, JSON.stringify(skipped));
  chk("id 集合與 builder 表一致", JSON.stringify(cards.map((c) => c.id)) === JSON.stringify(ALL_IDS));
  chk("每張卡 sub 帶資料日", cards.every((c) => /資料日/.test(c.sub || "")),
    cards.filter((c) => !/資料日/.test(c.sub || "")).map((c) => c.id).join(","));
  chk("每張卡有 rows 或 paras", cards.every((c) => (c.rows || []).length || (c.paras || []).length));
  // C 類不在組裝表內
  chk("C 類 5 張不在表內", !ALL_IDS.some((id) =>
    ["flows-sync-1", "flows-sync-2", "flows-oppose-1", "flows-oppose-2", "pm-aetf-1"].includes(id)));
  chk("第二期圖表卡不在表內", !ALL_IDS.includes("v2-ov-9") && !ALL_IDS.includes("v2-ov-10"));
}
// ---- 2. 排序正確性抽查 ----
{
  const { cards } = buildDailyCards(FIX());
  const by = (id) => cards.find((c) => c.id === id);
  const c1 = by("sig-sub-surge");
  chk("卡1 依 R 降序", c1.rows[0].m === "運算設備" && c1.rows[1].m === "晶圓製造",
    JSON.stringify(c1.rows.map((r) => r.m)));
  const c2 = by("sig-dual-buy");
  chk("卡2 依 f_amt 降序（3231 5805883k > 2454 3290026k）",
    c2.rows[0].l === "3231" && c2.rows[1].l === "2454", JSON.stringify(c2.rows));
  chk("卡2 名稱由跨源 join 取得", c2.rows[0].m === "緯創");
  const c3 = by("sig-new-high");
  chk("卡3 只收 nh==1", c3.rows.length === 1 && c3.rows[0].l === "2330");
  chk("卡3 note 標母體無正超額", /超額為負|41\.7/.test(c3.foot || c3.note || ""));
  const c4 = by("sig-new-low");
  chk("卡4 note 標未排除跌停", /未排除跌停/.test(c4.note));
  const c5 = by("sig-exit-sell");
  chk("卡5 note 標不排序", /不排序/.test(c5.note) && /順序不含強弱/.test(c5.note));
  const c6 = by("sig-surge-warn");
  chk("卡6 S 值降序（2454 S=9e9/7e9? no：amt*1000/a5→2454 1.0x < 3231 2.4x）",
    c6.rows[0].l === "3231", JSON.stringify(c6.rows));
  chk("卡6 note 標不代表強弱", /不代表強弱/.test(c6.note));
  const g = by("v2-global-1");
  chk("global-1 VIX 值帶入", JSON.stringify(g.rows).includes("38.2"));
  const r1 = by("v2-rank-1");
  chk("rank-1 依佔比降序（2330 最大）", r1.rows[0].l === "2330", JSON.stringify(r1.rows[0]));
  chk("rank-1 括號帶上週佔比", /上週/.test(r1.rows[0].r));
  const l4 = by("pm-lending-4");
  chk("lending-4 依 sbl_short_bal 降序（2454 900000 居首）", l4.rows[0].l === "2454");
  const ff = by("flows-ff-1");
  chk("ff-1 近5日新→舊＋本週累計（本週=07-21..24 共4日）",
    ff.rows[0].m === "07-24" && /本週累計（4日）/.test(ff.rows[ff.rows.length - 1].m), JSON.stringify(ff.rows.map((r) => r.m)));
}
// ---- 3. B 類 9 張 note 標排序欄位 ----
{
  const { cards } = buildDailyCards(FIX());
  for (const id of B_IDS) {
    const c = cards.find((x) => x.id === id);
    chk(`B 類 ${id} note 標排序依據`, c && /依.+(降序|排序|分組)/.test(c.note || ""), c && c.note);
  }
}
// ---- 4. regime 閘門：空頭只抑制卡 1/2，卡 3-6 不變 ----
{
  chk("fxRegime bull", fxRegime(mkTotals()).regime === "bull");
  chk("fxRegime bear", fxRegime(mkTotals({ bear: true })).regime === "bear");
  chk("fxRegime 不足20筆 → bull＋未判定", (() => { const r = fxRegime(mkTotals({ days: 10 }));
    return r.regime === "bull" && r.undetermined === true; })());
  const fx = FIX(); fx.totals = mkTotals({ bear: true });
  const { cards, skipped } = buildDailyCards(fx);
  const sk = Object.fromEntries(skipped.map((s) => [s.id, s.reason]));
  chk("空頭：卡1 skipped reason=regime-bear", sk["sig-sub-surge"] === "regime-bear");
  chk("空頭：卡2 skipped reason=regime-bear", sk["sig-dual-buy"] === "regime-bear");
  chk("空頭：卡3-6 照常產出", ["sig-new-high", "sig-new-low", "sig-exit-sell", "sig-surge-warn"]
    .every((id) => cards.some((c) => c.id === id)));
  chk("空頭：其餘 27 張不受影響", cards.length === 31, `${cards.length}`);
  // regime 未判定 → 卡1/2 照發並在 note 標註
  const fx2 = FIX(); fx2.totals = mkTotals({ days: 10 });
  const r2 = buildDailyCards(fx2);
  const c1 = r2.cards.find((c) => c.id === "sig-sub-surge");
  chk("regime 未判定：卡1 照發＋note 標註", c1 && /regime 未判定/.test(c1.note));
}
// ---- 5. 逐源缺失 → 對應卡 skipped、其他不受影響 ----
{
  const DEP = {   // 來源鍵 → 預期受影響卡
    baseline: SIG_IDS,
    flowsDaily: ["sig-dual-buy", "sig-surge-warn", "v2-rank-1"],
    daysummary: ["v2-global-1", "v2-ov-1", "v2-ov-5", "v2-ov-6", "v2-ov-7", "v2-ov-8",
      "v2-ov-14", "v2-chain-1", "news-morning-3"],
    flowsLatest: ["flows-hdr-2", "flows-etf-1", "flows-foreign-1", "flows-trust-1"],
    foreignHistory: ["flows-ff-1"],
    postmkt: ["pm-block-1", "pm-lending-3", "pm-lending-4", "pm-lending-6"],
    mktbal: ["pm-mktbal-1", "pm-mktbal-2"],
    morning: ["news-morning-2"],
    us: ["news-morning-4"],
    aetfDiff: ["pm-aetf-2", "pm-aetf-4", "pm-aetf-5"],
  };
  for (const [src, ids] of Object.entries(DEP)) {
    const fx = FIX(); fx[src] = null;
    const { cards, skipped } = buildDailyCards(fx);
    const skIds = skipped.map((s) => s.id).sort();
    chk(`缺 ${src} → 只有對應卡 skipped`, JSON.stringify(skIds) === JSON.stringify([...ids].sort()),
      `skipped=${JSON.stringify(skIds)}`);
    chk(`缺 ${src} → 其餘 ${33 - ids.length} 張照常`, cards.length === 33 - ids.length, `${cards.length}`);
  }
  // totals 缺：flows-hdr-1 skipped，卡1/2 因 regime 未判定仍照發
  { const fx = FIX(); fx.totals = null;
    const { cards, skipped } = buildDailyCards(fx);
    chk("缺 totals → 只有 flows-hdr-1 skipped（卡1/2 視為未判定照發）",
      skipped.length === 1 && skipped[0].id === "flows-hdr-1" && cards.length === 32,
      JSON.stringify(skipped)); }
  // vix 缺：v2-global-1 照發、VIX 欄顯「—」
  { const fx = FIX(); fx.vix = null;
    const { cards, skipped } = buildDailyCards(fx);
    const g = cards.find((c) => c.id === "v2-global-1");
    chk("缺 vix → global-1 照發且顯「—」", skipped.length === 0 && g &&
      g.rows.some((r) => r.m === "VIX" && r.r === "—")); }
  // lastweek 缺：rank-1 照發（降級：無上週括號）
  { const fx = FIX(); fx.lastweek = null;
    const { cards, skipped } = buildDailyCards(fx);
    const r = cards.find((c) => c.id === "v2-rank-1");
    chk("缺 lastweek → rank-1 照發、無上週括號", skipped.length === 0 && r && !/上週 /.test(r.rows[0].r)); }
  // 全空 src：33 張全 skipped、不炸
  { const { cards, skipped } = buildDailyCards({});
    chk("全空 src → 0 卡、33 skipped、不拋例外", cards.length === 0 && skipped.length === 33); }
  // 舊 schema baseline（Phase A 前，8 欄）→ 卡3/4 skipped 並說明缺欄
  { const fx = FIX();
    for (const k of Object.keys(fx.baseline.stocks)) fx.baseline.stocks[k] = fx.baseline.stocks[k].slice(0, 8);
    for (const k of Object.keys(fx.baseline.subs_y)) fx.baseline.subs_y[k] = fx.baseline.subs_y[k].slice(0, 2);
    const { cards, skipped } = buildDailyCards(fx);
    const sk = Object.fromEntries(skipped.map((s) => [s.id, s.reason]));
    chk("舊 schema：卡3/4 skipped＋缺欄理由", /nh/.test(sk["sig-new-high"] || "") && /a20/.test(sk["sig-new-low"] || ""));
    chk("舊 schema：卡1 照發（R 全缺顯 R —）", cards.find((c) => c.id === "sig-sub-surge").rows[0].r === "R —"); }
}
// ---- 6. 產物能過渲染層（cardBubble／buildCardCarousels／cardsFallbackText）----
{
  const { cards } = buildDailyCards(FIX());
  let bubbleErr = null;
  for (const c of cards) { try { cardBubble(c); } catch (e) { bubbleErr = `${c.id}: ${e.message}`; break; } }
  chk("33 張逐卡過 cardBubble（含誠實原則守門）", bubbleErr === null, bubbleErr);
  let msgs = null, carErr = null;
  try { msgs = buildCardCarousels(cards, "股市雷達 2026-07-24 盤後圖卡"); } catch (e) { carErr = e.message; }
  chk("buildCardCarousels 整包不炸", carErr === null, carErr);
  chk("34 bubble（含免責）→ 3 carousel ≤5 message", msgs && msgs.length === 3, msgs && `${msgs.length}`);
  chk("每 carousel ≤12 bubble", msgs && msgs.every((m) => m.contents.contents.length <= 12));
  let fb = null, fbErr = null;
  try { fb = cardsFallbackText(cards, "2026-07-24"); } catch (e) { fbErr = e.message; }
  chk("純文字降級版可產出", fbErr === null && typeof fb === "string" && fb.length > 100, fbErr);
  // 上游 tone 含「貢獻最強」→ 組裝層已中性化，卡面不含禁用字
  const ov5 = cards.find((c) => c.id === "v2-ov-5");
  chk("tone 禁用字已中性化（最強→最大）", /貢獻最大/.test(ov5.paras[0]) && !/最強/.test(ov5.paras[0]));
}
// ---- 7. 純函式：同輸入兩次呼叫 byte-identical、不改動輸入 ----
{
  const fx = FIX();
  const snap = JSON.stringify(fx);
  const a = JSON.stringify(buildDailyCards(fx));
  const b = JSON.stringify(buildDailyCards(fx));
  chk("同輸入兩次輸出 byte-identical", a === b);
  chk("不竄改輸入物件", JSON.stringify(fx) === snap);
}

// ---- 7b. 流動性過濾：卡面鏡像回測母體（2026-07-29 首晚實推抓到的缺漏）----
// 冷門股 9997 全訊號命中（y1=1、it/fi≥2、nh=1、ints=89.4）但當日額 3 千萬 <1 億，
// 回測母體（LIQ=1e8）根本不含它——修正前它會以灌爆的 ints 佔據卡 3 榜首。
{
  const out = buildDailyCards(FIX());
  const byId = Object.fromEntries(out.cards.map((c) => [c.id, c]));
  for (const id of ["sig-new-high", "sig-dual-buy", "sig-surge-warn"]) {
    const card = byId[id];
    chk(`${id} 不含冷門股 9997`, card && !card.rows.some((r) => r.l === "9997"),
      card && card.rows.map((r) => r.l).join(","));
  }
  chk("卡3 流動股 2330 仍在", byId["sig-new-high"].rows.some((r) => r.l === "2330"));
  // flows 缺時退 a5 近似：卡3 仍擋 9997（a5=3e7）、留 2330（a5=5e10）
  const fx2 = FIX(); fx2.flowsDaily = null;
  const out2 = buildDailyCards(fx2);
  const nh2 = out2.cards.find((c) => c.id === "sig-new-high");
  chk("flows 缺→a5 後備仍擋 9997、留 2330",
    nh2 && !nh2.rows.some((r) => r.l === "9997") && nh2.rows.some((r) => r.l === "2330"),
    nh2 && nh2.rows.map((r) => r.l).join(","));
}

// ---- 8. 形狀壞的源不得全滅（B1 驗收 A2 缺口的回歸測試）----
// 「非 null 但形狀歪」：rows=42、buy_by_amt={}、dates=42——修正前這三種都會讓
// 共用 ctx 建構（fxNameMap/fxRegime）拋例外、33 張全滅。
{
  for (const [label, mut] of [
    ["lending.rows=42", (f) => { f.postmkt.lending.rows = 42; }],
    ["buy_by_amt={}", (f) => { f.flowsLatest.pages.foreign.buy_by_amt = {}; }],
    ["totals.dates=42", (f) => { f.totals.dates = 42; }],
  ]) {
    const fx = FIX(); mut(fx);
    let out = null, err = null;
    try { out = buildDailyCards(fx); } catch (e) { err = e.message; }
    chk(`${label} 不拋例外`, err === null, err);
    chk(`${label} 仍產出多數卡（>20 張）`, out && out.cards.length > 20,
      out && `cards=${out.cards.length}`);
  }
  // totals.dates 壞 → regime 降級為未判定（視為 bull 不抑制），卡 1 note 帶標註
  const fx = FIX(); fx.totals.dates = 42;
  const out = buildDailyCards(fx);
  const c1 = out.cards.find((c) => c.id === "sig-sub-surge");
  chk("totals 壞 → regime 降級未判定、卡1 照發且帶標註",
    c1 && /regime 未判定/.test([c1.note, c1.foot].join("")), c1 && (c1.note || c1.foot));
}

console.log(`dailycards: ${pass} passed, ${fail} failed`);
if (fail) process.exit(1);
