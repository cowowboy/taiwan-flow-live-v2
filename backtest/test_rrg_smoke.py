#!/usr/bin/env python3
# backtest/test_rrg_smoke.py — run_rrg.py 的離線煙霧/回歸測試
#
# 為什麼需要這支：run_rrg.py 要有 data/intraday/*.json 才跑得動，跑一次 25 秒；
# 這支改用合成樣本直接呼叫核心計算函式，驗「公式對不對、降級分支走不走得通、
# 報告必要段落在不在」——**不驗真實資料的數字**（那要跑 run_rrg.py 本人）。
#
# 守的具體 bug（2026-08-09 建檔時抓到）：
#   data/intraday/2026-07-18.json 的 total 只有 1/54 格有值（殘檔），
#   原本用「any(total 有值)」當守門會把它當成有效交易日收進來，
#   結果基準期整整少一天真實樣本、報告首行印成「14 個交易日」。
#   現在改用時點覆蓋率 MIN_COVER=0.9。見 test_load_days_drops_sparse_day。
#
#   zscore_pool() 原本用 `mad <= 0` 判斷離差退化。但「離差全為 0」在浮點下不是 0：
#   RS_Ratio 走 (100*share)/base，兩個相同的 frame 差 1 ULP 就讓 MAD 落在 1e-14，
#   `mad > 0` 成立、z = (v-m)/1e-14 噴到 1e16 直接誤標。離線資料數值連續沒撞到，
#   但盤中一定會撞：Worker /replay 的「缺格往前回退 ≤5 分鐘」會讓相鄰兩個請求時點
#   拿到同一格 frame。現在改用 MAD_EPS=1e-9。見 test_zscore_pool_survives_float_dust。
#
#   traj_stats() 有同型問題：`net <= 0` / `tnet > 0` 拿淨位移當比值分母，軌跡繞一圈
#   回到起點時淨位移在浮點下是 1e-14 而非 0，比值噴到天文數字並汙染「比值中位」的樣本。
#   後果比 z 那個輕（只動報告統計量、不產出誤標名單、traj_stats 不上線），但同型要同型守。
#   現在改用 NET_EPS=1e-9。見 test_traj_stats_survives_float_dust。
#
# 用法：python backtest/test_rrg_smoke.py（免 token、免網路、免快取）

from __future__ import annotations

import json
import math
import statistics as stats
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_rrg as rr   # noqa: E402

FAILED = []


def check(label, cond):
    ok = bool(cond)
    print(f"{'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        FAILED.append(label)


def close(a, b, tol=1e-9):
    return abs(a - b) <= tol


# ---------------------------------------------------------------- 小工具

def test_pctl():
    check("pctl 端點", close(rr.pctl([1, 2, 3, 4], 0), 1) and close(rr.pctl([1, 2, 3, 4], 100), 4))
    check("pctl 內插 P50=2.5", close(rr.pctl([1, 2, 3, 4], 50), 2.5))
    check("pctl 空清單回 nan", math.isnan(rr.pctl([], 50)))


def test_mad():
    # median=3；離差 [2,1,0,1,2] → median=1
    check("mad([1,2,3,4,5])==1", close(rr.mad([1, 2, 3, 4, 5]), 1.0))
    check("mad 全同值為 0", close(rr.mad([7, 7, 7]), 0.0))
    check("mad 空清單為 0", close(rr.mad([]), 0.0))


def test_quad_boundaries():
    check("(100,100) 算領先（邊界含等於）", rr.quad(100.0, 100.0) == "領先")
    check("(100,99) 算轉弱", rr.quad(100.0, 99.9) == "轉弱")
    check("(99,99) 算落後", rr.quad(99.9, 99.9) == "落後")
    check("(99,100) 算改善", rr.quad(99.9, 100.0) == "改善")


def test_tier_of():
    check("3.0% 落在 ≥3% 層", rr.tier_of(3.0) == "≥3%")
    check("2.99% 落在 1~3% 層", rr.tier_of(2.99) == "1~3%")
    check("0.5% 落在 0.5~1% 層", rr.tier_of(0.5) == "0.5~1%")
    check("0.49% 落在 <0.5% 層", rr.tier_of(0.49) == "<0.5%")
    check("0% 落在 <0.5% 層", rr.tier_of(0.0) == "<0.5%")


# ---------------------------------------------------------------- 座標公式

def test_build_ratio_same_time_vs_flat():
    """同時點基準取「前 K 日同一時點」；終盤基準（對照組）取「前 K 日最後一格」。
    刻意讓兩者在 t=0 給出不同答案，確保沒有互相抄。"""
    dates = ["d1", "d2", "d3"]
    names = ["A"]
    shares = {
        "d1": {"A": [0.10, 0.10, 0.20]},
        "d2": {"A": [0.20, 0.20, 0.40]},
        "d3": {"A": [0.30, 0.30, 0.30]},
    }
    same = rr.build_ratio(dates, names, shares, 2, "same_time")
    flat = rr.build_ratio(dates, names, shares, 2, "flat")
    check("同時點基準只算得出 d3（前 2 日當基準）", list(same) == ["d3"])
    # base = (0.10+0.20)/2 = 0.15 → 100*0.30/0.15 = 200
    check("同時點 t=0 → 200.0", close(same["d3"]["A"][0], 200.0))
    # base = (0.20+0.40)/2 = 0.30 → 100*0.30/0.30 = 100
    check("終盤基準 t=0 → 100.0（與同時點不同）", close(flat["d3"]["A"][0], 100.0))
    check("終盤基準全日同一個 base",
          close(flat["d3"]["A"][0], 100.0) and close(flat["d3"]["A"][2], 100.0))


def test_build_ratio_guards():
    dates = ["d1", "d2"]
    names = ["A", "B"]
    shares = {
        "d1": {"A": [0.0, 0.10], "B": [None, 0.10]},
        "d2": {"A": [0.10, None], "B": [0.10, 0.10]},
    }
    same = rr.build_ratio(dates, names, shares, 1, "same_time")
    check("基準為 0 → 座標 None", same["d2"]["A"][0] is None)
    check("當日缺值 → 座標 None", same["d2"]["A"][1] is None)
    check("基準日缺值 → 座標 None", same["d2"]["B"][0] is None)


def test_mom_from_ratio():
    r = [100.0, 110.0, 130.0, None, 140.0]
    m = rr.mom_from_ratio(r, 1)
    check("t<L 無動能", m[0] is None)
    check("mom = 100 + r(t) - r(t-L)", close(m[1], 110.0) and close(m[2], 120.0))
    check("回看點缺值 → None", m[4] is None)
    check("自身缺值 → None", m[3] is None)


def test_sample_idx():
    idx = rr.sample_idx(10, 3, 2)
    check("取樣錨在最後一格", idx[-1] == 9)
    check("取樣點遞增", idx == sorted(idx))
    check("起點不早於 L", min(idx) >= 2)
    check("間隔等於 step", all(b - a == 3 for a, b in zip(idx, idx[1:])))
    check("step=1 時每格都取", rr.sample_idx(5, 1, 0) == [0, 1, 2, 3, 4])


# ---------------------------------------------------------------- 判定 1／2

def mk_coords(pts_by_name):
    return {"d1": dict(pts_by_name)}


def test_traj_stats_straight_beats_zigzag():
    straight = [(100.0 + i, 100.0 + i) for i in range(4)]
    zig = [(100.0, 100.0), (110.0, 100.0), (100.0, 100.0), (110.0, 100.0)]
    idx = [0, 1, 2, 3]
    tiers = {("d1", "S"): "≥3%", ("d1", "Z"): "≥3%"}
    js, *_ = rr.traj_stats(mk_coords({"S": straight}), ["S"], idx, tiers)
    jz, *_ = rr.traj_stats(mk_coords({"Z": zig}), ["Z"], idx, tiers)
    check("直線軌跡比值 = 1/3", close(js[0], 1 / 3))
    check("鋸齒軌跡比值 = 1.0", close(jz[0], 1.0))
    check("直線比鋸齒平滑（比值較低）", js[0] < jz[0])


def test_traj_stats_skips_incomplete_and_zero_net():
    idx = [0, 1, 2]
    tiers = {("d1", "X"): "≥3%", ("d1", "Y"): "≥3%"}
    holed = {"X": [(100.0, 100.0), None, (102.0, 102.0)]}
    ja, *_ = rr.traj_stats(mk_coords(holed), ["X"], idx, tiers)
    check("軌跡有缺口 → 整條跳過", ja == [])
    loop = {"Y": [(100.0, 100.0), (110.0, 100.0), (100.0, 100.0)]}
    jb, _, _, sm, nm, _ = rr.traj_stats(mk_coords(loop), ["Y"], idx, tiers)
    check("淨位移為 0 → 不列入比值（避免除以 0）", jb == [] and len(sm) == 1 and close(nm[0], 0.0))


def test_traj_stats_survives_float_dust():
    """守浮點退化，與 test_zscore_pool_survives_float_dust 同型（分母是浮點殘渣）。

    traj_stats 的「比值中位」拿淨位移 math.dist(pts[0], pts[-1]) 當分母。軌跡繞一圈
    回到起點時，淨位移在浮點下不會剛好是 0——座標走 (100*share)/base，首尾差 1 ULP
    就讓 dist 落在 1e-14 量級而非 0。舊的 `net > 0` 判斷放行後，分子是真實的步長中位、
    分母是浮點殘渣，比值噴到天文數字並汙染樣本。後果比 z 那個輕（只動報告的統計量、
    不產出誤標名單、traj_stats 也不上線），但同型問題應該同型守法。
    """
    # 首尾差 1 ULP：淨位移是「幾乎但不完全等於 0」，不是剛好 0
    end = math.nextafter(100.0, math.inf)
    idx = list(range(6))                      # 6 點 → 尾巴版（末 WIN=6 點）也吃得到同一條
    loop = {"DUST": [(100.0, 100.0), (110.0, 100.0), (110.0, 110.0),
                     (100.0, 110.0), (105.0, 100.0), (end, 100.0)]}
    tiers = {("d1", "DUST"): "≥3%"}           # 非小基數 → 同時檢查 jit_big 分支

    raw_net = math.dist((100.0, 100.0), (end, 100.0))
    check("合成軌跡的淨位移是浮點殘渣（>0 但 ≤ NET_EPS）",
          0.0 < raw_net <= rr.NET_EPS)
    step_med = stats.median([math.dist(a, b)
                             for a, b in zip(loop["DUST"], loop["DUST"][1:])])
    would_be = step_med / raw_net
    check(f"舊的 `net > 0` 門檻會給出比值≈{would_be:.1e}（天文數字＝汙染比值中位）",
          would_be > 1e12)

    ja, jbig, jtail, sm, nm, ef = rr.traj_stats(mk_coords(loop), ["DUST"], idx, tiers)
    check("全日版：浮點殘渣淨位移不列入比值", ja == [])
    check("非小基數版：同樣不列入", jbig == [])
    check("尾巴版：`tnet > 0` 同型守門，也不列入", jtail == [])
    check("路徑效率不列入（同一個分母）", ef == [])
    # 描述性統計（步長中位／淨位移中位）不受守門影響——它們不做除法，照樣要記錄
    check("步長中位仍照記（不做除法，不受守門影響）",
          len(sm) == 1 and close(sm[0], step_med))
    check("淨位移中位仍照記為浮點殘渣本身", len(nm) == 1 and close(nm[0], raw_net))


def test_quad_stats():
    idx = [0, 1, 2]
    tiers = {("d1", "A"): "≥3%", ("d1", "B"): "<0.5%"}
    stay = {"A": [(110.0, 110.0)] * 3, "B": [(110.0, 110.0)] * 3}
    sa, sb, churn, size = rr.quad_stats(mk_coords(stay), ["A", "B"], idx, tiers)
    check("全程同象限 → 不變比例 100%", close(sa, 1.0))
    check("領先象限平均成員數 = 2", close(size["領先"], 2.0))
    check("成員數無變動", close(churn["領先"], 0.0))
    flip = {"A": [(110.0, 110.0), (90.0, 90.0), (110.0, 110.0)],
            "B": [(110.0, 110.0)] * 3}
    sa2, sb2, churn2, _ = rr.quad_stats(mk_coords(flip), ["A", "B"], idx, tiers)
    check("一半配對換象限 → 不變比例 50%", close(sa2, 0.5))
    check("≥0.5% 子集只算 A → 不變比例 0%", close(sb2, 0.0))
    check("領先成員數逐格變動 = 1", close(churn2["領先"], 1.0))


# ---------------------------------------------------------------- 判定 4

def build_pool_case():
    """造 2 個族群、20 個時點：JUMP 在最後一個取樣點暴衝且 share 也真的大變；
    TINY 位移同樣暴衝但 share 幾乎沒動（＝小基數假訊號）。"""
    T, L, step = 20, 2, 2
    ratios = {"d1": {}}
    ratios["d1"]["JUMP"] = [100.0 + (t % 3) for t in range(T - 1)] + [300.0]
    ratios["d1"]["TINY"] = [100.0 + (t % 3) for t in range(T - 1)] + [300.0]
    coords = rr.build_coords(ratios, ["JUMP", "TINY"], L)
    shares = {"d1": {
        "JUMP": [0.01] * (T - 1) + [0.02],       # Δ = 1.00 個百分點
        "TINY": [0.0001] * (T - 1) + [0.000102],  # Δ = 0.0002 個百分點
    }}
    idx = rr.sample_idx(T, step, L)
    disp = rr.disp_series(coords, shares, ["JUMP", "TINY"], idx, L)
    return coords, shares, idx, disp, L


def test_disp_series_carries_dpp():
    coords, shares, idx, disp, L = build_pool_case()
    seq = disp[("d1", "JUMP")]
    check("disp_series 每筆是 (j, t, RS位移, Δpp)", len(seq[0]) == 4)
    check("最後一筆 Δpp = 1.00 個百分點", close(seq[-1][3], 1.0, 1e-9))
    check("Δpp 用絕對值", all(x[3] >= 0 for x in seq))
    check("TINY 的 Δpp 遠小於 JUMP",
          disp[("d1", "TINY")][-1][3] < seq[-1][3] / 100)


def test_zscore_pool_flags_the_jump():
    coords, shares, idx, disp, L = build_pool_case()
    pool = rr.zscore_pool(disp, coords, "win")
    last_j = max(j for _, j in pool)
    zs = {n: z for n, z, v, q, dpp in pool[("d1", last_j)]}
    check("暴衝點的 z 遠超過 2.0", zs.get("JUMP", 0) > 2.0)
    early = [z for _, j in [(0, j) for j in range(last_j)] for n, z, *_ in pool.get(("d1", j), [])]
    check("暴衝前沒有任何點超過 z=2.0", all(z < 2.0 for z in early) if early else True)


def test_zscore_pool_win_needs_min_win():
    coords, shares, idx, disp, L = build_pool_case()
    win = rr.zscore_pool(disp, coords, "win")
    day = rr.zscore_pool(disp, coords, "day")
    first_disp_j = min(x[0] for x in disp[("d1", "JUMP")])
    check("day 版從第一筆位移就給 z", min(j for _, j in day) == first_disp_j)
    check(f"win 版要先累積 {rr.MIN_WIN} 筆位移才給 z",
          min(j for _, j in win) == first_disp_j + rr.MIN_WIN - 1)
    check("win 版的可評時點是 day 版的子集",
          set(win).issubset(set(day)) and len(win) < len(day))


def test_zscore_pool_skips_zero_mad():
    """位移完全一致（MAD=0）的族群不能進 pool，否則會除以 0。"""
    T, L = 12, 2
    ratios = {"d1": {"FLAT": [100.0 + t for t in range(T)]}}   # 每格位移固定
    coords = rr.build_coords(ratios, ["FLAT"], L)
    shares = {"d1": {"FLAT": [0.01] * T}}
    idx = rr.sample_idx(T, 2, L)
    disp = rr.disp_series(coords, shares, ["FLAT"], idx, L)
    check("等速位移的 disp 全相同", len({round(x[2], 9) for x in disp[("d1", "FLAT")]}) == 1)
    check("MAD=0 → day 版整組跳過", rr.zscore_pool(disp, coords, "day") == {})
    check("MAD=0 → win 版整組跳過", rr.zscore_pool(disp, coords, "win") == {})


def test_zscore_pool_survives_float_dust():
    """守浮點誤標（前端 2026-08-09 實測的造紙鏈 z=4.4e16）。

    盤中觸發路徑：Worker `/replay` 的「缺格往前回退 ≤5 分鐘」會讓相鄰兩個請求時點
    拿到**同一格 frame**，位移因此「幾乎但不完全等於 0」——RS_Ratio 走
    (100*share)/base，差 1 ULP 就讓離差落在 1e-14 而非 0。舊的 `mad > 0` 判斷放行後，
    分母是浮點殘渣、分子是真實位移，z 直接噴到天文數字並無條件誤標。
    離線資料數值連續所以撞不到，這支用合成序列把它釘住。"""
    T = 8
    flat = [(100.0, 100.0)] * T
    coords = {"d1": {"PAPER": flat, "STEEL": flat}}

    # 前 7 格是回退拿到同一格 frame（位移≈0，只有浮點殘渣），第 8 格才真的動了
    dust = [0.0, 1.4e-14, 0.0, 7.1e-15, 1.4e-14, 0.0, 7.1e-15, 21.3]
    disp = {("d1", "PAPER"): [(j, j, v, 0.5) for j, v in enumerate(dust)]}

    raw_mad = rr.mad(dust)
    check("合成序列的 MAD 是浮點殘渣（>0 但 ≤ MAD_EPS）",
          0.0 < raw_mad <= rr.MAD_EPS)
    would_be_z = (dust[-1] - stats.median(dust)) / raw_mad
    check(f"舊的 `mad > 0` 門檻會給出 z≈{would_be_z:.1e}（天文數字＝無條件誤標）",
          would_be_z > 1e12)

    for variant in ("day", "win"):
        pool = rr.zscore_pool(disp, coords, variant)
        check(f"{variant} 版：浮點殘渣序列整組被跳過", pool == {})
        zs = [z for rows in pool.values() for _, z, *_ in rows]
        check(f"{variant} 版：不產生任何極端 z", all(abs(z) < 10 for z in zs))

    tiers = {("d1", "PAPER"): "0.5~1%"}
    for variant in ("day", "win"):
        avg, *_ = rr.flag_stats(rr.zscore_pool(disp, coords, variant), tiers, rr.REC_Z, 0.0)
        check(f"{variant} 版：z={rr.REC_Z} 下標記數為 0（不誤標）", close(avg, 0.0))

    # 反向對照：有真實雜訊的族群不可被這道門檻誤殺（前端實測的鋼鐵鏈）
    noisy = [1.0, 1.4, 0.9, 1.6, 1.1, 1.0, 1.2, 12.0]
    disp_n = {("d1", "STEEL"): [(j, j, v, 0.5) for j, v in enumerate(noisy)]}
    check("正常雜訊序列的 MAD 遠大於 MAD_EPS", rr.mad(noisy) > rr.MAD_EPS)
    for variant in ("day", "win"):
        pool_n = rr.zscore_pool(disp_n, coords, variant)
        zmax = max(z for rows in pool_n.values() for _, z, *_ in rows)
        check(f"{variant} 版：正常雜訊仍算得出 z、暴衝點仍被標（zmax={zmax:.1f}）",
              pool_n != {} and zmax >= rr.REC_Z)


def test_flag_stats_floor_kills_small_base():
    """這是報告「建議參數」最關鍵的一條：z 之外必須再疊絕對變化量下限，
    否則 share 幾乎沒動的小基數族群會跟真正的大額輪動並列。"""
    coords, shares, idx, disp, L = build_pool_case()
    pool = rr.zscore_pool(disp, coords, "win")
    tiers = {("d1", "JUMP"): "1~3%", ("d1", "TINY"): "<0.5%"}
    a0, big0, rate0, comp0, imp0 = rr.flag_stats(pool, tiers, 2.0, 0.0)
    a1, big1, rate1, comp1, imp1 = rr.flag_stats(pool, tiers, 2.0, rr.REC_F)
    check("F=0 時兩個族群都被標", a0 > a1 and a0 > 0)
    check("F=REC_F 時只剩 Δpp 夠大的那個", close(comp1["<0.5%"], 0.0))
    check("F=REC_F 後被標的都是非小基數", close(big1, a1))
    check("被標比率是「該層被標數 ÷ 該層評估數」，不是名單組成",
          rate0["<0.5%"] > 0 and close(rate1["<0.5%"], 0.0))
    a2, *_ = rr.flag_stats(pool, tiers, 999.0, 0.0)
    check("z 門檻拉到 999 → 名單清空", close(a2, 0.0))


def test_topn_stats_ranks_by_raw_disp():
    coords, shares, idx, disp, L = build_pool_case()
    pool = rr.zscore_pool(disp, coords, "win")
    tiers = {("d1", "JUMP"): "1~3%", ("d1", "TINY"): "<0.5%"}
    comp, imp = rr.topn_stats(pool, tiers, 2)
    check("TopN 取滿兩個族群時組成各半",
          close(comp["1~3%"], 50.0) and close(comp["<0.5%"], 50.0))
    check("改善象限比例是 0~100 的百分比", 0.0 <= imp <= 100.0)


# ---------------------------------------------------------------- 資料守門

def mk_day_json(date, cover, T=54):
    times = [f"{9 + (i * 5) // 60:02d}:{(5 + i * 5) % 60:02d}" for i in range(T)]
    total = [(i + 1) * 1e11 if i < int(T * cover) else None for i in range(T)]
    g = {"某次產業": [(i + 1) * 1e8 if total[i] else None for i in range(T)]}
    return {"date": date, "unit": "元", "times": times, "total": total,
            "frames": [{"t": t, "stale": 0} for t in times],
            "nstk": [1] * T, "g": g}


def test_load_days_drops_sparse_day():
    """守 2026-07-18 殘檔：total 僅 1/54 格有值，不得被當成有效交易日。"""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        (p / "2026-07-18.json").write_text(
            json.dumps(mk_day_json("2026-07-18", 1 / 54)), encoding="utf-8")
        (p / "2026-07-20.json").write_text(
            json.dumps(mk_day_json("2026-07-20", 1.0)), encoding="utf-8")
        orig = rr.INTRADAY
        try:
            rr.INTRADAY = p
            days, dropped = rr.load_days()
        finally:
            rr.INTRADAY = orig
    check("殘檔被剔除", [d["date"] for d in days] == ["2026-07-20"])
    check("剔除清單有記錄理由", dropped and dropped[0][0] == "2026-07-18")
    check("剔除門檻確實是覆蓋率而非 any()", rr.MIN_COVER > 0.5)


def test_build_shares_handles_none_total():
    days = [mk_day_json("2026-07-20", 0.5)]
    dates, times, names, shares = rr.build_shares(days)
    check("build_shares 取得族群名", names == ["某次產業"])
    check("total 缺值處 share 為 None", shares["2026-07-20"]["某次產業"][-1] is None)
    check("total 有值處 share 算得出",
          close(shares["2026-07-20"]["某次產業"][0], 1e8 / 1e11))
    check("end_share_pct 取最後一個有效值",
          rr.end_share_pct(shares, "2026-07-20", "某次產業") > 0)


# ---------------------------------------------------------------- 建議參數自洽

def test_recommended_params_self_consistent():
    """報告主張「L 必須是 step 的整數倍且 ≥2 倍」（否則 t-L 不落在取樣格上，
    前端得多打 /replay）。建議值自己要先滿足這條。"""
    check("REC_L 是 REC_STEP 的整數倍", rr.REC_L % rr.REC_STEP == 0)
    check("REC_L / REC_STEP ≥ 2", rr.REC_L // rr.REC_STEP >= 2)
    check("REC_Z 在掃描過的門檻集合內", rr.REC_Z in rr.ZS)
    check("REC_K 在掃描過的基準期集合內", rr.REC_K in rr.KS)
    check("REC_F 在掃描過的下限集合內", rr.REC_F in rr.FLOORS)
    check("MIN_WIN < WIN（視窗版才有意義）", rr.MIN_WIN < rr.WIN)


# ---------------------------------------------------------------- 報告段落

REQUIRED_SECTIONS = ["## 建議參數", "## 局限"]


def test_committed_report_sections():
    p = rr.OUT
    check(f"{p.name} 已產出", p.exists())
    if not p.exists():
        return
    txt = p.read_text(encoding="utf-8")
    for sec in REQUIRED_SECTIONS:
        check(f"報告含「{sec}」段", sec in txt)
    for k in ("RS_Ratio_i(d,t)", "RS_Mom_i(d,t)", "base_i(d,t)"):
        check(f"報告寫出 {k} 公式", k in txt)
    for kw in ("次產業", "產業鏈", "交易日"):
        check(f"局限段提到「{kw}」", kw in txt.split("## 局限", 1)[-1])
    body = txt.split("## 建議參數", 1)[-1].split("## 局限", 1)[0]
    for kw in (f"K = {rr.REC_K}", f"{rr.REC_L*5} 分", f"{rr.REC_STEP*5} 分",
               f"z 門檻 = {rr.REC_Z}"):
        check(f"建議參數段給出具體值「{kw}」", kw in body)


def main():
    for fn in [test_pctl, test_mad, test_quad_boundaries, test_tier_of,
               test_build_ratio_same_time_vs_flat, test_build_ratio_guards,
               test_mom_from_ratio, test_sample_idx,
               test_traj_stats_straight_beats_zigzag,
               test_traj_stats_skips_incomplete_and_zero_net,
               test_traj_stats_survives_float_dust,
               test_quad_stats,
               test_disp_series_carries_dpp, test_zscore_pool_flags_the_jump,
               test_zscore_pool_win_needs_min_win, test_zscore_pool_skips_zero_mad,
               test_zscore_pool_survives_float_dust,
               test_flag_stats_floor_kills_small_base, test_topn_stats_ranks_by_raw_disp,
               test_load_days_drops_sparse_day, test_build_shares_handles_none_total,
               test_recommended_params_self_consistent,
               test_committed_report_sections]:
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
