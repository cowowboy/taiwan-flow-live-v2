#!/usr/bin/env python3
# backtest/test_chain_overlap_smoke.py — 產業鏈重疊度檢定的離線煙霧/回歸測試
#
# 寫法比照 backtest/test_rrg_daily_axes_smoke.py：合成樣本 → 直接呼叫核心純函式 → 手算值斷言。
#
# ── 斷言原則（沿用 test_rrg_daily_axes_smoke.py 檔頭）──────────────────
#   **對邏輯斷言，不對產物斷言。** 每一項數值斷言都要滿足兩者之一：
#     (i) 期望值是手算常數（改壞公式必紅），或
#     (ii) 用一份獨立的暴力法／植入已知答案的合成樣本重算並比對（改壞公式必紅）。
#   「報告裡有某段字串」這類存在性檢查一律放最後一節、並標明**守門力弱**。
#
# ── 守的具體坑 ────────────────────────────────────────────────────────
#   1. **加權重疊必須是加權的**。報告第 1 節的整個立論是「檔數 Jaccard 看起來還好
#      （中位 0%），成交額加權才是有效重疊」。若 weighted_jaccard 被寫成只看有沒有交集，
#      這個立論就沒了。見 test_weighted_jaccard_hand_computed。
#   2. **Jacobi 特徵分解要真的分解對**。PC1 佔比是「重疊有沒有拉高共同因子」的唯一依據，
#      算錯會讓整個第 2.1 節在比較假數字。見 test_jacobi_eigen_reconstructs。
#   3. **residualize 要真的把因子扣掉、而且不能順手改掉平均**。扣掉平均等於改動每條鏈的
#      長期 alpha，殘差版就變成在測另一個東西。見 test_residualize_removes_planted_factor。
#   4. **group_day 的口徑要與 src/build_chain_daily.py 的 aggregate_day 一致**
#      （只計 twse、ETF 不進報酬與市值加權但進市場成交額、群組成交額用全額不均分、
#      市值權重 = sh × 前收）。口徑一偏，對照組就是在跑另一份東西，第 3 節的比較全失效。
#      見 test_group_day_matches_aggregate_day。
#   5. **互斥化必須真的互斥**。第 3 節「互斥切法一樣全滅」的前提是那兩個變體真的是分割。
#      見 test_exclusive_assignment_is_a_partition。
#   6. **橫斷面排序不可以有前視**。trailing_excess 若多吃一天，第 2.4 節會憑空生出動能。
#      見 test_trailing_excess_has_no_lookahead。
#   7. **xsec_spread 要抓得到植入的訊號、也要在訊號反轉時翻號**。若它恆回 0 或恆回正，
#      第 2.4／2.5 節的「全滅」與「符號一致」都是假的。見 test_xsec_spread_planted_signal。
#   8. **分塊 bootstrap 在自相關序列上必須比逐日版寬**。報告用「分塊那欄才算數」下判定；
#      若分塊沒有比較寬，那句話就是空的。見 test_block_boot_ci_is_wider_on_autocorrelated。
#   9. **顯著性計數規則要對**。第 2.4／3.2 節每一句結論都是計數推出來的，
#      ci_crosses_zero 判錯會讓「0 個顯著」變成「很多個顯著」。
#      見 test_ci_crosses_zero_and_counting。
#
# 用法：python3 backtest/test_chain_overlap_smoke.py（免 token、免網路、免快取）

from __future__ import annotations

import io
import math
import random
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_chain_overlap as ov   # noqa: E402

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


# ================================================================ 1 重疊度

def test_weighted_jaccard_hand_computed():
    """Σmin/Σmax。手算：wa={x:10,y:0}, wb={x:10,z:90} → min=10, max=10+0+90=100 → 0.1。"""
    wa = {"x": 10.0, "y": 0.0}
    wb = {"x": 10.0, "z": 90.0}
    check("加權 Jaccard 手算 = 0.10", close(ov.weighted_jaccard(wa, wb), 0.10))
    check("完全相同 → 1.0", close(ov.weighted_jaccard({"a": 3.0}, {"a": 3.0}), 1.0))
    check("完全不相交 → 0.0", close(ov.weighted_jaccard({"a": 3.0}, {"b": 3.0}), 0.0))
    check("空對空 → 0.0（不是 nan、不炸）", close(ov.weighted_jaccard({}, {}), 0.0))
    # 兩邊同乘一個常數，比值不變（單位換元/億元不影響結論）
    k = 7.3
    check("同乘常數不變（尺度不變）",
          close(ov.weighted_jaccard({a: v * k for a, v in wa.items()},
                                    {a: v * k for a, v in wb.items()}), 0.10, 1e-12))
    # 守門：加權版與檔數版必須在「共用成員佔成交額極大」的情形分道揚鑣
    big = {"共用": 999.0, "自有": 1.0}
    small = {"共用": 999.0, "別的": 1.0}
    jw = ov.weighted_jaccard(big, small)
    jc = ov.jaccard_count(set(big), set(small))
    check("共用成員吃掉幾乎全部成交額時，加權 Jaccard ≫ 檔數 Jaccard（0.998 vs 0.333）",
          jw > 0.99 and close(jc, 1 / 3) and jw - jc > 0.6)
    # 反向：共用成員微不足道時，加權 Jaccard ≪ 檔數 Jaccard
    a2 = {"共用": 1.0, "自有": 999.0}
    b2 = {"共用": 1.0, "別的": 999.0}
    check("共用成員微不足道時，加權 Jaccard ≪ 檔數 Jaccard",
          ov.weighted_jaccard(a2, b2) < 0.01 < ov.jaccard_count(set(a2), set(b2)))


def test_jaccard_count_hand_computed():
    check("檔數 Jaccard {1,2,3}∩{2,3,4} = 2/4", close(ov.jaccard_count({1, 2, 3}, {2, 3, 4}), 0.5))
    check("空集合 → 0.0", close(ov.jaccard_count(set(), set()), 0.0))


def test_overlap_coef_is_directional():
    """有向重疊：分母是自己。大鏈對小鏈、小鏈對大鏈必須不同。"""
    big = {"s": 10.0, "o": 90.0}
    small = {"s": 10.0}
    check("small→big = 100%", close(ov.overlap_coef(small, big), 1.0))
    check("big→small = 10%", close(ov.overlap_coef(big, small), 0.1))
    check("**不對稱**（寫成對稱就抓不到「小鏈被大鏈完全包含」）",
          ov.overlap_coef(small, big) != ov.overlap_coef(big, small))
    check("分母為 0 → 0.0", close(ov.overlap_coef({}, big), 0.0))


def test_shared_amt_frac():
    """只掛 1 條的成員不算共用；比例用**成交額**不是檔數。"""
    w = {"a": 90.0, "b": 10.0}
    counts = {"a": 1, "b": 3}
    check("共用成交額比例 = 10/100", close(ov.shared_amt_frac(w, counts), 0.1))
    check("全部只掛 1 條 → 0", close(ov.shared_amt_frac(w, {"a": 1, "b": 1}), 0.0))
    check("全部多掛 → 1", close(ov.shared_amt_frac(w, {"a": 2, "b": 2}), 1.0))
    check("**用成交額不是檔數**（檔數版會是 0.5，本函式是 0.1）",
          not close(ov.shared_amt_frac(w, counts), 0.5))


def test_chain_members_and_counts_filter_twse():
    cmap = {
        "1111": {"t": "twse", "c": ["A", "B", "A"]},      # 重複的 A 要去重
        "2222": {"t": "tpex", "c": ["A"]},                 # 非 twse → 整檔排除
        "3333": {"t": "twse", "c": []},                    # 沒有鏈 → 不進 counts
        "4444": {"t": "twse", "c": ["B"]},
    }
    mem = ov.chain_members(cmap)
    check("只計 twse（2222 被排除）", mem["A"] == {"1111"})
    check("鏈 B 兩檔", mem["B"] == {"1111", "4444"})
    cnt = ov.chain_count_by_code(cmap)
    check("重複鏈名去重後 1111 = 2 條", cnt["1111"] == 2)
    check("沒有鏈標籤的股票不進 counts", "3333" not in cnt)
    check("非 twse 不進 counts", "2222" not in cnt)


# ================================================================ 2 PCA / 殘差

def test_jacobi_eigen_reconstructs():
    """獨立驗證：A ≈ V diag(λ) Vᵀ、V 正交、λ 遞減、trace 守恆。"""
    rnd = random.Random(1234)
    n = 6
    B = [[rnd.uniform(-1, 1) for _ in range(n)] for _ in range(n)]
    A = [[sum(B[i][k] * B[j][k] for k in range(n)) for j in range(n)] for i in range(n)]
    vals, vecs = ov.jacobi_eigen(A)
    err = 0.0
    for i in range(n):
        for j in range(n):
            rec = sum(vals[m] * vecs[m][i] * vecs[m][j] for m in range(n))
            err = max(err, abs(rec - A[i][j]))
    check(f"A ≈ V diag(λ) Vᵀ（最大誤差 {err:.2e} < 1e-8）", err < 1e-8)
    orth = max(abs(sum(vecs[a][k] * vecs[b][k] for k in range(n)) - (1.0 if a == b else 0.0))
               for a in range(n) for b in range(n))
    check(f"特徵向量正交且單位長（最大偏差 {orth:.2e}）", orth < 1e-8)
    check("特徵值遞減", all(vals[i] >= vals[i + 1] - 1e-12 for i in range(n - 1)))
    check("trace 守恆 Σλ = ΣA_ii",
          close(sum(vals), sum(A[i][i] for i in range(n)), 1e-8))
    # 手算的 2×2：[[2,1],[1,2]] 的特徵值是 3 與 1
    v2, _ = ov.jacobi_eigen([[2.0, 1.0], [1.0, 2.0]])
    check("2×2 手算特徵值 = (3, 1)", close(v2[0], 3.0, 1e-10) and close(v2[1], 1.0, 1e-10))


def test_pca_explained_on_synthetic():
    """植入已知結構：單因子 → PC1 佔比極高；獨立雜訊 → PC1 接近 1/n 的量級。"""
    rnd = random.Random(99)
    T, n = 200, 8
    names = [f"g{i}" for i in range(n)]
    f = [rnd.gauss(0, 1) for _ in range(T)]
    one = {nm: [f[t] + rnd.gauss(0, 0.05) for t in range(T)] for nm in names}
    ind = {nm: [rnd.gauss(0, 1) for _ in range(T)] for nm in names}
    X1, _ = ov.center(one)
    X0, _ = ov.center(ind)
    e1 = ov.pca_explained(names, X1, True)
    e0 = ov.pca_explained(names, X0, True)
    check(f"單因子樣本 PC1 > 90%（實得 {e1[0] * 100:.1f}%）", e1[0] > 0.90)
    check(f"獨立雜訊 PC1 < 30%（實得 {e0[0] * 100:.1f}%，1/n = 12.5%）", e0[0] < 0.30)
    check("**兩者必須明顯不同**（PC1 若寫成常數，這條會紅）", e1[0] - e0[0] > 0.5)
    check("解釋比例加總 = 1", close(sum(e1), 1.0, 1e-8) and close(sum(e0), 1.0, 1e-8))


def test_residualize_removes_planted_factor():
    """扣掉 PC1 之後，殘差與植入因子的相關要趨近 0，變異要大幅下降，**平均要保留**。"""
    rnd = random.Random(7)
    T, n = 240, 6
    names = [f"g{i}" for i in range(n)]
    f = [rnd.gauss(0, 1) for _ in range(T)]
    drift = {nm: 0.001 * (i + 1) for i, nm in enumerate(names)}
    cols = {nm: [drift[nm] + f[t] + rnd.gauss(0, 0.10) for t in range(T)] for nm in names}
    res = ov.residualize(names, cols, 1)
    c_before = max(abs(ov_pearson(cols[nm], f)) for nm in names)
    c_after = max(abs(ov_pearson(res[nm], f)) for nm in names)
    check(f"扣 PC1 前與因子高度相關（{c_before:.3f} > 0.9）", c_before > 0.9)
    check(f"扣 PC1 後幾乎無關（{c_after:.3f} < 0.1）", c_after < 0.1)
    vr = ov.var_ratio(names, cols, res)
    check(f"殘差變異大幅下降（保留 {vr * 100:.1f}% < 10%）", vr < 0.10)
    mm = max(abs(st.mean(cols[nm]) - st.mean(res[nm])) for nm in names)
    check(f"**各序列平均保留不變**（最大偏差 {mm:.2e}）", mm < 1e-9)
    # 扣更多主成分，殘留變異必須更小（單調）
    check("扣 PC1~3 的殘留變異 < 扣 PC1",
          ov.var_ratio(names, cols, ov.residualize(names, cols, 3)) < vr)


def ov_pearson(a, b):
    """本檔自帶的獨立 Pearson（不呼叫受測模組，避免用被測物驗被測物）。"""
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    num = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    da = math.sqrt(sum((x - ma) ** 2 for x in a))
    db = math.sqrt(sum((x - mb) ** 2 for x in b))
    return num / (da * db) if da and db else 0.0


# ================================================================ 3 日聚合口徑

def _fixture():
    """手算用的小樣本。刻意涵蓋四種邊界：非 twse、ETF、無鏈標籤、跨兩條鏈。"""
    cmap = {
        "1111": {"t": "twse", "c": ["A", "B"], "sh": 100, "e": "X"},
        "2222": {"t": "twse", "c": ["A"], "sh": 200, "e": "X"},
        "3333": {"t": "tpex", "c": ["A"], "sh": 50, "e": "X"},     # 非 twse → 全排除
        "0050": {"t": "twse", "c": ["A"], "sh": 10, "e": "ETF"},   # ETF → 有額無報酬
        "4444": {"t": "twse", "c": [], "sh": 300, "e": "Y"},       # 無鏈 → 只進市場
    }
    prices = {
        "1111": [100.0, 11.0, 1.0],     # 前收 10 → r=+10%、w=100×10=1000
        "2222": [200.0, 21.0, 1.0],     # 前收 20 → r=+5%、 w=200×20=4000
        "3333": [999.0, 10.0, 0.0],
        "0050": [50.0, 100.0, 1.0],
        "4444": [400.0, 9.0, -1.0],     # 前收 10 → r=−10%、w=300×10=3000
        "_TAIEX": [1e12, 20000.0, 10.0],
    }
    return cmap, prices


def test_group_day_matches_aggregate_day():
    cmap, prices = _fixture()
    d = ov.group_day(prices, cmap, lambda c, i: sorted(set(i.get("c") or [])))
    m, g = d["market"], d["groups"]
    check("市場成交額 = 100+200+50+400 = 750（含 ETF、不含 tpex）", close(m["amt"], 750.0))
    check("市場 ret_ew = mean(.10, .05, −.10)", close(m["ret_ew"], (0.10 + 0.05 - 0.10) / 3))
    check("市場 ret_mw = (100+200−300)/8000 = 0", close(m["ret_mw"], 0.0))
    check("鏈 A 成交額 = 100+200+50 = 350（**全額**，不是均分的 50+200+50）",
          close(g["A"]["amt"], 350.0))
    check("鏈 A 檔數 = 3（ETF 計入檔數與成交額）", g["A"]["n"] == 3)
    check("鏈 A share = 350/750", close(g["A"]["share"], 350.0 / 750.0))
    check("鏈 A ret_ew = mean(.10, .05)（**ETF 不進報酬**）", close(g["A"]["ret_ew"], 0.075))
    check("鏈 A ret_mw = (0.1×1000+0.05×4000)/5000 = 0.06", close(g["A"]["ret_mw"], 0.06))
    check("鏈 B 只有 1111：成交額 100（**1111 對 A 與 B 各算全額 100，不是各 50**）",
          close(g["B"]["amt"], 100.0) and g["B"]["n"] == 1)
    check("鏈 B ret_ew = 0.10", close(g["B"]["ret_ew"], 0.10))
    check("非 twse 的 3333 完全沒有進任何一格",
          not close(g["A"]["amt"], 350.0 + 999.0) and not close(m["amt"], 1749.0))
    check("無鏈標籤的 4444 進市場但不進任何群組",
          close(m["amt"], 750.0) and all("4444" not in str(v) for v in g))


def test_group_day_respects_grouping_function():
    """換切法只該換分組，不該動市場統計——第 3 節的 apples-to-apples 靠這條。"""
    cmap, prices = _fixture()
    a = ov.group_day(prices, cmap, lambda c, i: sorted(set(i.get("c") or [])))
    b = ov.group_day(prices, cmap, lambda c, i: ([] if (i.get("e") in (None, "ETF")) else [i["e"]]))
    check("換切法後市場成交額不變", close(a["market"]["amt"], b["market"]["amt"]))
    check("換切法後市場 ret_ew 不變", close(a["market"]["ret_ew"], b["market"]["ret_ew"]))
    check("交易所產業別 X = 1111+2222（ETF 類別被排除）",
          close(b["groups"]["X"]["amt"], 300.0) and b["groups"]["X"]["n"] == 2)
    check("交易所產業別 Y = 4444（沒有鏈標籤也照樣有產業別）",
          close(b["groups"]["Y"]["amt"], 400.0))
    check("ETF 類別不成組", "ETF" not in b["groups"])


def test_columnar_shape():
    cmap, prices = _fixture()
    days = [ov.group_day(prices, cmap, lambda c, i: sorted(set(i.get("c") or []))) for _ in range(3)]
    col = ov.columnar(["d1", "d2", "d3"], days, ["A", "B", "Z"])
    check("形狀對得上 ax.axis_systems 的期待（chains/market/dates）",
          set(col) == {"dates", "market", "chains"} and set(col["market"]) == {"ret_ew", "ret_mw"})
    check("每條序列長度 = 天數", all(len(v) == 3 for v in col["chains"]["A"].values()))
    check("不存在的群組 Z → 全 None（不是 0，0 會被當成真的報酬）",
          col["chains"]["Z"]["ret_ew"] == [None, None, None])


# ================================================================ 4 互斥化

def test_exclusive_assignment_is_a_partition():
    cmap = {
        "1111": {"t": "twse", "c": ["大", "小"], "sh": 1},
        "2222": {"t": "twse", "c": ["大"], "sh": 1},
        "3333": {"t": "twse", "c": ["大"], "sh": 1},
        "4444": {"t": "twse", "c": ["小"], "sh": 1},
        "5555": {"t": "tpex", "c": ["大"], "sh": 1},
        "6666": {"t": "twse", "c": [], "sh": 1},
    }
    # 大鏈成交額 100+100+100=300、小鏈 100+1=101 → 1111 在小鏈的佔比 100/101 較高
    amt = {"1111": 100.0, "2222": 100.0, "3333": 100.0, "4444": 1.0}
    aw = ov.exclusive_by_weight(cmap, amt)
    check("佔比規則：1111 佔小鏈 99% > 佔大鏈 33% → 指派給「小」", aw["1111"] == "小")
    a_s = ov.exclusive_by_smallest(cmap)
    check("成員數規則：小鏈 2 檔 < 大鏈 4 檔 → 指派給「小」", a_s["1111"] == "小")
    for nm, a in (("佔比規則", aw), ("成員數規則", a_s)):
        check(f"{nm}：每檔只有一個群組（是分割不是覆蓋）",
              all(isinstance(v, str) for v in a.values()))
        check(f"{nm}：非 twse 與無鏈標籤者不在其中",
              "5555" not in a and "6666" not in a)
        check(f"{nm}：涵蓋所有有鏈標籤的 twse 股票",
              set(a) == {"1111", "2222", "3333", "4444"})
    # 反向樣本：把小鏈灌成巨鏈（4444 極大），1111 在小鏈的佔比就掉下來 → 改指「大」
    #   大鏈：10/(10+1+1) = 83%；小鏈：10/(10+1000) = 1%
    aw2 = ov.exclusive_by_weight(cmap, {"1111": 10.0, "2222": 1.0, "3333": 1.0, "4444": 1000.0})
    check("**規則真的在看資料**：改變成交額會改變指派（1111 → 大）", aw2["1111"] == "大")


# ================================================================ 5 前視／橫斷面／CI

def test_trailing_excess_has_no_lookahead():
    """score[t] 只能吃 t 以前（含 t）的報酬。多吃一天就會憑空生出動能。"""
    T = 12
    bench = [0.0] * T
    base = [0.01] * T
    rets = {"g": base[:]}
    sc = ov.trailing_excess(rets, bench, 3)
    check("暖機期 t<look → None", sc["g"][:3] == [None, None, None])
    exp = (1.01 ** 3) - 1.0
    check("t=5 的分數 = 過去 3 日複利超額（手算 1.01³−1）", close(sc["g"][5], exp, 1e-12))
    # 動 t+1 的報酬，score[t] 不可以變
    r2 = base[:]
    r2[6] = 9.99
    sc2 = ov.trailing_excess({"g": r2}, bench, 3)
    check("**改 t+1 的報酬，score[t] 不變（無前視）**", close(sc2["g"][5], exp, 1e-12))
    # 動 t 當日的報酬，score[t] 必須變（否則是差一格差錯邊）
    r3 = base[:]
    r3[5] = 0.5
    sc3 = ov.trailing_excess({"g": r3}, bench, 3)
    check("改 t 當日的報酬，score[t] **要**變（守 off-by-one）",
          not close(sc3["g"][5], exp, 1e-12))


def test_xsec_spread_planted_signal():
    """植入「分數＝未來報酬」的完美訊號 → 價差必須是大正數；訊號反轉 → 大負數。"""
    T, n = 60, 10
    bench = [0.0] * T
    names = [f"g{i}" for i in range(n)]
    rets = {nm: [0.001 * i] * T for i, nm in enumerate(names)}
    good = {nm: [float(i)] * T for i, nm in enumerate(names)}
    bad = {nm: [float(-i)] * T for i, nm in enumerate(names)}
    idx = list(range(5, T - 5))
    rg = ov.xsec_spread(good, rets, bench, idx, fwd=3)
    rb = ov.xsec_spread(bad, rets, bench, idx, fwd=3)
    # 前 20% = g9,g8（平均日報酬 0.0085）、後 20% = g0,g1（0.0005），3 日複利差
    exp = ((1.009 ** 3 - 1) + (1.008 ** 3 - 1)) / 2 - ((1.0 ** 3 - 1) + (1.001 ** 3 - 1)) / 2
    check(f"完美訊號的價差 = 手算 {exp * 100:+.3f}%（實得 {rg['avg']:+.3f}%）",
          close(rg["avg"], exp * 100, 1e-6))
    check("反轉訊號 → 價差恰為相反數（**不是恆正、也不是恆 0**）",
          close(rb["avg"], -rg["avg"], 1e-6))
    check("完美訊號每天都為正 → win = 100%", close(rg["win"], 100.0))
    check("有效日數 = idx 中前瞻算得出來的天數", rg["n"] == len(idx))
    # 分數全相同 → 排序退化成鏈名字典序，價差不該還是那個大正數
    flat = {nm: [1.0] * T for nm in names}
    rf = ov.xsec_spread(flat, rets, bench, idx, fwd=3)
    check("分數無資訊時價差 ≠ 完美訊號的價差", not close(rf["avg"], rg["avg"], 1e-6))


def test_block_boot_ci_is_wider_on_autocorrelated():
    """AR(1) 正自相關序列上，移動分塊 CI 必須比逐日 CI 寬——報告用分塊那欄下判定。"""
    rnd = random.Random(4242)
    x, v = [], 0.0
    for _ in range(400):
        v = 0.85 * v + rnd.gauss(0, 1)
        x.append(v + 0.3)
    ci_b = ov.block_boot_ci(x, 10, reps=400, seed=1)
    ci_i = ov.block_boot_ci(x, 1, reps=400, seed=1)
    wb, wi = ci_b[1] - ci_b[0], ci_i[1] - ci_i[0]
    check(f"分塊 CI 比逐日 CI 寬（{wb:.3f} > {wi:.3f}）", wb > wi)
    # 白噪音上兩者應該接近（分塊不是無條件放大）
    y = [rnd.gauss(0, 1) for _ in range(400)]
    yb = ov.block_boot_ci(y, 10, reps=400, seed=1)
    yi = ov.block_boot_ci(y, 1, reps=400, seed=1)
    ratio = (yb[1] - yb[0]) / (yi[1] - yi[0])
    check(f"白噪音上兩者接近（寬度比 {ratio:.2f} < 1.5，**證明不是無條件放大**）", ratio < 1.5)
    check("樣本 <20 → nan（不硬算）", math.isnan(ov.block_boot_ci([1.0] * 5, 3)[0]))


def test_ci_crosses_zero_and_counting():
    check("跨 0 → True", ov.ci_crosses_zero((-0.1, 0.2)))
    check("全正 → False", not ov.ci_crosses_zero((0.1, 0.2)))
    check("全負 → False", not ov.ci_crosses_zero((-0.3, -0.1)))
    check("剛好貼到 0 → 視為跨 0（保守）", ov.ci_crosses_zero((0.0, 0.2)))
    check("nan → 視為跨 0（不當成顯著）", ov.ci_crosses_zero((float("nan"), 0.2)))
    fake = {"c4": {"轉入改善": ({"n": 99}, (0.1, 0.3)),
                   "轉入領先": ({"n": 99}, (-0.1, 0.3)),
                   "對照組(無轉換)": ({"n": 99}, (0.5, 0.9))}}
    check("只數兩個訊號格、且只數**正向**（對照組不算）",
          ov.count_positive_signals(fake) == 1)
    neg = {"c4": {"轉入改善": ({"n": 99}, (-0.5, -0.1)),
                  "轉入領先": ({"n": 99}, (-0.5, -0.1)),
                  "對照組(無轉換)": ({"n": 99}, (0.5, 0.9))}}
    check("**負向顯著不算正向訊號**", ov.count_positive_signals(neg) == 0)
    none = {"c4": {"轉入改善": (None, (0.1, 0.3)),
                   "轉入領先": (None, (0.1, 0.3)),
                   "對照組(無轉換)": (None, (0.1, 0.3))}}
    check("樣本不足（stat=None）不算顯著", ov.count_positive_signals(none) == 0)


# ================================================================ 6 存在性檢查（守門力弱）
# ⚠ 這一節只擋得住整段被刪，擋不住數字算錯。引用本檔斷言總數當測試強度時請先扣掉。

REQUIRED = ["## 1. 成員重疊度的基本盤",
            "## 2. 關鍵檢定：重疊度是否解釋了輪動訊號的失效",
            "### 2.1 鏈的報酬有多少是共同因子",
            "### 2.2 扣掉共同因子後",
            "### 2.3 重疊度高的鏈對",
            "### 2.4 這批資料裡到底有沒有短天期輪動可抓",
            "### 2.5 若動能存在，RRG 的座標抓不抓得到",
            "## 3. 對照組：換一種切法會不會比較好",
            "## 4. 補救方案評估", "## 5. 結論", "## 6. 局限"]


def test_committed_report_sections():
    p = ROOT / "backtest" / "report_chain_overlap.md"
    check(f"{p.name} 已產出", p.exists())
    if not p.exists():
        return
    txt = p.read_text(encoding="utf-8")
    for sec in REQUIRED:
        check(f"報告含「{sec}」段", sec in txt)
    for kw in ("成交額加權 Jaccard", "置換虛無", "移動分塊", "管線平行度檢查",
               "交易所產業別", "2308", "PC1"):
        check(f"報告寫出關鍵概念「{kw}」", kw in txt)
    con = txt.split("## 5. 結論", 1)[-1]
    check("結論段給出明確判定（成立／不成立／證據不足）",
          any(k in con for k in ("假說成立", "假說不成立", "證據不足")))
    lim = txt.split("## 6. 局限", 1)[-1]
    check("局限段承認互斥化規則是自訂的", "自訂" in lim)
    check("局限段承認動能結果是樣本內、不可當策略", "樣本內" in lim)
    check("局限段承認 block 長度是本檔選的", "block=5" in lim)


def main():
    fns = [test_weighted_jaccard_hand_computed, test_jaccard_count_hand_computed,
           test_overlap_coef_is_directional, test_shared_amt_frac,
           test_chain_members_and_counts_filter_twse,
           test_jacobi_eigen_reconstructs, test_pca_explained_on_synthetic,
           test_residualize_removes_planted_factor,
           test_group_day_matches_aggregate_day, test_group_day_respects_grouping_function,
           test_columnar_shape, test_exclusive_assignment_is_a_partition,
           test_trailing_excess_has_no_lookahead, test_xsec_spread_planted_signal,
           test_block_boot_ci_is_wider_on_autocorrelated,
           test_ci_crosses_zero_and_counting,
           test_committed_report_sections]
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
