# backtest/test_alpha_sweep_smoke.py — run_alpha_sweep.py 的離線煙霧/回歸測試
#
# 為什麼需要這支：run_alpha_sweep.py 要有 backtest/cache/ 的真實快取才跑得動，
# 但快取不進 git，改壞了不會有任何東西擋。這支用合成市場＋合成樣本直接呼叫各函式，
# 驗「14 個訊號都產得出結果、切半/分層/K/日層級算對、樣本不足不崩、報告三層全列」
# ——**不驗數字的財務意義**（那要真實快取）。
#
# 指標數值本身的正確性不在這裡驗，由 backtest/test_alpha_parity.mjs（JS↔Python 零差異）守。
#
# 用法：python backtest/test_alpha_sweep_smoke.py（免 token、免網路、免快取）

from __future__ import annotations

import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_alpha_sweep as a   # noqa: E402
import run_sorting as rs      # noqa: E402

FAILED = []


def check(label, cond):
    ok = bool(cond)
    print(f"{'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        FAILED.append(label)


# ── 合成資料 ────────────────────────────────────────────────────
def _days(n, start=date(2025, 6, 16)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def synthetic_market(ncodes=40, ndays=140, seed=7):
    """造 price/inst 快取結構（隨機走勢，會踩到 KD 交叉/MACD 翻正/RSI 極值/布林觸軌）。"""
    rnd = random.Random(seed)
    days = _days(ndays)
    codes = [f"{1101 + i * 7}" for i in range(ncodes)]
    px = {c: 50 + rnd.random() * 500 for c in codes}
    tx = 20000.0
    price, inst = {}, {}
    for t, d in enumerate(days):
        tx *= 1 + (rnd.random() - 0.5) * 0.02
        row = {"_TAIEX": [9e12, tx, tx * 1.01, tx * 0.99, round(tx, 2)]}
        iv = {}
        for c in codes:
            px[c] = max(5.0, px[c] * (1 + (rnd.random() - 0.5) * 0.09))
            cl = round(px[c], 2)
            hi = round(cl * (1 + rnd.random() * 0.03), 2)
            lo = round(cl * (1 - rnd.random() * 0.03), 2)
            amt = 2e8 * (0.6 + rnd.random() * 6)          # 全部過 LIQ=1 億
            row[c] = [amt, cl, hi, lo, cl]
            iv[c] = [int((rnd.random() - 0.5) * 4e6), int((rnd.random() - 0.5) * 8e6)]
        price[d] = row
        inst[d] = iv
    return days, price, inst


def mk(n, days, *, e3, e1=0.0, first_ratio=0.5, seed=3):
    """造 n 筆合成樣本；欄位須與 build_stock_samples 的 27 個 key 一致。

    前 first_ratio 比例落在交易日前半段，其餘落在後半段（供切半測試）。
    """
    rnd = random.Random(seed)
    cut = a.split_index(days)
    rows = []
    for i in range(n):
        first = i < int(n * first_ratio)
        d = days[i % max(1, cut)] if first else days[cut + i % max(1, len(days) - cut)]
        val = e3(i) if callable(e3) else e3
        rows.append(dict(
            d=d, c=f"{1000 + i % 97}",
            surge=1.0, ret=0.0, pos=0.5, amt=3e8,
            r1=val, r2=val, r3=val, e1=e1, e2=val, e3=val,
            lim=False, limd=False, it3=0, fi3=0, ints=0.0,
            ma=True, bias=0.0, brk=False, brkd=False,
            volt=1.0, regime="bull", break_depth=None,
            trust_buy_amt=rnd.uniform(-500, 500), foreign_buy_amt=rnd.uniform(-500, 500),
            consec_exit=0,
        ))
    return rows


# ── 1. 候選清單與 K ─────────────────────────────────────────────
def test_candidate_list_frozen():
    ids = [s["id"] for s in a.SIGNALS]
    check(f"候選 14 列（實際 {len(a.SIGNALS)}）", len(a.SIGNALS) == 14)
    check("編號為 AS-01~AS-14 且不重複",
          ids == [f"AS-{i:02d}" for i in range(1, 15)])
    both = [s["id"] for s in a.SIGNALS if s["dir"] == "both"]
    check(f"雙邊檢定恰為 AS-03/AS-04（實際 {both}）", both == ["AS-03", "AS-04"])
    check("雙邊各計 2 次", all(s["weight"] == 2 for s in a.SIGNALS if s["dir"] == "both"))
    check("單邊各計 1 次", all(s["weight"] == 1 for s in a.SIGNALS if s["dir"] != "both"))
    check(f"K = 16（實際 {a.K_TESTS}）", a.K_TESTS == 16)
    check("K 由清單自動帶入（非硬編）",
          a.K_TESTS == sum(s["weight"] for s in a.SIGNALS))


def test_thresholds_match_preregistration():
    check("全期 N 門檻 500", a.MIN_N_FULL == 500)
    check("半段 N 門檻 200", a.MIN_N_HALF == 200)
    check("效果量門檻 0.50%", a.MIN_ABS_E3 == 0.50)
    check("AS-01~04 取當日前 30 名", a.TOP_N == 30)
    check("AS-05/06 取當日前後 10%", a.DECILE == 0.10)


def test_sample_keys_match_builder():
    """合成樣本欄位必須與真實 builder 一致，否則這支測試在驗假的東西。"""
    import inspect
    src = inspect.getsource(rs.build_stock_samples)
    synth = set(mk(1, _days(30), e3=0.0)[0])
    missing = [k for k in synth if f"{k}=" not in src]
    check(f"合成樣本欄位都存在於 build_stock_samples（缺 {missing}）", not missing)


# ── 2. 14 個訊號都產得出結果 ────────────────────────────────────
def test_all_14_signals_fire():
    days, price, inst = synthetic_market()
    samples, _, _ = rs.build_stock_samples(days, price, inst)
    check(f"合成市場有樣本（{len(samples)}）", len(samples) > 0)
    diag = a.attach_all(samples, days, price)
    fired = {}
    for sig in a.SIGNALS:
        rows = [r for r in samples if sig["id"] in r["sig"]]
        res = a.evaluate_signal(rows, days)
        fired[sig["id"]] = res["n"]
        check(f"{sig['id']} 產得出結果且 N>0（N={res['n']}）",
              isinstance(res, dict) and res["n"] > 0)
        check(f"{sig['id']} 有分層", a.classify_tier(res) in ("A", "B", "C"))
    check(f"排序欄診斷涵蓋 14 訊號（{len(diag)}）", len(diag) == 14)
    print(f"      觸發數：{fired}")

    # 名額上限：AS-01~04 每日至多 TOP_N 筆
    for sid in ("AS-01", "AS-02", "AS-03", "AS-04"):
        by_day = {}
        for r in samples:
            if sid in r["sig"]:
                by_day[r["d"]] = by_day.get(r["d"], 0) + 1
        worst = max(by_day.values()) if by_day else 0
        check(f"{sid} 單日不超過 {a.TOP_N} 名（實際最多 {worst}）", worst <= a.TOP_N)

    # AS-05/06 為互斥的頭尾十分位（同日同檔不可能同時是前 10% 與後 10%）
    both = [r for r in samples if "AS-05" in r["sig"] and "AS-06" in r["sig"]]
    check(f"AS-05 與 AS-06 不重疊（重疊 {len(both)} 筆）", not both)

    # 同步/對作四組互斥（同一 (股,日) 的法人符號組合只會落在一組）
    pairs = [("AS-01", "AS-02"), ("AS-01", "AS-03"), ("AS-03", "AS-04")]
    bad = [(x, y) for x, y in pairs
           if any(x in r["sig"] and y in r["sig"] for r in samples)]
    check(f"法人四組彼此互斥（重疊組 {bad}）", not bad)


def test_technical_signals_use_worker_semantics():
    """AS-07/08 必須帶 K<50 / K>50 條件，AS-13/14 走生產的 pb 判定。"""
    days, price, inst = synthetic_market(ncodes=25, ndays=120, seed=11)
    samples, _, _ = rs.build_stock_samples(days, price, inst)
    a.attach_all(samples, days, price)
    by_cd = {(r["c"], r["d"]): r for r in samples}
    bad_kd = bad_boll = 0
    for c in sorted({r["c"] for r in samples}):
        sd, H, L, C = [], [], [], []
        for d in days:
            row = price[d].get(c)
            if row:
                sd.append(d); H.append(row[2]); L.append(row[3]); C.append(row[4])
        kdp, bp = a.kd_path(H, L, C), a.boll_path(C)
        for i, d in enumerate(sd):
            r = by_cd.get((c, d))
            if not r:
                continue
            if kdp[i] and "AS-07" in r["sig"] and not kdp[i]["k"] < 50:
                bad_kd += 1
            if kdp[i] and "AS-08" in r["sig"] and not kdp[i]["k"] > 50:
                bad_kd += 1
            if bp[i] and "AS-13" in r["sig"] and not bp[i]["pb"] >= 1:
                bad_boll += 1
            if bp[i] and "AS-14" in r["sig"] and not bp[i]["pb"] <= 0:
                bad_boll += 1
    check(f"AS-07/08 皆滿足 K<50 / K>50（違反 {bad_kd}）", bad_kd == 0)
    check(f"AS-13/14 皆滿足 pb≥1 / pb≤0（違反 {bad_boll}）", bad_boll == 0)


# ── 3. 切半邏輯 ─────────────────────────────────────────────────
def test_split_half():
    days = _days(101)
    check("切點＝交易日數整除 2（奇數天）", a.split_index(days) == 50)
    check("切點＝交易日數整除 2（偶數天）", a.split_index(_days(100)) == 50)

    same = a.evaluate_signal(mk(600, days, e3=0.02), days)
    check(f"前後段各半（{same['h1']['n']}/{same['h2']['n']}）",
          same["h1"]["n"] == 300 and same["h2"]["n"] == 300)
    check("同號 → 切半同號為真", same["c_half_sign"])
    check("兩段各 N≥200 → 半段 N 判準過", same["c_half_n"])
    check("同號且 N 足 → 存活", same["survive_half"])

    cut = a.split_index(days)
    flip = mk(600, days, e3=lambda i: 0.02 if i < 300 else -0.02)
    resf = a.evaluate_signal(flip, days)
    check(f"前後段異號 → 切半同號為假（{resf['h1']['e3_avg']:+.2f}/"
          f"{resf['h2']['e3_avg']:+.2f}）", not resf["c_half_sign"])
    check("異號 → 不存活", not resf["survive_half"])

    thin = a.evaluate_signal(mk(300, days, e3=0.02), days)
    check(f"半段 N=150 <200 → 半段 N 判準不過（{thin['h1']['n']}）",
          not thin["c_half_n"] and thin["c_half_sign"])
    check("半段同號但 N 不足 → 不存活", not thin["survive_half"])

    # 切半必須依索引、不看日期字串內容
    rows = mk(400, days, e3=0.01)
    r_all = a.evaluate_signal(rows, days)
    check(f"切點日期由 days 帶出（{r_all['cut_day']} | {r_all['next_day']}）",
          r_all["cut_day"] == days[cut - 1] and r_all["next_day"] == days[cut])


# ── 4. 日層級序列 ───────────────────────────────────────────────
def test_daily_series():
    rows = [dict(d="2026-01-05", e3=0.02), dict(d="2026-01-05", e3=0.04),
            dict(d="2026-01-06", e3=-0.03),
            dict(d="2026-01-07", e3=0.01), dict(d="2026-01-07", e3=0.05)]
    ser = a.daily_series(rows)
    check(f"逐日序列長度＝訊號日數（{len(ser)}）", len(ser) == 3)
    check("日期升冪", [d for d, _ in ser] == ["2026-01-05", "2026-01-06", "2026-01-07"])
    check(f"當日先取平均（{ser[0][1]:.4f}）", abs(ser[0][1] - 0.03) < 1e-12)
    check("負值日照算", abs(ser[1][1] + 0.03) < 1e-12)

    days = _days(60)
    rows2 = ([dict(**r, e1=0.0, r1=0.0, r3=0.0, c="1") for r in
              [dict(d=days[0], e3=0.10), dict(d=days[1], e3=-0.01),
               dict(d=days[2], e3=-0.01), dict(d=days[3], e3=-0.01)]])
    res = a.evaluate_signal(rows2, days)
    check(f"日層級平均＝逐日平均的平均（{res['day_avg']:+.3f}%）",
          abs(res["day_avg"] - 1.75) < 1e-9)
    check(f"正報酬日佔比（{res['day_pos']:.1f}%）", abs(res["day_pos"] - 25.0) < 1e-9)
    check(f"pooled 平均同樣是 +1.75%（本例每日 1 筆）",
          abs(res["e3_avg"] - 1.75) < 1e-9)
    check("日層級與 pooled 同號 → 判準過", res["c_day_sign"])

    # 單日暴衝：pooled 被拉正，但逐日平均為負 → 第二層要擋得下來（§1.3 的用意）
    spike = ([dict(d=days[0], e3=1.0, e1=0.0, r1=0.0, r3=0.0, c="1")] * 50
             + [dict(d=days[i], e3=-0.03, e1=0.0, r1=0.0, r3=0.0, c="1")
                for i in range(1, 40)])
    rspike = a.evaluate_signal(spike, days)
    check(f"單日暴衝：pooled 正（{rspike['e3_avg']:+.2f}%）"
          f"但日層級負（{rspike['day_avg']:+.2f}%）",
          rspike["e3_avg"] > 0 and rspike["day_avg"] < 0)
    check("→ 日層級同號判準擋下", not rspike["c_day_sign"])


# ── 5. 三層分類 ─────────────────────────────────────────────────
def test_tier_classification():
    days = _days(120)
    a_res = a.evaluate_signal(mk(600, days, e3=0.02), days)
    check(f"A：全部門檻過（N={a_res['n']} e3={a_res['e3_avg']:+.2f}%）",
          a.classify_tier(a_res) == "A")

    # 前半 +3%、後半 −1% → pooled +1.0%（效果量過關）但切半不同號
    b_flip = a.evaluate_signal(mk(600, days, e3=lambda i: 0.03 if i < 300 else -0.01), days)
    check(f"B：效果量夠（{b_flip['e3_avg']:+.2f}%）但切半不同號",
          a.classify_tier(b_flip) == "B" and b_flip["c_eff"] and not b_flip["c_half_sign"])

    b_n = a.evaluate_signal(mk(400, days, e3=0.02), days)
    check("B：效果量夠但全期 N<500", a.classify_tier(b_n) == "B" and not b_n["c_n"])

    c_eff = a.evaluate_signal(mk(600, days, e3=0.001), days)
    check(f"C：未達效果量門檻（{c_eff['e3_avg']:+.2f}%）",
          a.classify_tier(c_eff) == "C")

    edge_lo = a.evaluate_signal(mk(600, days, e3=0.00499), days)
    edge_hi = a.evaluate_signal(mk(600, days, e3=0.005), days)
    check(f"門檻邊界：0.499% 未過（{edge_lo['e3_avg']:.3f}%）", not edge_lo["c_eff"])
    check(f"門檻邊界：0.500% 恰過（{edge_hi['e3_avg']:.3f}%）", edge_hi["c_eff"])

    # 做空訊號：e3 為負但絕對值夠 → 一樣可進 A（判準用 |e3|）
    short_res = a.evaluate_signal(mk(600, days, e3=-0.02), days)
    check("做空方向（e3 負）不因符號被判 C", a.classify_tier(short_res) == "A")
    short_sig = next(s for s in a.SIGNALS if s["dir"] == "short")
    long_sig = next(s for s in a.SIGNALS if s["dir"] == "long")
    both_sig = next(s for s in a.SIGNALS if s["dir"] == "both")
    check("方向標註：做空訊號 e3 負＝相符", a.dir_match(short_sig, short_res) is True)
    check("方向標註：做多訊號 e3 負＝相反", a.dir_match(long_sig, short_res) is False)
    check("方向標註：雙邊訊號不判方向", a.dir_match(both_sig, short_res) is None)


# ── 6. 樣本不足／邊界不崩 ───────────────────────────────────────
def test_no_crash_on_thin_data():
    days = _days(60)
    empty = a.evaluate_signal([], days)
    check("零樣本不崩，N=0", empty["n"] == 0)
    check("零樣本歸 C", a.classify_tier(empty) == "C")
    check("零樣本各判準皆 False",
          not (empty["c_n"] or empty["c_eff"] or empty["c_half_n"]
               or empty["c_half_sign"] or empty["c_day_sign"]))

    lines = []
    a.render_signal(a.SIGNALS[0], empty, {}, lines)
    check("零樣本的報告區塊印得出來", any("無樣本" in x for x in lines))

    one = a.evaluate_signal(mk(1, days, e3=0.02), days)
    check("單筆樣本不崩", one["n"] == 1 and one["day_n"] == 1)

    # 只有一段有樣本
    only_first = a.evaluate_signal(
        [dict(d=days[0], e3=0.02, e1=0.0, r1=0.0, r3=0.0, c="1")], days)
    check("只有前半段有樣本時 h2 為 None、不崩",
          only_first["h2"] is None and not only_first["c_half_sign"])

    # 極短快取：不足暖身期，attach_all 不得崩
    days2, price2, inst2 = synthetic_market(ncodes=12, ndays=30, seed=5)
    s2, _, _ = rs.build_stock_samples(days2, price2, inst2)
    diag2 = a.attach_all(s2, days2, price2)
    check(f"30 日快取仍跑得完（樣本 {len(s2)}）", isinstance(diag2, dict))
    check("30 日快取下 MACD 類訊號無誤觸發（暖身不足）",
          all("AS-09" not in r["sig"] and "AS-10" not in r["sig"] for r in s2))

    # 指標函式對過短序列一律回 None，不炸
    check("kd_path 短序列全 None", a.kd_path([1, 2], [1, 2], [1, 2]) == [None, None])
    check("macd_path 短序列全 None", set(a.macd_path([1.0] * 10)) == {None})
    check("rsi_path 短序列全 None", set(a.rsi_path([1.0] * 3, 5)) == {None})
    check("boll_path 短序列全 None", set(a.boll_path([1.0] * 5)) == {None})
    check("sma 不足回 None", a.sma([1, 2], 3) is None)


def test_jround_is_js_semantics():
    """JS Math.round 是 half-up 朝 +∞；Python 內建 round 是 banker's——不可混用。"""
    check("jround(0.5)=1", a.jround(0.5) == 1)
    check("jround(1.5)=2", a.jround(1.5) == 2)
    check("jround(2.5)=3（banker's 會給 2）", a.jround(2.5) == 3)
    check("jround(-0.5)=0（朝 +∞）", a.jround(-0.5) == 0)
    check("jround(-2.5)=-2", a.jround(-2.5) == -2)
    check("jround(0.49999999999999994)=0（不寫成 floor(x+0.5)）",
          a.jround(0.49999999999999994) == 0)
    check("r2 取兩位小數", a.r2(1.117) == 1.12 and a.r2(-1.117) == -1.12)
    check("r2(0.125)=0.13 / r2(-0.125)=-0.12（半數朝 +∞，非對稱）",
          a.r2(0.125) == 0.13 and a.r2(-0.125) == -0.12)


# ── 7. 報告：三層全列、14 項對得上、揭露段落齊全 ──────────────────
def _fake_results(days):
    res = {}
    for i, sig in enumerate(a.SIGNALS):
        if i % 3 == 0:
            rows = mk(600, days, e3=0.02, seed=i)                       # A
        elif i % 3 == 1:
            rows = mk(600, days, e3=lambda j: 0.02 if j < 300 else -0.02, seed=i)  # B
        else:
            rows = mk(600, days, e3=0.001, seed=i)                      # C
        r = a.evaluate_signal(rows, days)
        r["_rows"] = rows
        res[sig["id"]] = r
    return res


def test_report_structure():
    days = _days(120)
    _, price, inst = synthetic_market(ncodes=12, ndays=120, seed=9)
    samples, _, _ = rs.build_stock_samples(days[:120], price, inst)
    results = _fake_results(days)
    lines = a.build_report(days, samples, results, {}, "deadbeef 2026-07-27T16:57:01+00:00", False)
    txt = "\n".join(lines)

    for sig in a.SIGNALS:
        check(f"報告含 {sig['id']} 段", f"## {sig['id']}" in txt)
        check(f"報告含 {sig['id']} 的預註冊定義原文", sig["desc"] in txt)
    check("開頭印多重比較揭露（K=16、0.8 個）",
          "共檢定 16 個訊號" in txt and "約 0.8 個" in txt)
    check("開頭標明預註冊書 commit", "deadbeef" in txt)
    check("A 層標題", "## A. 存活" in txt)
    check("B 層標題", "## B. 半段活" in txt)
    check("C 層標題（必須完整列出）", "## C. 陰性" in txt)
    for sig in a.SIGNALS:
        check(f"{sig['id']} 出現在三層總表", f"| {sig['id']} |" in txt)
    check("印出 4 項已知偏差", all(b.split("：")[0][2:6] in txt for b in a.KNOWN_BIASES))
    check("已知偏差確為 4 項", len(a.KNOWN_BIASES) == 4)
    check("印出對照組基準", "對照組" in txt)
    check("印出切半方式（依索引）", "依交易日索引對半切" in txt)
    check("印出 e1 只作描述用途", "不列入採用判準" in txt)
    check("印出不報 p 值的理由", "不報 p 值" in txt)
    check("印出判準門檻表", "≥ 500" in txt and "0.50%" in txt and "≥ 200" in txt)
    check("每個訊號都印分層", txt.count("**分層**") == 14)
    check("工作區乾淨時不印憑據警告", "未 commit 的修改" not in txt)

    dirty = "\n".join(a.build_report(days, samples, results, {}, "deadbeef", True))
    check("預註冊書有未 commit 修改時報告會警示", "未 commit 的修改" in dirty)


def main():
    for fn in [test_candidate_list_frozen, test_thresholds_match_preregistration,
               test_sample_keys_match_builder,
               test_all_14_signals_fire, test_technical_signals_use_worker_semantics,
               test_split_half, test_daily_series, test_tier_classification,
               test_no_crash_on_thin_data, test_jround_is_js_semantics,
               test_report_structure]:
        print(f"\n--- {fn.__name__} ---")
        fn()
    print(f"\n{'=' * 50}")
    if FAILED:
        print(f"{len(FAILED)} 項失敗：")
        for f in FAILED:
            print(f"  - {f}")
        return 1
    print("全部通過")
    return 0


if __name__ == "__main__":
    sys.exit(main())
