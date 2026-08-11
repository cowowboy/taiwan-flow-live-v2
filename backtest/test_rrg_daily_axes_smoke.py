#!/usr/bin/env python3
# backtest/test_rrg_daily_axes_smoke.py — 第二階段（軸定義比較）的離線煙霧/回歸測試
#
# 寫法比照 backtest/test_rrg_daily_smoke.py：合成樣本 → 直接呼叫核心純函式 → 手算值斷言。
#
# ── 本檔的斷言原則（2026-08-11 建檔時，因為本 session 已兩次踩到
#    「測試因為錯誤的理由而通過」而明文寫下）─────────────────────────
#   **對邏輯斷言，不對產物斷言。** 每一項數值斷言都必須滿足兩個條件之一：
#     (i) 期望值是手算出來的常數（改壞公式必紅），或
#     (ii) 用一份**獨立的暴力法**重算並比對（改壞公式必紅）。
#   「報告裡有某段字串」這類存在性檢查一律歸在最後一節、並在此標明**守門力弱**，
#   引用斷言數當測試強度時必須先扣掉那一節。
#
# ── 守的具體坑 ─────────────────────────────────────────────────────
#   1. **A-ew 與 A-mw 不是兩個方案**。資金版的軸只吃 share，加權欄位進不去。
#      報告把四格寫成三組就靠這條；哪天有人「順手」把報酬加進資金版的軸，
#      報告的第 1 節就整段失效。見 test_flow_axis_is_weighting_free。
#   2. **基準指數開頭的 None 不可以讓整條線變 None**。market.taiex_ret[0] 結構性為 null；
#      cum_index 若照「缺值就截斷」處理，價格版對 taiex 基準一天都算不出來
#      （第一版就是這樣，報告第 9 節整列印「有效日不足 0」）。
#      但**中間**的缺值必須截斷——那正是第一階段修掉的 12 天壞資料的形狀。
#      見 test_cum_index_leading_vs_interior_none。
#   3. **開頭當 0 之所以安全，靠的是 z-score 的尺度不變性**。
#      分母指數乘上任何常數，z 完全不變。這不是「大概沒差」，是恆等式。
#      見 test_zscore100_is_scale_invariant。
#   4. **z-score 的窗含當期、資金版的基準不含當期**。兩者差一格會讓整批數字位移。
#      見 test_roll_mean_prev_excludes_today、test_roll_mean_sd_includes_today。
#   5. **置換虛無必須抓得到退化指標**。準則 (b) 的整個立論是「把所有鏈丟進同一象限
#      也能拿 100% 穩定度，所以要扣虛無」。如果虛無在退化情形下**不等於** 1.0，
#      這條門檻就是假的。見 test_perm_null_catches_degenerate。
#   6. **平滑病理的守門欄要真的會動**。dwell（平均停留天數）若寫成常數，
#      第 3 節「不能取 Jaccard 最大值」的整段論證就沒有依據。
#      見 test_stability_flip_and_dwell。
#   7. **LOO 封閉解要對得起暴力法**。Δ = w(r_s−r_c)/(1−w) 是報告第 5 節的核心工具，
#      推導錯了整節的支配度數字全錯而且看不出來。見 test_loo_delta_matches_bruteforce。
#   8. **成員權重的口徑要與 src/build_chain_daily.py 的 aggregate_day 一致**
#      （只計 twse、c 去重、鏈成交額用全額不是均分、無 sh 者不進市值加權）。
#      口徑一偏，支配度就在量另一個東西。見 test_member_weights_matches_aggregate_day。
#   9. **前瞻超額要是複利、要對齊 T+1 起算**。差一格＝把訊號日自己的報酬算進去，
#      會憑空生出「訊號很準」的假象。見 test_fwd_excess_is_compound_and_starts_at_t1。
#  10. **bootstrap 必須按日期整批重抽**。逐 chain-day 重抽會低估標準誤數倍，
#      讓第 6 節每一格都「顯著」。見 test_boot_ci_blocks_by_date。
#
# 用法：python3 backtest/test_rrg_daily_axes_smoke.py（免 token、免網路、免快取）

from __future__ import annotations

import io
import math
import random
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_rrg_daily_axes as ax   # noqa: E402

FAILED = []


def check(label, cond):
    ok = bool(cond)
    print(f"{'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        FAILED.append(label)


def close(a, b, tol=1e-9):
    if a is None or b is None:
        return False
    return abs(a - b) <= tol


# ================================================================ 通用數學

def test_pctl_ranks_spearman():
    check("pctl 端點", close(ax.pctl([1, 2, 3, 4], 0), 1) and close(ax.pctl([1, 2, 3, 4], 100), 4))
    check("pctl 內插 P50=2.5", close(ax.pctl([1, 2, 3, 4], 50), 2.5))
    check("pctl 忽略 None", close(ax.pctl([1, None, 3], 50), 2.0))
    check("pctl 空 → nan", math.isnan(ax.pctl([], 50)))
    check("ranks 無 tie", ax.ranks([30.0, 10.0, 20.0]) == [3.0, 1.0, 2.0])
    check("ranks 有 tie 取平均（1.5, 1.5, 3）", ax.ranks([5.0, 5.0, 9.0]) == [1.5, 1.5, 3.0])
    # Spearman 對單調非線性完全不變（Pearson 會變）——這正是選它的理由
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    ys = [1.0, 8.0, 27.0, 64.0, 125.0]
    check("spearman 單調遞增 = +1", close(ax.spearman(xs, ys), 1.0, 1e-12))
    check("spearman 單調遞減 = −1", close(ax.spearman(xs, ys[::-1]), -1.0, 1e-12))
    check("pearson 對同一組資料 ≠ 1（證明 spearman 不是在算 pearson）",
          abs(ax.pearson(xs, ys) - 1.0) > 0.01)
    check("spearman 常數序列 → nan", math.isnan(ax.spearman([1, 1, 1], [1, 2, 3])))
    check("spearman 樣本 <3 → nan", math.isnan(ax.spearman([1, 2], [1, 2])))


def test_roll_mean_prev_excludes_today():
    """資金版的基準是「前 K 日」，**不含當日**（同 run_rrg.py 的 build_ratio）。"""
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    got = ax.roll_mean_prev(xs, 2)
    check("前 2 期平均、不含當期：[None,None,1.5,2.5,3.5]",
          got == [None, None, 1.5, 2.5, 3.5])
    check("含當期會是 [.,1.5,2.5,3.5,4.5]，本函式**不是**那個（差一格守門）",
          got[2] != 2.5)
    check("窗內有 None → 該格 None",
          ax.roll_mean_prev([1.0, None, 3.0, 4.0], 2) == [None, None, None, None])
    check("n 大於序列長度 → 全 None", ax.roll_mean_prev([1.0, 2.0], 5) == [None, None])


def test_roll_mean_sd_includes_today():
    """z-score 的窗**含當期**，且標準差是樣本標準差（ddof=1，不是母體）。"""
    xs = [2.0, 4.0, 6.0]
    mu, sd = ax.roll_mean_sd(xs, 3)
    check("含當期的 3 期平均 = 4", close(mu[2], 4.0))
    check("樣本標準差（ddof=1）= 2.0", close(sd[2], 2.0))
    check("**不是**母體標準差 1.63299", not close(sd[2], math.sqrt(8 / 3), 1e-6))
    check("暖機期為 None", mu[0] is None and mu[1] is None and sd[1] is None)
    m2, s2 = ax.roll_mean_sd([1.0, 1.0, 1.0], 3)
    check("常數序列 SD = 0（不是 None）", close(s2[2], 0.0))


def test_zscore100_hand_computed():
    xs = [2.0, 4.0, 6.0]
    z = ax.zscore100(xs, 3)
    check("z = 100 + (6−4)/2 = 101", close(z[2], 101.0))
    check("暖機期 None", z[0] is None and z[1] is None)
    check("SD=0 → None（不製造無限大座標）",
          ax.zscore100([5.0, 5.0, 5.0], 3)[2] is None)
    check("窗內含 None → None",
          ax.zscore100([1.0, None, 3.0], 3)[2] is None)


def test_zscore100_is_scale_invariant():
    """守的坑 3：分母指數乘上任何常數 c，z 完全不變（恆等式，不是近似）。

    這條恆等式就是 cum_index「開頭 None 當 0」之所以安全的全部依據。"""
    rnd = random.Random(7)
    xs = [100.0 + rnd.gauss(0, 5) for _ in range(30)]
    a = ax.zscore100(xs, 10)
    for c in (0.5, 3.7, 1000.0):
        b = ax.zscore100([x * c for x in xs], 10)
        check(f"RS 整條 ×{c} → z 逐格相同",
              all((p is None and q is None) or close(p, q, 1e-9) for p, q in zip(a, b)))
    # 平移也不變（z 對整個仿射變換 ax+b 都不變，比需要的還強）
    d = ax.zscore100([x + 50.0 for x in xs], 10)
    check("RS 整條 +50 → z 也不變（仿射不變）",
          all((p is None and q is None) or close(p, q, 1e-9) for p, q in zip(a, d)))
    # 反面：**隨時間變動**的乘子就不再不變——這正是「換一條大盤基準」的形狀。
    # 沒有這一條，上面全部的斷言可能只是「函式回傳常數」也會通過。
    e = ax.zscore100([x * (1.0 + i * 0.05) for i, x in enumerate(xs)], 10)
    check("RS 乘上**隨時間變動**的因子（＝換基準）→ z **會**改變",
          any(p is not None and q is not None and abs(p - q) > 1e-6 for p, q in zip(a, e)))


def test_cum_index_leading_vs_interior_none():
    """守的坑 2：開頭的 None 當 0（指數停在 base），中間的 None 必須截斷。"""
    check("正常連乘：[100, 110, 121]",
          all(close(a, b, 1e-9) for a, b in
              zip(ax.cum_index([0.1, 0.1, 0.0]), [100.0, 110.0, 121.0])))
    lead = ax.cum_index([None, 0.1, 0.1])
    check("開頭 None 不截斷：[100, 100, 110]",
          all(close(a, b, 1e-9) for a, b in zip(lead, [100.0, 100.0, 110.0])))
    check("開頭連續兩個 None 也不截斷",
          ax.cum_index([None, None, 0.1])[2] is not None)
    interior = ax.cum_index([0.1, None, 0.1])
    check("中間 None 截斷成 None（不憑空補 0）",
          interior[0] is not None and interior[1] is not None and interior[2] is None)
    check("中間 None 之後**全部**是 None",
          ax.cum_index([0.1, None, 0.1, 0.1])[2:] == [None, None])


def test_quad_and_boundary():
    check("領先 (R≥100, M≥100)", ax.quad(120.0, 120.0) == "領先")
    check("轉弱 (R≥100, M<100)", ax.quad(120.0, 80.0) == "轉弱")
    check("落後 (R<100, M<100)", ax.quad(80.0, 80.0) == "落後")
    check("改善 (R<100, M≥100)", ax.quad(80.0, 120.0) == "改善")
    check("邊界 100 歸在 ≥ 側（與 run_rrg.py 的 quad 同）",
          ax.quad(100.0, 100.0) == "領先")
    check("邊界 R=100, M=99.999 → 轉弱", ax.quad(100.0, 99.999) == "轉弱")


def test_jaccard():
    check("完全相同 = 1", close(ax.jaccard({1, 2}, {1, 2}), 1.0))
    check("完全不交 = 0", close(ax.jaccard({1, 2}, {3, 4}), 0.0))
    check("1/3", close(ax.jaccard({1, 2}, {2, 3}), 1 / 3))
    check("兩邊皆空 → None（不是 0，不能污染平均）", ax.jaccard(set(), set()) is None)
    check("一邊空 = 0", close(ax.jaccard({1}, set()), 0.0))


# ================================================================ 軸定義

def test_flow_coords_hand_computed():
    # share = [1,1,1,2,4]，K=2、L=1
    #  t=2: base=(1+1)/2=1     → R=100
    #  t=3: base=(1+1)/2=1     → R=200，M=100+(200−100)=200
    #  t=4: base=(1+2)/2=1.5   → R=100×4/1.5=266.667，M=100+(266.667−200)=166.667
    c = ax.flow_coords({"X": [1.0, 1.0, 1.0, 2.0, 4.0]}, 2, 1)["X"]
    check("暖機期無座標", c[0] is None and c[1] is None)
    check("t=2 沒有前一格 ratio（L=1 但 t−1 的 ratio 為 None）→ None", c[2] is None)
    check("t=3 座標 = (200, 200)", close(c[3][0], 200.0) and close(c[3][1], 200.0))
    check("t=4 座標 = (266.667, 166.667)",
          close(c[4][0], 800 / 3, 1e-9) and close(c[4][1], 100 + 800 / 3 - 200, 1e-9))
    check("base 為 0 → None（不除以 0）",
          ax.flow_coords({"X": [0.0, 0.0, 1.0]}, 2, 1)["X"][2] is None)


def test_flow_axis_is_weighting_free():
    """守的坑 1：報告第 1 節「四格→三組」的唯一依據。

    做法不是「呼叫兩次看看一不一樣」（那是恆真的），而是**把報酬欄位整個換掉**、
    確認座標一格都不動。"""
    s = {
        "chains": {
            "X": {"share": [0.5, 0.4, 0.3, 0.6, 0.5], "ret_ew": [0.01] * 5, "ret_mw": [0.01] * 5},
            "Y": {"share": [0.2, 0.3, 0.4, 0.1, 0.2], "ret_ew": [-0.01] * 5, "ret_mw": [-0.01] * 5},
        },
        "market": {"ret_ew": [0.0] * 5, "ret_mw": [0.0] * 5},
    }
    a1 = ax.axis_systems(s, 2, 1)["A"]
    # 把報酬全部換成天差地遠的值，share 一格不動
    s2 = {"chains": {n: dict(v) for n, v in s["chains"].items()},
          "market": {"ret_ew": [0.05] * 5, "ret_mw": [-0.09] * 5}}
    s2["chains"]["X"]["ret_ew"] = [0.30] * 5
    s2["chains"]["X"]["ret_mw"] = [-0.30] * 5
    s2["chains"]["Y"]["ret_ew"] = [-0.25] * 5
    s2["chains"]["Y"]["ret_mw"] = [0.25] * 5
    a2 = ax.axis_systems(s2, 2, 1)["A"]
    check("報酬欄位整組換掉，A 的座標逐格不變 → A-ew ≡ A-mw",
          all(a1[n] == a2[n] for n in a1))
    # 對照：同一份改動確實會讓 B 的座標改變（證明上面那條不是因為「什麼都沒接上」）
    b1 = ax.axis_systems(s, 2, 1)["B-ew"]
    b2 = ax.axis_systems(s2, 2, 1)["B-ew"]
    check("同一份改動確實會動到 B-ew（證明報酬欄位真的有接上）",
          any(b1[n] != b2[n] for n in b1))


def test_price_coords_definition():
    """正統 RRG：完全跟住大盤 → 相對強度是常數 → SD=0 → 沒有座標（不是 100）。"""
    n = 30
    same = {"X": [0.01] * n}
    cd = ax.price_coords(same, [0.01] * n, 5, 3)["X"]
    check("鏈與大盤逐日同步 → 全期無座標（RS 常數、SD=0）",
          all(c is None for c in cd))
    # 穩定跑贏 → RS 單調上升 → RS-Ratio 必 >100（z-score 對上升序列的最新一格為正）
    rnd = random.Random(11)
    up = {"X": [0.01 + 0.003 for _ in range(n)]}
    cd2 = ax.price_coords(up, [0.01] * n, 5, 3)["X"]
    last = [c for c in cd2 if c is not None][-1]
    check("穩定跑贏大盤 → RS-Ratio > 100", last[0] > 100.0)
    dn = {"X": [0.01 - 0.003 for _ in range(n)]}
    cd3 = ax.price_coords(dn, [0.01] * n, 5, 3)["X"]
    last3 = [c for c in cd3 if c is not None][-1]
    check("穩定落後大盤 → RS-Ratio < 100", last3[0] < 100.0)
    # 基準的開頭 None 不可以害死整組座標（＝ taiex_ret 的形狀）
    b = [None] + [0.01] * (n - 1)
    noisy = {"X": [0.01 + rnd.gauss(0, 0.01) for _ in range(n)]}
    cd4 = ax.price_coords(noisy, b, 5, 3)["X"]
    check("基準第 0 格為 None（taiex_ret 的形狀）仍算得出座標",
          any(c is not None for c in cd4))
    # 而且與「把第 0 格換成 0」逐格相同（尺度不變性的實地驗證）
    cd5 = ax.price_coords(noisy, [0.0] + [0.01] * (n - 1), 5, 3)["X"]
    check("基準第 0 格 None 與 0 給出逐格相同的座標",
          all((p is None and q is None) or
              (p is not None and q is not None and close(p[0], q[0], 1e-9)
               and close(p[1], q[1], 1e-9)) for p, q in zip(cd4, cd5)))


# ================================================================ 準則 1 穩定度

def mk_coords(pattern: dict) -> dict:
    """pattern: {chain: [象限字串, ...]} → 造出落在該象限的座標。"""
    pt = {"領先": (110.0, 110.0), "轉弱": (110.0, 90.0),
          "落後": (90.0, 90.0), "改善": (90.0, 110.0), None: None}
    return {n: [pt[q] for q in seq] for n, seq in pattern.items()}


def test_stability_flip_and_dwell():
    """守的坑 6：dwell 必須真的隨「指標動不動」變化。"""
    T = 10
    fixed = mk_coords({f"c{i}": ["領先"] * T for i in range(4)})
    s1 = ax.stability(fixed, list(range(T)))
    check("全期不動 → 象限 Jaccard = 1", close(s1["quad_all"], 1.0, 1e-12))
    check("全期不動 → 翻轉率 = 0", close(s1["flip"], 0.0, 1e-12))
    check("全期不動 → 停留天數 = inf", math.isinf(s1["dwell"]))
    alt = mk_coords({f"c{i}": (["領先", "落後"] * T)[:T] for i in range(4)})
    s2 = ax.stability(alt, list(range(T)))
    check("每天全部翻轉 → 翻轉率 = 1", close(s2["flip"], 1.0, 1e-12))
    check("每天全部翻轉 → 停留天數 = 1", close(s2["dwell"], 1.0, 1e-12))
    check("每天全部翻轉 → 象限 Jaccard = 0", close(s2["quad_all"], 0.0, 1e-12))
    # 一半的鏈固定、一半的鏈每天翻 → 翻轉率恰為 0.5、停留 2 天
    half = {}
    half.update({f"a{i}": ["領先"] * T for i in range(3)})
    half.update({f"b{i}": (["領先", "落後"] * T)[:T] for i in range(3)})
    s3 = ax.stability(mk_coords(half), list(range(T)))
    check("半數翻轉 → 翻轉率 = 0.5、停留 = 2 天",
          close(s3["flip"], 0.5, 1e-12) and close(s3["dwell"], 2.0, 1e-12))
    # 候補清單 = 改善 ∪ 領先；把某鏈從領先移到轉弱，它就該離開候補清單
    cand = mk_coords({"a": ["領先", "轉弱"], "b": ["改善", "改善"]})
    s4 = ax.stability(cand, [0, 1])
    check("候補清單 Jaccard：{a,b} vs {b} = 1/2", close(s4["cand"], 0.5, 1e-12))
    # 不相鄰的日期索引必須被跳過（序列有洞時不能拿來當「相鄰日」）
    s5 = ax.stability(alt, [0, 2, 4])
    check("非相鄰索引一律跳過 → 統計不出任何配對", s5["pairs"] == 0)


def test_improving_top_ordering():
    coords = {
        "a": [(90.0, 130.0)],   # 改善，M=130
        "b": [(90.0, 120.0)],   # 改善，M=120
        "c": [(90.0, 140.0)],   # 改善，M=140
        "d": [(110.0, 150.0)],  # 領先，不該入列
        "e": [(90.0, 80.0)],    # 落後，不該入列
    }
    check("改善象限按 RS-Mom 遞減：c, a, b",
          ax.improving_top(coords, 0) == ["c", "a", "b"])
    check("topn 生效", ax.improving_top(coords, 0, 2) == ["c", "a"])
    check("領先／落後不入列", "d" not in ax.improving_top(coords, 0)
          and "e" not in ax.improving_top(coords, 0))


def test_perm_null_catches_degenerate():
    """守的坑 5：準則 (b) 的整個立論。

    退化情形（每天所有鏈都在同一象限）真實穩定度是 100%，
    **置換虛無也必須是 100%**——否則「扣掉虛無」這條門檻擋不住退化指標。"""
    T = 12
    deg = mk_coords({f"c{i}": ["領先"] * T for i in range(8)})
    real = ax.stability(deg, list(range(T)))
    null = ax.perm_null(deg, list(range(T)), reps=20)
    check("退化指標的真實 Jaccard = 100%", close(real["quad_all"], 1.0, 1e-12))
    check("退化指標的**虛無**也是 100% → 扣完等於 0，門檻擋得住",
          close(null["quad_all"], 1.0, 1e-9))
    check("退化指標扣虛無後 = 0pp（不到 10pp 門檻）",
          abs(real["quad_all"] - null["quad_all"]) < 1e-9)
    # 對照：真的有結構的情形，真實值必須顯著高於虛無
    struct = mk_coords({f"a{i}": ["領先"] * T for i in range(4)}
                       | {f"b{i}": ["落後"] * T for i in range(4)})
    r2 = ax.stability(struct, list(range(T)))
    n2 = ax.perm_null(struct, list(range(T)), reps=20)
    check("有結構（鏈的身分穩定）→ 真實值遠高於虛無",
          r2["quad_all"] - n2["quad_all"] > 0.4)
    check("虛無有真的把標籤打散（虛無 < 1）", n2["quad_all"] < 0.9)


# ================================================================ 準則 2

def test_tier_of_and_flip_by_tier():
    check("≥3%", ax.tier_of(5.0) == "≥3%")
    check("1~3% 左閉右開", ax.tier_of(1.0) == "1~3%" and ax.tier_of(3.0) == "≥3%")
    check("<0.5%", ax.tier_of(0.1) == "<0.5%")
    # 大鏈固定象限、小鏈天天翻 → 翻轉率必須分別是 0% 與 100%
    T = 8
    coords = mk_coords({"big": ["領先"] * T, "small": (["領先", "落後"] * T)[:T]})
    shares = {"big": [0.10] * T, "small": [0.001] * T}
    pol = ax.tier_pollution(coords, shares, list(range(T)))
    check("大鏈層翻轉率 = 0%", close(pol["≥3%"]["flip"], 0.0, 1e-12))
    check("小鏈層翻轉率 = 100%", close(pol["<0.5%"]["flip"], 100.0, 1e-12))
    check("分層是按**當日** share（大鏈全部落在 ≥3% 層）", pol["≥3%"]["n"] == T)


def test_raw_noise_vs_size_direction():
    """小鏈比較吵 → Spearman 必須是負的；反過來造資料則必須是正的。"""
    rnd = random.Random(3)
    T = 60
    drv, shares = {}, {}
    for i in range(10):
        size = 10.0 ** (-i / 3.0)             # 規模逐條下降
        vol = 0.002 * (i + 1)                 # 噪音逐條上升（＝小鏈比較吵）
        v, cur = [], 1.0
        for _ in range(T):
            cur *= math.exp(rnd.gauss(0, vol))
            v.append(cur)
        drv[f"c{i}"] = v
        shares[f"c{i}"] = [size] * T
    rho, n, _med = ax.raw_noise_vs_size(drv, shares, list(range(T)))
    check(f"小鏈比較吵 → ρ 顯著為負（實得 {rho:+.3f}）", rho < -0.5)
    check("納入的鏈數正確", n == 10)
    # 反轉噪音的方向，ρ 必須翻正（證明它真的在量相關而不是回傳常數）
    inv = {f"c{i}": drv[f"c{9 - i}"] for i in range(10)}
    rho2, _n2, _m2 = ax.raw_noise_vs_size(inv, shares, list(range(T)))
    check(f"把噪音方向反轉 → ρ 翻正（實得 {rho2:+.3f}）", rho2 > 0.5)


# ================================================================ 準則 3 支配度

def sample_classify() -> dict:
    return {
        "1001": {"n": "多鏈股", "t": "twse", "c": ["X", "Y"], "sh": 1000},
        "1002": {"n": "單鏈股", "t": "twse", "c": ["X"], "sh": 2000},
        "2002": {"n": "上櫃股", "t": "tpex", "c": ["X"], "sh": 5000},
        "0050": {"n": "ETF", "t": "twse", "c": [], "sh": 9999},
        "9001": {"n": "無股本股", "t": "twse", "c": ["Y", "Y"], "sh": 0},
    }


def sample_prices() -> dict:
    return {
        "1001": [100e8, 110.0, 10.0],    # 前收 100 → r=+10%、市值權重 1000×100
        "1002": [50e8, 99.0, -1.0],      # 前收 100 → r=−1%、市值權重 2000×100
        "2002": [999e8, 10.0, 0.0],      # tpex：整檔不計
        "0050": [20e8, 50.0, 0.0],       # ETF：classify 無鏈 → 不進任何鏈
        "9001": [30e8, 20.0, 0.0],       # sh=0 → 有等權、無市值權重
        "_TAIEX": [900e10, 20000.0, 100.0],
    }


def test_member_weights_matches_aggregate_day():
    """守的坑 8：口徑必須與 src/build_chain_daily.py 的 aggregate_day 逐條一致。"""
    w = ax.member_weights(sample_prices(), sample_classify())
    check("只有 X、Y 兩條鏈（ETF 與無鏈者不生鏈）", set(w) == {"X", "Y"})
    check("tpex 的 2002 不進 X 鏈", "2002" not in w["X"])
    check("ETF 不進任何鏈", all("0050" not in d for d in w.values()))
    check("c 重複列出只算一次（Y 鏈剛好 2 個成員）", len(w["Y"]) == 2)
    # 成交額權重：鏈成交額用**全額**加總（不是跨鏈均分）→ X = 100/(100+50)
    check("X 鏈成交額權重 1001 = 2/3（全額口徑，非均分）",
          close(w["X"]["1001"][0], 2 / 3, 1e-12))
    check("Y 鏈成交額權重 1001 = 100/130", close(w["Y"]["1001"][0], 10 / 13, 1e-12))
    check("**不是**均分口徑（均分會讓 1001 在 X 只有 50/(50+50)=0.5）",
          not close(w["X"]["1001"][0], 0.5, 1e-6))
    # 等權：X 有 2 個有報酬的成員 → 各 0.5
    check("X 等權 = 0.5", close(w["X"]["1001"][1], 0.5, 1e-12))
    # 市值權重：X 的 tw = 1000×100 + 2000×100 → 1001 = 1/3
    check("X 市值權重 1001 = 1/3", close(w["X"]["1001"][2], 1 / 3, 1e-12))
    check("X 市值權重 1002 = 2/3", close(w["X"]["1002"][2], 2 / 3, 1e-12))
    check("sh=0 的 9001 無市值權重（None），但有等權",
          w["Y"]["9001"][2] is None and close(w["Y"]["9001"][1], 0.5, 1e-12))
    check("Y 鏈只有 1001 有市值權重 → 正規化後 = 1.0",
          close(w["Y"]["1001"][2], 1.0, 1e-12))
    check("報酬用 spread ÷ 前收（+10%），不是 close 比值",
          close(w["X"]["1001"][3], 0.10, 1e-12))
    check("除權息形狀（spread=0）→ 報酬 0 而非暴跌",
          close(w["Y"]["9001"][3], 0.0, 1e-12))


def test_loo_delta_matches_bruteforce():
    """守的坑 7：封閉解要對得起獨立寫的暴力法（把成員拿掉、重算加權平均）。"""
    rnd = random.Random(5)
    worst = 0.0
    for _ in range(300):
        k = rnd.randint(2, 8)
        ws = [rnd.random() + 0.05 for _ in range(k)]
        tot = sum(ws)
        ws = [x / tot for x in ws]
        rs = [rnd.gauss(0, 0.03) for _ in range(k)]
        rc = sum(a * b for a, b in zip(ws, rs))
        i = rnd.randrange(k)
        # 暴力法：把 i 拿掉、剩下的重新正規化後算加權平均
        rest = [(ws[j], rs[j]) for j in range(k) if j != i]
        s = sum(a for a, _b in rest)
        brute = rc - sum(a * b for a, b in rest) / s
        got = ax.loo_delta(ws[i], rs[i], rc)
        worst = max(worst, abs(got - brute))
    check(f"封閉解與暴力法逐次相符（最大差 {worst:.2e}）", worst < 1e-12)
    check("w=1（整條鏈只有它）→ None，不除以 0", ax.loo_delta(1.0, 0.1, 0.1) is None)
    check("w 為 None → None", ax.loo_delta(None, 0.1, 0.1) is None)
    check("報酬與鏈報酬相同 → Δ=0（拿掉不影響）",
          close(ax.loo_delta(0.3, 0.05, 0.05), 0.0, 1e-15))
    # 方向：權重愈大，同樣的偏離造成的 Δ 愈大（支配度的語意）
    check("權重愈大 → |Δ| 愈大",
          abs(ax.loo_delta(0.5, 0.10, 0.02)) > abs(ax.loo_delta(0.1, 0.10, 0.02)))


# ================================================================ 準則 4 延續性

def test_fwd_excess_is_compound_and_starts_at_t1():
    """守的坑 9：複利、且從 T+1 起算（不含訊號日自己的報酬）。"""
    r = [0.50, 0.10, 0.20, 0.0, 0.0]      # t=0 的 0.50 是「訊號日當天」，不該被算進去
    b = [0.00, 0.00, 0.00, 0.0, 0.0]
    check("T+1 = 10%", close(ax.fwd_excess(r, b, 0, 1), 0.10, 1e-12))
    check("T+2 是複利 1.1×1.2−1 = 32%（不是相加的 30%）",
          close(ax.fwd_excess(r, b, 0, 2), 0.32, 1e-12))
    check("**不是** 32% 以外的值——特別不是含訊號日的 0.5×1.1×1.2−1",
          not close(ax.fwd_excess(r, b, 0, 2), 0.5 * 1.1 * 1.2 - 1, 1e-6))
    b2 = [0.0, 0.05, 0.0, 0.0, 0.0]
    check("超額 = 鏈複利 − 大盤複利（10% − 5%）",
          close(ax.fwd_excess(r, b2, 0, 1), 0.05, 1e-12))
    check("視窗超出序列尾端 → None", ax.fwd_excess(r, b, 3, 5) is None)
    check("視窗內有 None → None", ax.fwd_excess([0.1, None, 0.1], [0.0] * 3, 0, 2) is None)


def test_transition_samples_and_stat():
    T = 12
    dates = [f"2026-01-{i+1:02d}" for i in range(T)]
    coords = mk_coords({"a": ["落後", "改善", "改善"] + ["領先"] * (T - 3)})
    rets = {"a": [0.0] + [0.01] * (T - 1)}
    bench = [0.0] * T
    rows = ax.transition_samples(coords, rets, bench, dates, list(range(T)))
    by = {r["d"]: r for r in rows}
    check("第 0 天沒有前一日 → 不生樣本", dates[0] not in by)
    check("落後→改善 那天標成 enter", by[dates[1]]["enter"] is True
          and by[dates[1]]["q"] == "改善")
    check("改善→改善 那天不算 enter", by[dates[2]]["enter"] is False)
    check("改善→領先 那天標成 enter", by[dates[3]]["enter"] is True
          and by[dates[3]]["q"] == "領先")
    check("前瞻視窗不足的尾端日不生樣本（T+5 要有 5 天 → 最後可用日是 index 6）",
          dates[6] in by and dates[7] not in by and dates[T - 1] not in by)
    check("黏性＝隔日仍在同象限：第 1 天（改善→改善）為真", by[dates[1]]["sticky"] is True)
    check("黏性：第 2 天（改善→領先）為假", by[dates[2]]["sticky"] is False)
    # stat 的樣本下限
    check(f"樣本 <{ax.MIN_ROWS} 回 None", ax.stat(rows[:3]) is None)
    fake = [{"e1": 0.01, "e3": 0.02, "e5": 0.03, "sticky": True} for _ in range(ax.MIN_ROWS)]
    v = ax.stat(fake)
    check("全正超額 → 勝大盤率 100%、平均 +2%",
          close(v["we3"], 100.0) and close(v["e3"], 2.0, 1e-12))
    check("中位數欄位獨立於平均", close(v["e3m"], 2.0, 1e-12))
    mixed = ([{"e1": 0.0, "e3": 0.10, "e5": 0.0, "sticky": False}] * 2 +
             [{"e1": 0.0, "e3": -0.01, "e5": 0.0, "sticky": False}] * 18)
    v2 = ax.stat(mixed)
    check("兩個大贏拉高平均但拉不動中位（avg>0、med<0）",
          v2["e3"] > 0 and v2["e3m"] < 0)


def test_boot_ci_blocks_by_date():
    """守的坑 10：必須按日期整批重抽。"""
    # 同一天內全部同號、跨日期正負各半 → 日期分塊的 CI 必須寬到跨 0
    rows = []
    for d in range(40):
        v = 0.02 if d % 2 == 0 else -0.02
        for _c in range(30):
            rows.append({"d": f"D{d}", "e3": v})
    lo, hi = ax.boot_ci(rows, "e3", reps=400)
    check(f"日期分塊 → CI 跨 0（實得 [{lo:+.3f}, {hi:+.3f}]）", lo < 0 < hi)
    # **這一條才是守實作的斷言**（前一版只比較了兩種「輸入」，改壞實作照樣綠）：
    # 本樣本的有效樣本數是 40 個日期，不是 1200 筆 → 均值標準誤 ≈ 2%/√40 = 0.316%
    #   → 95%CI 寬度應該 ≈ 4×0.316% ≈ 1.2 個百分點。
    # 若實作退回逐筆重抽，有效樣本數變成 1200，寬度會縮到 ≈ 4×2%/√1200 ≈ 0.23 pp。
    # 門檻取 0.8pp，落在兩者之間、離兩邊都夠遠。
    width = hi - lo
    check(f"CI 寬度 {width:.3f}pp > 0.8pp ← 只有真的按日期分塊才做得到"
          f"（逐筆重抽約 0.23pp）", width > 0.8)
    check(f"CI 寬度也不該過寬（<2pp），否則是分塊以外的地方壞了", width < 2.0)
    const = [{"d": f"D{i}", "e3": 0.01} for i in range(40)]
    lo3, hi3 = ax.boot_ci(const, "e3", reps=200)
    check("常數樣本 → CI 收斂到該常數", close(lo3, 1.0, 1e-9) and close(hi3, 1.0, 1e-9))
    check("日期數 <10 → nan（不給假 CI）",
          math.isnan(ax.boot_ci([{"d": "D1", "e3": 0.1}] * 5, "e3")[0]))


# ================================================================ 持續性 N

def test_persist_list():
    coords = mk_coords({
        "a": ["改善", "改善", "改善", "落後"],
        "b": ["落後", "改善", "改善", "改善"],
        "c": ["改善", "落後", "改善", "落後"],
    })
    l1 = ax.persist_list(coords, "改善", 1)
    check("N=1 就是原始象限成員", [sorted(x) for x in l1] ==
          [["a", "c"], ["a", "b"], ["a", "b", "c"], ["b"]])
    l2 = ax.persist_list(coords, "改善", 2)
    check("N=2：t=1 只有 a（b 前一天不在、c 當天不在）", l2[1] == {"a"})
    check("N=2：t=2 是 a 與 b", l2[2] == {"a", "b"})
    check("N=2：t=0 沒有前一天 → 空", l2[0] == set())
    l3 = ax.persist_list(coords, "改善", 3)
    # a 在 t=0..2 連 3 天 → t=2 入榜；b 在 t=1..3 連 3 天 → t=3 入榜；c 從未連 3 天
    check("N=3：t=2 是 a、t=3 是 b（c 從未連 3 天）",
          [sorted(x) for x in l3] == [[], [], ["a"], ["b"]])
    check("N 愈大名單愈小（子集關係）",
          all(l3[t] <= l2[t] <= l1[t] for t in range(4)))


# ================================================================ 已交付的報告
# ⚠ 這一節是**存在性檢查**，守門力弱（只擋得住整段被刪，擋不住數字算錯）。
#   引用本檔斷言總數當測試強度時，請先扣掉這一節。

REQUIRED = ["## 1. 四格為什麼變三組", "## 2. 準則 1：跨日穩定度",
            "## 3. 參數掃描", "## 4. 準則 2：小分母汙染",
            "## 5. 準則 3：單一個股支配度", "## 6. 準則 4：輪動訊號的延續性",
            "## 7. 持續性條件 N", "## 8. 早期（2025）vs 近期（2026）",
            "## 11. 建議方案", "## 12. 局限"]


def test_committed_report_sections():
    p = ROOT / "backtest" / "report_rrg_daily_axes.md"
    check(f"{p.name} 已產出", p.exists())
    if not p.exists():
        return
    txt = p.read_text(encoding="utf-8")
    for sec in REQUIRED:
        check(f"報告含「{sec}」段", sec in txt)
    for kw in ("置換虛無", "平滑病理", "肘點", "日期分塊", "多重比較",
               "z-score", "2308", "尺度"):
        check(f"報告寫出關鍵概念「{kw}」", kw in txt)
    rec = txt.split("## 11. 建議方案", 1)[-1]
    check("建議方案段給出具體參數（K/n=）", "K/n=" in rec)
    check("建議方案段給出持續性條件 N", "持續性條件 N=" in rec)
    lim = txt.split("## 12. 局限", 1)[-1]
    check("局限段承認價格版公式是公開重建", "重建" in lim)
    check("局限段承認沒有樣本外檢驗", "樣本外" in lim)
    check("局限段承認樣本不獨立", "不獨立" in lim)


def main():
    fns = [test_pctl_ranks_spearman, test_roll_mean_prev_excludes_today,
           test_roll_mean_sd_includes_today, test_zscore100_hand_computed,
           test_zscore100_is_scale_invariant, test_cum_index_leading_vs_interior_none,
           test_quad_and_boundary, test_jaccard,
           test_flow_coords_hand_computed, test_flow_axis_is_weighting_free,
           test_price_coords_definition,
           test_stability_flip_and_dwell, test_improving_top_ordering,
           test_perm_null_catches_degenerate,
           test_tier_of_and_flip_by_tier, test_raw_noise_vs_size_direction,
           test_member_weights_matches_aggregate_day, test_loo_delta_matches_bruteforce,
           test_fwd_excess_is_compound_and_starts_at_t1,
           test_transition_samples_and_stat, test_boot_ci_blocks_by_date,
           test_persist_list, test_committed_report_sections]
    for fn in fns:
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
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.exit(main())
