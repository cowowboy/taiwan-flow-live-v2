#!/usr/bin/env python3
# backtest/run_rrg.py — 盤中 RRG（相對輪動圖）兩軸座標定義的離線驗證
#
# 【訊號定義】對族群 i、交易日 d、日內時點 t（09:05~13:30 每 5 分共 54 格）：
#   share_i(d,t)    = 該族群累計成交額(t) / 全市場累計成交額(t)
#   base_i(d,t)     = 前 K 個交易日「同一時點 t」的 share 平均
#                     （同時點基準；用來中和日內成交量的時間形狀：早盤大、午盤小）
#   RS_Ratio_i(d,t) = 100 * share_i(d,t) / base_i(d,t)
#   RS_Mom_i(d,t)   = 100 + (RS_Ratio_i(d,t) - RS_Ratio_i(d,t-L))
#   象限：領先(R≥100,M≥100)／轉弱(R≥100,M<100)／落後(R<100,M<100)／改善(R<100,M≥100)
#   掃描參數：K∈{3,5,10} 基準交易日數、L∈{2,3,6} 動能回看時點數（10/15/30 分）、
#             繪圖取樣 step∈{1,2,3} 時點（5/10/15 分）
#
# 【判定】五項，全部在**次產業層**（data/intraday 的 g 矩陣）：
#   1 軌跡訊噪比：相鄰取樣點位移中位數 ÷ 淨位移（全日版與「尾巴末 N 點」版），越低越平滑
#   2 象限穩定度：相鄰取樣點象限不變比例；各象限成員數的逐格變動
#   3 小基數汙染：依終盤 share 分 4 層，各層 RS_Ratio 的中位／P95／>150／>200 比例；
#                 另加「日內時間形狀」檢定＝RS_Ratio 中位數隨時點的漂移
#   4 有意義位移：z_i(t) = (近 L 期位移 − 該族群自身位移中位數) / 自身位移 MAD，
#                 掃 z∈{1.0,1.5,2.0,2.5}，看每時點被標數量、**各層被標比率**、改善象限比例
#   5 輔助排序：用「RS 位移」排序 vs 用「share 絕對變化量（百分點）」排序的名單差異
#
# 【對照組】
#   - 基準定義對照：同時點基準 vs **終盤基準**（前 K 日收盤 share 當常數，不分時點）——
#     用來驗證「同時點基準能中和日內時間形狀」這個核心設計主張。
#   - 參數對照：27 組 (K,L,step) 彼此互為對照。
#   - 位移門檻對照：z 自身標準化名單 vs 直接取「絕對位移 Top-N」名單（同樣張數）。
#   - 分層對照：終盤 share ≥3% 大族群 vs <0.5% 小基數族群；被標比率 vs 母體佔比。
#   - 跨層對照：次產業層結論 vs 產業鏈層（classify.json 映射）——驗結論可移轉性。
#
# 【用法】python backtest/run_rrg.py → 印表 ＋ 寫 backtest/report_rrg.md
#         離線、免 token、免網路；只讀 data/intraday/*.json 與 data/classify.json。

from __future__ import annotations

import io
import json
import math
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
INTRADAY = DATA / "intraday"
OUT = ROOT / "backtest" / "report_rrg.md"

KS = [3, 5, 10]           # 基準交易日數
LS = [2, 3, 6]            # 動能回看時點數（×5 分）
STEPS = [1, 2, 3]         # 繪圖取樣間隔（×5 分）
ZS = [1.0, 1.5, 2.0, 2.5]
FLOORS = [0.0, 0.01, 0.02, 0.05, 0.10, 0.20]   # 絕對變化量下限掃描（share 百分點）
WIN = 6                   # 前端可行的自身分布視窗（＝規格 §4 的 N=6 個軌跡點）
MIN_WIN = 4               # 視窗版最少要幾筆位移才算 z
# 前端為了餵飽 z 視窗，必須在 WIN 個軌跡點之外「再往前」多取的座標點數
# （＝ index.html 的 OV_RRG_ZEXT）。位移 d(t) 要回看 L//step 個取樣格，故 N 個座標點
# 只生得出 N-L//step 筆位移：ZEXT=0 → 3 筆 < MIN_WIN，z 永遠算不出來、位移異常靜默失效；
# ZEXT=3 → 6 筆，與本報告校準 z=2.0 用的 win6 視窗一致。規格 §4 於 2026-08-09 依此修訂。
ZEXT = 3
# MAD 退化門檻：這是 `mad <= 0` 的浮點安全版本，**不是換估計量、也不是放寬門檻**。
# 「位移離差全為 0」在浮點下不會剛好是 0：RS_Ratio 走 (100*share)/base，兩個相同的
# frame 只要差 1 ULP，MAD 就落在 1e-14 量級而非 0，`mad > 0` 會成立，
# z = (v - m) / 1e-14 直接噴到 1e16 並誤標。盤中一定會撞到——Worker /replay 的
# 「缺格往前回退 ≤5 分鐘」會讓相鄰兩個請求時點拿到同一格 frame，位移就是
# 「幾乎但不完全等於 0」。門檻取 1e-9，與前端實作一致。
MAD_EPS = 1e-9
# 淨位移退化門檻：這是 `net <= 0` / `tnet > 0` 的浮點安全版本，**不是換判準、也不是放寬門檻**。
# 同型於 MAD_EPS，但守的是**另一個量**——traj_stats 的「比值中位」把淨位移
# math.dist(pts[0], pts[-1]) 當分母，軌跡繞一圈回到原點時它「幾乎但不完全等於 0」：
# 座標走 (100*share)/base，起點與終點只要差 1 ULP，dist 就落在 1e-13 量級而非 0，
# `net > 0` 會成立，jit = 步中位 / 1e-13 直接噴到天文數字並汙染比值中位的樣本。
# 另立常數而非沿用 MAD_EPS：MAD_EPS 是「離差估計量退化」、本常數是「淨位移退化」，
# 兩者觸發條件不同（前者位移全同、後者首尾重合），日後要單獨調其一才不會誤動另一個；
# 量級相同是因為兩者都活在同一個 RS 座標空間（RS_Ratio／RS_Mom 以 100 為中心）。
NET_EPS = 1e-9
MIN_COVER = 0.9           # 日檔至少要有 90% 時點有市場總額才算有效交易日
TIERS = [("≥3%", 3.0, 1e9), ("1~3%", 1.0, 3.0), ("0.5~1%", 0.5, 1.0), ("<0.5%", 0.0, 0.5)]
BIG_TIERS = ("≥3%", "1~3%", "0.5~1%")   # 「非小基數」＝終盤 share ≥0.5%
QUADS = ("領先", "轉弱", "落後", "改善")

REC_K, REC_L, REC_STEP, REC_Z = 5, 6, 2, 2.0   # 建議參數（結論見報告末段）
REC_F = 0.02                                   # 建議的絕對變化量下限（share 百分點，鏈層）

LINES: list[str] = []


def emit(s: str = "") -> None:
    print(s)
    LINES.append(s)


def pctl(xs, p):
    if not xs:
        return float("nan")
    xs = sorted(xs)
    k = (len(xs) - 1) * p / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return xs[lo]
    return xs[lo] * (hi - k) + xs[hi] * (k - lo)


def mad(xs):
    """中位數絕對離差（未做 1.4826 常態化；z 門檻直接對這個尺度校準）。"""
    if not xs:
        return 0.0
    m = st.median(xs)
    return st.median([abs(x - m) for x in xs])


def quad(r, m):
    if r >= 100.0:
        return "領先" if m >= 100.0 else "轉弱"
    return "改善" if m >= 100.0 else "落後"


def tier_of(share_pct):
    for nm, lo, hi in TIERS:
        if lo <= share_pct < hi:
            return nm
    return TIERS[-1][0]


# ---------------------------------------------------------------- 資料載入

def load_days():
    """只收時點覆蓋率 ≥MIN_COVER 的日檔。
    （例：2026-07-18 的 total 僅 1/54 格有值，屬殘檔，必須剔除。）"""
    days, dropped = [], []
    for f in sorted(INTRADAY.glob("????-??-??.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        tot = d.get("total") or []
        cover = (sum(1 for v in tot if v) / len(tot)) if tot else 0.0
        if cover < MIN_COVER:
            dropped.append((d.get("date"), cover))
            continue
        days.append(d)
    return days, dropped


def build_shares(days):
    """回傳 (dates, times, names, shares)。shares[date][name][t] = 佔全市場比例（缺值 None）。"""
    dates = [d["date"] for d in days]
    times = days[0]["times"]
    T = len(times)
    names = sorted({n for d in days for n, arr in (d.get("g") or {}).items()
                    if arr and any(v for v in arr if v)})
    shares = {}
    for d in days:
        tot, g = d["total"], (d.get("g") or {})
        row = {}
        for n in names:
            arr = g.get(n) or []
            row[n] = [(arr[t] / tot[t]) if (t < len(arr) and arr[t] is not None
                                            and t < len(tot) and tot[t]) else None
                      for t in range(T)]
        shares[d["date"]] = row
    return dates, times, names, shares


def last_valid(xs):
    for v in reversed(xs):
        if v is not None:
            return v
    return None


def end_share_pct(shares, d, n):
    v = last_valid(shares[d][n])
    return (v * 100.0) if v is not None else 0.0


# ---------------------------------------------------------------- 座標

def build_ratio(dates, names, shares, K, mode="same_time"):
    """RS_Ratio。mode='same_time'：base 用前 K 日**同一時點** share 平均；
    mode='flat'（對照組）：base 用前 K 日**終盤** share 平均，全日常數。"""
    T = len(shares[dates[0]][names[0]])
    out = {}
    for di in range(K, len(dates)):
        d = dates[di]
        prev = [shares[dates[di - k]] for k in range(1, K + 1)]
        row = {}
        for n in names:
            cur = shares[d][n]
            if mode == "same_time":
                r = []
                for t in range(T):
                    if cur[t] is None:
                        r.append(None)
                        continue
                    vals = [p[n][t] for p in prev]
                    if any(v is None for v in vals):
                        r.append(None)
                        continue
                    b = sum(vals) / K
                    r.append(100.0 * cur[t] / b if b > 0 else None)
            else:
                vals = [last_valid(p[n]) for p in prev]
                b = (sum(vals) / K) if all(v is not None for v in vals) else 0.0
                r = [None if (cur[t] is None or b <= 0) else 100.0 * cur[t] / b
                     for t in range(T)]
            row[n] = r
        out[d] = row
    return out


def mom_from_ratio(rat, L):
    return [None if (t < L or rat[t] is None or rat[t - L] is None)
            else 100.0 + rat[t] - rat[t - L] for t in range(len(rat))]


def build_coords(ratios, names, L):
    out = {}
    for d, row in ratios.items():
        c = {}
        for n in names:
            r = row[n]
            m = mom_from_ratio(r, L)
            c[n] = [None if (r[t] is None or m[t] is None) else (r[t], m[t])
                    for t in range(len(r))]
        out[d] = c
    return out


def sample_idx(T, step, L):
    """取樣時點：錨在收盤 13:30 往前推；需算得出動能，故起點 ≥ L。"""
    idx = list(range(T - 1, L - 1, -step))
    idx.reverse()
    return idx


# ---------------------------------------------------------------- 判定 1／2

def traj_stats(coords, names, idx, tiers):
    """jit = 相鄰取樣點位移中位數 / 淨位移。回傳全日版與尾巴（末 WIN 點）版。"""
    jit_all, jit_big, jit_tail, stepmed, netmed, eff = [], [], [], [], [], []
    for d, row in coords.items():
        for n in names:
            pts = [row[n][t] for t in idx]
            if any(p is None for p in pts) or len(pts) < 3:
                continue
            ds = [math.dist(a, b) for a, b in zip(pts, pts[1:])]
            net = math.dist(pts[0], pts[-1])
            sm = st.median(ds)
            stepmed.append(sm)
            netmed.append(net)
            tp = pts[-WIN:]
            if len(tp) >= 3:
                tnet = math.dist(tp[0], tp[-1])
                if tnet > NET_EPS:   # 尾巴首尾重合（含浮點殘渣）→ 不列入比值
                    jit_tail.append(st.median([math.dist(a, b)
                                               for a, b in zip(tp, tp[1:])]) / tnet)
            if net <= NET_EPS:       # 全日淨位移退化（含浮點殘渣）→ 不列入比值
                continue
            jit_all.append(sm / net)
            eff.append(sum(ds) / net)
            if tiers.get((d, n)) in BIG_TIERS:
                jit_big.append(sm / net)
    return jit_all, jit_big, jit_tail, stepmed, netmed, eff


def quad_stats(coords, names, idx, tiers):
    """相鄰取樣點象限不變比例 ＋ 各象限成員數的逐格變動。"""
    same_all = keep_all = same_big = keep_big = 0
    cnt_series = {}
    for d, row in coords.items():
        per_j = []
        for t in idx:
            c = {q: 0 for q in QUADS}
            for n in names:
                p = row[n][t]
                if p is not None:
                    c[quad(*p)] += 1
            per_j.append(c)
        for q in QUADS:
            cnt_series[(d, q)] = [c[q] for c in per_j]
        for n in names:
            seq = [row[n][t] for t in idx]
            for a, b in zip(seq, seq[1:]):
                if a is None or b is None:
                    continue
                ok = quad(*a) == quad(*b)
                keep_all += 1
                same_all += ok
                if tiers.get((d, n)) in BIG_TIERS:
                    keep_big += 1
                    same_big += ok
    churn, size = {}, {}
    for q in QUADS:
        ch, sz = [], []
        for d in coords:
            s = cnt_series[(d, q)]
            sz.extend(s)
            ch.extend(abs(b - a) for a, b in zip(s, s[1:]))
        churn[q] = st.mean(ch) if ch else 0.0
        size[q] = st.mean(sz) if sz else 0.0
    return (same_all / keep_all if keep_all else 0.0,
            same_big / keep_big if keep_big else 0.0, churn, size)


# ---------------------------------------------------------------- 判定 4／5

def disp_series(coords, shares, names, idx, L):
    """{(date,name): [(j, t, RS位移, |Δshare| 百分點)]}；
    RS位移＝座標點 t 與 t-L 的歐氏距離（＝動能窗長的位移）。"""
    out = {}
    for d, row in coords.items():
        for n in names:
            seq = []
            for j, t in enumerate(idx):
                a = row[n][t]
                b = row[n][t - L] if t - L >= 0 else None
                if a is None or b is None:
                    continue
                s1, s0 = shares[d][n][t], shares[d][n][t - L]
                dpp = abs(s1 - s0) * 100.0 if (s1 is not None and s0 is not None) else 0.0
                seq.append((j, t, math.dist(a, b), dpp))
            if seq:
                out[(d, n)] = seq
    return out


def zscore_pool(disp, coords, variant):
    """{(date,j): [(name, z, RS位移, 象限, Δpp)]}。
    variant='day'：自身分布用當日全程取樣點（理想值，離線才拿得到）；
    variant='win'：只用最近 WIN 個取樣點（＝前端 N=WIN 個 /replay 點實際拿得到的量）。"""
    pool = defaultdict(list)
    for (d, n), seq in disp.items():
        ds = [x[2] for x in seq]
        if variant == "day":
            m, a = st.median(ds), mad(ds)
            if a <= MAD_EPS:      # 離差退化（含浮點殘渣），不給 z
                continue
            for j, t, v, dpp in seq:
                pool[(d, j)].append((n, (v - m) / a, v, quad(*coords[d][n][t]), dpp))
        else:
            for k, (j, t, v, dpp) in enumerate(seq):
                w = ds[max(0, k - WIN + 1):k + 1]
                if len(w) < MIN_WIN:
                    continue
                m, a = st.median(w), mad(w)
                if a <= MAD_EPS:  # 同上；盤中 /replay 回退同格會撞到這裡
                    continue
                pool[(d, j)].append((n, (v - m) / a, v, quad(*coords[d][n][t]), dpp))
    return pool


def flag_stats(pool, tiers, z0, floor_pp=0.0):
    """回傳 (每時點平均標記數, 每時點平均非小基數標記數, 各層被標比率%, 被標組成%, 改善象限%)。
    floor_pp>0 時再疊一層「|Δshare| ≥ floor_pp 百分點」的絕對變化量下限。"""
    ev = defaultdict(int)
    hit = defaultdict(int)
    per, per_big, imp, tot = [], [], 0, 0
    for (d, j), rows in pool.items():
        nh = nb = 0
        for n, z, v, q, dpp in rows:
            tv = tiers.get((d, n), "<0.5%")
            ev[tv] += 1
            if z >= z0 and dpp >= floor_pp:
                hit[tv] += 1
                nh += 1
                tot += 1
                imp += (q == "改善")
                if tv in BIG_TIERS:
                    nb += 1
        per.append(nh)
        per_big.append(nb)
    rate = {nm: (hit[nm] / ev[nm] * 100 if ev[nm] else 0.0) for nm, _, _ in TIERS}
    comp = {nm: (hit[nm] / tot * 100 if tot else 0.0) for nm, _, _ in TIERS}
    return (st.mean(per) if per else 0.0, st.mean(per_big) if per_big else 0.0,
            rate, comp, (imp / tot * 100 if tot else 0.0))


def topn_stats(pool, tiers, n_top):
    """對照組：不做自身標準化，每時點直接取原始位移最大的 N 個。"""
    comp, imp, tot = defaultdict(int), 0, 0
    for (d, j), rows in pool.items():
        for n, z, v, q, dpp in sorted(rows, key=lambda r: -r[2])[:n_top]:
            comp[tiers.get((d, n), "<0.5%")] += 1
            imp += (q == "改善")
            tot += 1
    return ({nm: comp[nm] / tot * 100 if tot else 0.0 for nm, _, _ in TIERS},
            (imp / tot * 100 if tot else 0.0))


def fmt_tiers(d):
    return " / ".join(f"{d.get(nm,0):.1f}%" for nm, _, _ in TIERS)


# ---------------------------------------------------------------- 產業鏈層

def load_sub2chains():
    f = DATA / "classify.json"
    if not f.exists():
        return {}
    cmap = json.loads(f.read_text(encoding="utf-8")).get("map") or {}
    s2c = defaultdict(set)
    for v in cmap.values():
        for pair in (v.get("p") or []):
            if len(pair) >= 2:
                s2c[pair[1]].add(pair[0])
    return {k: sorted(v) for k, v in s2c.items()}


def chain_shares(days, s2c):
    """次產業累計額依 classify 映射彙總到產業鏈（跨鏈次產業金額均分），再轉 share。"""
    T = len(days[0]["times"])
    chains = sorted({c for cs in s2c.values() for c in cs})
    shares = {}
    for d in days:
        tot, g = d["total"], (d.get("g") or {})
        acc = {c: [0.0] * T for c in chains}
        for sub, arr in g.items():
            cs = s2c.get(sub)
            if not cs or not arr:
                continue
            k = len(cs)
            for t in range(min(T, len(arr))):
                v = arr[t]
                if not v:
                    continue
                for c in cs:
                    acc[c][t] += v / k
        shares[d["date"]] = {c: [(acc[c][t] / tot[t]) if tot[t] else None
                                 for t in range(T)] for c in chains}
    return chains, shares


# ---------------------------------------------------------------- 主流程

def main():
    days, dropped = load_days()
    dates, times, names, shares = build_shares(days)
    T = len(times)

    emit("# 回測報告：盤中 RRG（相對輪動圖）座標定義驗證")
    emit("")
    emit(f"樣本 **{len(dates)} 個交易日**（{dates[0]} ~ {dates[-1]}）· "
         f"日內 {T} 個時點（{times[0]}~{times[-1]}，每 5 分）· "
         f"次產業 {len(names)} 個 · 來源 `data/intraday/*.json` 的 `g` 矩陣。")
    if dropped:
        emit("")
        emit("剔除的殘檔（時點覆蓋率 < %.0f%%）：%s" %
             (MIN_COVER * 100,
              "、".join(f"`{d}`（{c*100:.0f}%）" for d, c in dropped)))
    emit("")
    emit("兩軸定義（正本與 `run_rrg.py` 檔頭一致）：")
    emit("")
    emit("```")
    emit("share_i(d,t)    = 該族群累計成交額(t) / 全市場累計成交額(t)")
    emit("base_i(d,t)     = 前 K 個交易日「同一時點 t」的 share 平均")
    emit("RS_Ratio_i(d,t) = 100 * share_i(d,t) / base_i(d,t)")
    emit("RS_Mom_i(d,t)   = 100 + (RS_Ratio_i(d,t) - RS_Ratio_i(d,t-L))")
    emit("```")
    emit("")

    tiers = {(d, n): tier_of(end_share_pct(shares, d, n)) for d in dates for n in names}
    tcnt = defaultdict(int)
    for k in tiers.values():
        tcnt[k] += 1
    ntot = sum(tcnt.values())
    emit("終盤 share 分層的「族群×日」母體：" +
         "　".join(f"{nm}={tcnt[nm]}（{tcnt[nm]/ntot*100:.1f}%）" for nm, _, _ in TIERS))
    emit("")
    small_pop = tcnt["<0.5%"] / ntot * 100
    emit(f"> 母體有 **{small_pop:.0f}% 是「<0.5%」的小基數族群**——這是判定 4 的關鍵背景：")
    emit(f"> 一個「與規模無關」的門檻，抽出來的名單自然也會有約 {small_pop:.0f}% 是小基數，")
    emit("> 這不等於門檻被小基數汙染，要看的是**各層的被標比率**而非名單組成。")
    emit("")

    ratios = {(K, m): build_ratio(dates, names, shares, K, m)
              for K in KS for m in ("same_time", "flat")}
    coords = {(K, L): build_coords(ratios[(K, "same_time")], names, L)
              for K in KS for L in LS}

    # ---------------- 判定 1 ----------------
    emit("## 1. 軌跡訊噪比")
    emit("")
    emit("`比值` ＝ 相鄰取樣點位移中位數 ÷ 當日全程淨位移；越低＝軌跡是「走出去」而非原地抖動。")
    emit(f"`尾巴比值` 只取**最後 {WIN} 個取樣點**（＝前端實際畫出來的那條尾巴），是更貼近畫面的指標。")
    emit("`路徑/淨位移` 是同一件事的另一種寫法（1.0＝直線，越大越繞）。單位皆為 RS 點。")
    emit("")
    emit("| K | L(分) | step(分) | 取樣點/日 | 位移中位 | 全日淨位移中位 | 比值中位(全族群) | 比值中位(≥0.5%) | 比值P75 | 尾巴比值中位 | 路徑/淨位移 |")
    emit("|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    jit_by = {}
    for K in KS:
        for L in LS:
            for step in STEPS:
                idx = sample_idx(T, step, L)
                ja, jb, jt, sm, nm_, ef = traj_stats(coords[(K, L)], names, idx, tiers)
                jit_by[(K, L, step)] = dict(all=st.median(ja), big=st.median(jb),
                                            p75=pctl(ja, 75), tail=st.median(jt),
                                            eff=st.median(ef), npts=len(idx),
                                            step_rs=st.median(sm))
                emit(f"| {K} | {L*5} | {step*5} | {len(idx)} | {st.median(sm):.2f} | "
                     f"{st.median(nm_):.2f} | {st.median(ja):.3f} | {st.median(jb):.3f} | "
                     f"{pctl(ja,75):.3f} | {st.median(jt):.3f} | {st.median(ef):.2f} |")
    emit("")

    # ---------------- 判定 2 ----------------
    emit("## 2. 象限穩定度")
    emit("")
    emit("`象限不變` ＝ 相鄰兩個取樣點落在同一象限的比例。"
         "注意**跨 step 的欄位不可直接比大小**（step 越大，兩點相隔的實際時間越長，本來就更容易換象限）；"
         "同一 step 內比 L 才是公平比較。")
    emit("")
    emit("| K | L(分) | step(分) | 象限不變(全族群) | 象限不變(≥0.5%) | 改善成員數 | 改善逐格變動 | 領先成員數 | 領先逐格變動 |")
    emit("|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    q_by = {}
    for K in KS:
        for L in LS:
            for step in STEPS:
                idx = sample_idx(T, step, L)
                sa, sb, churn, size = quad_stats(coords[(K, L)], names, idx, tiers)
                q_by[(K, L, step)] = dict(all=sa, big=sb, imp=size["改善"],
                                          imp_churn=churn["改善"])
                emit(f"| {K} | {L*5} | {step*5} | {sa*100:.1f}% | {sb*100:.1f}% | "
                     f"{size['改善']:.1f} | {churn['改善']:.1f} | "
                     f"{size['領先']:.1f} | {churn['領先']:.1f} |")
    emit("")
    emit("**關鍵讀法**：固定 step=10 分時，L 由 10 分拉到 30 分，象限不變比例從 "
         f"{q_by[(REC_K,2,2)]['all']*100:.1f}% 升到 {q_by[(REC_K,6,2)]['all']*100:.1f}%。"
         "原因是 L>step 時相鄰兩點的動能窗重疊，天然被平滑；L=step 時兩窗完全不重疊，"
         "等於把兩段獨立噪音相減。**建議永遠讓 L 是 step 的整數倍且 ≥2 倍。**")
    emit("")

    # ---------------- 判定 3 ----------------
    emit("## 3. 小基數汙染程度")
    emit("")
    emit("### 3a. 各層 RS_Ratio 分布（同時點基準 vs 終盤基準對照）")
    emit("")
    emit("對照組 `終盤基準` ＝ base 改用「前 K 日**收盤** share」當全日常數，"
         "刻意不中和日內時間形狀。")
    emit("")
    emit("| 基準 | K | 分層 | n | 中位 | P95 | >150 | >200 | 最大 |")
    emit("|:--|---:|:--|---:|---:|---:|---:|---:|---:|")
    tier_stat = {}
    for mode, label in (("same_time", "同時點"), ("flat", "終盤(對照)")):
        for K in KS:
            rr = ratios[(K, mode)]
            buckets = defaultdict(list)
            for d, row in rr.items():
                for n in names:
                    tv = tiers[(d, n)]
                    for v in row[n]:
                        if v is not None:
                            buckets[tv].append(v)
            for nm, _, _ in TIERS:
                b = buckets[nm]
                if not b:
                    continue
                s = dict(n=len(b), med=st.median(b), p95=pctl(b, 95),
                         o150=sum(1 for v in b if v > 150) / len(b) * 100,
                         o200=sum(1 for v in b if v > 200) / len(b) * 100, mx=max(b))
                tier_stat[(mode, K, nm)] = s
                emit(f"| {label} | {K} | {nm} | {s['n']} | {s['med']:.1f} | {s['p95']:.1f} | "
                     f"{s['o150']:.2f}% | {s['o200']:.2f}% | {s['mx']:,.0f} |")
    emit("")

    emit("### 3b. 同時點基準到底有沒有中和日內時間形狀")
    emit("")
    emit("前置分析發現小基數族群的 RS_Ratio 會暴衝到 197~206。這裡看的是**同一個問題的另一面**："
         "如果基準沒有中和日內成交量的時間形狀，RS_Ratio 的中位數會隨時點系統性漂移"
         "（早盤偏高／午盤偏低之類），而不是穩定停在 100 附近。")
    emit("")
    probe = [0, 3, 9, 17, 29, 41, 53]
    probe = [t for t in probe if t < T]
    emit("| 時點 | 同時點基準 中位 | 同時點 P95 | 終盤基準 中位 | 終盤 P95 |")
    emit("|:--|---:|---:|---:|---:|")
    drift = {}
    for mode in ("same_time", "flat"):
        rr = ratios[(REC_K, mode)]
        drift[mode] = {}
        for t in probe:
            vals = [row[n][t] for row in rr.values() for n in names if row[n][t] is not None]
            drift[mode][t] = (st.median(vals), pctl(vals, 95)) if vals else (float("nan"),) * 2
    for t in probe:
        a, b = drift["same_time"][t], drift["flat"][t]
        emit(f"| {times[t]} | {a[0]:.1f} | {a[1]:.1f} | {b[0]:.1f} | {b[1]:.1f} |")
    sm_rng = max(drift["same_time"][t][0] for t in probe) - min(drift["same_time"][t][0] for t in probe)
    fl_rng = max(drift["flat"][t][0] for t in probe) - min(drift["flat"][t][0] for t in probe)
    emit("")
    emit(f"中位數跨時點極差：**同時點基準 {sm_rng:.1f} 點 vs 終盤基準 {fl_rng:.1f} 點**"
         f"（K={REC_K}）。差距越小代表基準越乾淨地把日內時間形狀吃掉了。")
    emit("")

    # ---------------- 判定 4 ----------------
    emit("## 4. 「有意義位移」的可實作定義")
    emit("")
    emit("`z_i(t) = (近 L 期位移 − 該族群自身位移中位數) / 該族群自身位移 MAD`"
         "（MAD 未做 1.4826 常態化，門檻直接對這個尺度校準）。")
    emit(f"`day` ＝ 自身分布用當日全程取樣點（離線理想值）；"
         f"`win{WIN}` ＝ 只用最近 {WIN} 筆位移（＝規格 §4 前端打 "
         f"{WIN+REC_L//REC_STEP+ZEXT} 次 /replay 實際湊得出來的量，推導見「建議參數」段"
         f"的直接後果第 1 點）。")
    emit(f"下表固定 K={REC_K}、step={REC_STEP*5} 分；`各層被標比率` 是「該層被標次數 ÷ 該層評估次數」，"
         "拿來跟母體佔比對照就知道門檻有沒有偏袒小基數。")
    emit("")
    emit("| 變體 | L(分) | z | 每時點標記數 | 其中 ≥0.5% | 各層被標比率 ≥3%/1~3%/0.5~1%/<0.5% | 被標組成 ≥3%/1~3%/0.5~1%/<0.5% | 改善象限 |")
    emit("|:--|---:|---:|---:|---:|:--|:--|---:|")
    z_rows = {}
    for L in LS:
        idx = sample_idx(T, REC_STEP, L)
        cd = coords[(REC_K, L)]
        dsp = disp_series(cd, shares, names, idx, L)
        for variant, vkey in (("day", "day"), (f"win{WIN}", "win")):
            pool = zscore_pool(dsp, cd, vkey)
            for z0 in ZS:
                avg, avg_big, rate, comp, impp = flag_stats(pool, tiers, z0)
                z_rows[(variant, L, REC_STEP, z0)] = dict(avg=avg, big=avg_big,
                                                          rate=rate, comp=comp, imp=impp)
                emit(f"| {variant} | {L*5} | {z0} | {avg:.1f} | {avg_big:.1f} | "
                     f"{fmt_tiers(rate)} | {fmt_tiers(comp)} | {impp:.1f}% |")
    emit("")
    emit("### 4a. 對照組：不做自身標準化，直接取絕對位移 Top-N")
    emit("")
    emit(f"（K={REC_K}、L={REC_L*5} 分、step={REC_STEP*5} 分）")
    emit("")
    emit("| N | 分層組成 ≥3%/1~3%/0.5~1%/<0.5% | 改善象限 |")
    emit("|---:|:--|---:|")
    idx = sample_idx(T, REC_STEP, REC_L)
    dsp = disp_series(coords[(REC_K, REC_L)], shares, names, idx, REC_L)
    pool_ref = zscore_pool(dsp, coords[(REC_K, REC_L)], "win")
    abs_rows = {}
    for n_top in (5, 10, 20, 50):
        comp, impp = topn_stats(pool_ref, tiers, n_top)
        abs_rows[n_top] = (comp, impp)
        emit(f"| {n_top} | {fmt_tiers(comp)} | {impp:.1f}% |")
    emit("")
    emit("### 4b. step 敏感度（固定 z=%.1f、變體 win%d）" % (REC_Z, WIN))
    emit("")
    emit("| L(分) | step(分) | 每時點標記數 | 其中 ≥0.5% | <0.5% 被標比率 | 改善象限 |")
    emit("|---:|---:|---:|---:|---:|---:|")
    z_step = {}
    for L in LS:
        for step in STEPS:
            idx2 = sample_idx(T, step, L)
            cd2 = coords[(REC_K, L)]
            p2 = zscore_pool(disp_series(cd2, shares, names, idx2, L), cd2, "win")
            avg, avg_big, rate, comp, impp = flag_stats(p2, tiers, REC_Z)
            z_step[(L, step)] = dict(avg=avg, big=avg_big, imp=impp)
            emit(f"| {L*5} | {step*5} | {avg:.1f} | {avg_big:.1f} | "
                 f"{rate['<0.5%']:.1f}% | {impp:.1f}% |")
    emit("")

    # ---------------- 判定 5 ----------------
    emit("## 5. 輔助排序：RS 位移 vs share 絕對變化量（百分點）")
    emit("")
    emit("兩種排序都取同一時點的 Top 10，比較名單重疊、分層組成與名單成員的終盤 share 中位數。")
    emit(f"（K={REC_K}）")
    emit("")
    emit("| L(分) | step(分) | Top10 重疊 | RS位移榜 分層組成 | RS位移榜 中位share | Δpp榜 分層組成 | Δpp榜 中位share |")
    emit("|---:|---:|---:|:--|---:|:--|---:|")
    rank_rows = {}
    for L in LS:
        for step in STEPS:
            idx2 = sample_idx(T, step, L)
            cd2 = coords[(REC_K, L)]
            ov = []
            rs_t, pp_t = defaultdict(int), defaultdict(int)
            rs_s, pp_s = [], []
            for d in cd2:
                for t in idx2:
                    if t - L < 0:
                        continue
                    a, b = [], []
                    for n in names:
                        p, q = cd2[d][n][t], cd2[d][n][t - L]
                        s1, s0 = shares[d][n][t], shares[d][n][t - L]
                        if p is None or q is None or s1 is None or s0 is None:
                            continue
                        a.append((n, math.dist(p, q)))
                        b.append((n, abs(s1 - s0) * 100.0))
                    if len(a) < 10:
                        continue
                    ta = [n for n, _ in sorted(a, key=lambda x: -x[1])[:10]]
                    tb = [n for n, _ in sorted(b, key=lambda x: -x[1])[:10]]
                    ov.append(len(set(ta) & set(tb)))
                    for n in ta:
                        rs_t[tiers[(d, n)]] += 1
                        rs_s.append(end_share_pct(shares, d, n))
                    for n in tb:
                        pp_t[tiers[(d, n)]] += 1
                        pp_s.append(end_share_pct(shares, d, n))
            rs_c = {nm: rs_t[nm] / sum(rs_t.values()) * 100 for nm, _, _ in TIERS}
            pp_c = {nm: pp_t[nm] / sum(pp_t.values()) * 100 for nm, _, _ in TIERS}
            rank_rows[(L, step)] = dict(ov=st.mean(ov), rs_sh=st.median(rs_s),
                                        pp_sh=st.median(pp_s), rs_c=rs_c, pp_c=pp_c)
            emit(f"| {L*5} | {step*5} | {st.mean(ov):.1f}/10 | {fmt_tiers(rs_c)} | "
                 f"{st.median(rs_s):.2f}% | {fmt_tiers(pp_c)} | {st.median(pp_s):.2f}% |")
    emit("")
    emit("### 5a. 把兩者疊起來：z 門檻 ＋ 絕對變化量下限")
    emit("")
    emit(f"判定 4 的 z 抽出的是「相對自己而言不尋常」，判定 5 的 Δpp 抽出的是「絕對量夠大」；"
         f"兩個都要才是可看的名單。下表在建議參數（K={REC_K}／L={REC_L*5} 分／"
         f"step={REC_STEP*5} 分／win{WIN}）下疊加下限 F：`z ≥ {REC_Z} AND |Δshare| ≥ F 百分點`。")
    emit("")
    emit("| F(百分點) | 每時點標記數 | 其中 ≥0.5% | 分層組成 ≥3%/1~3%/0.5~1%/<0.5% | 改善象限 |")
    emit("|---:|---:|---:|:--|---:|")
    floor_rows = {}
    for F in FLOORS:
        avg, avg_big, rate, comp, impp = flag_stats(pool_ref, tiers, REC_Z, F)
        floor_rows[F] = dict(avg=avg, big=avg_big, comp=comp, imp=impp)
        emit(f"| {F:.2f} | {avg:.1f} | {avg_big:.1f} | {fmt_tiers(comp)} | {impp:.1f}% |")
    emit("")

    # ---------------- 判定 6：產業鏈層 ----------------
    emit("## 6. 產業鏈層交叉檢查（結論可移轉性）")
    emit("")
    s2c = load_sub2chains()
    chain_rows, chain_floor, chain_pool, ctiers = {}, {}, None, {}
    if s2c:
        chains, cshares = chain_shares(days, s2c)
        ctiers = {(d, c): tier_of(end_share_pct(cshares, d, c)) for d in dates for c in chains}
        emit(f"`data/classify.json` 映射：次產業 {len(s2c)} 個 → 產業鏈 {len(chains)} 條"
             f"（跨鏈次產業金額均分）。以下用建議參數 K={REC_K}／L={REC_L*5} 分／"
             f"step={REC_STEP*5} 分／z={REC_Z} 重跑判定 1、2、3、4 的關鍵欄位。")
        emit("")
        emit("| 層級 | 族群數 | 比值中位 | 尾巴比值中位 | 象限不變 | <0.5% 層 RS_Ratio P95 | <0.5% 層 >150 | 每時點 z≥2 標記數 | 其中 ≥0.5% |")
        emit("|:--|---:|---:|---:|---:|---:|---:|---:|---:|")
        for lab, nm_list, sh in (("次產業", names, shares), ("產業鏈", chains, cshares)):
            tt = tiers if lab == "次產業" else ctiers
            rr = build_ratio(dates, nm_list, sh, REC_K)
            cc = build_coords(rr, nm_list, REC_L)
            idx3 = sample_idx(T, REC_STEP, REC_L)
            ja, _, jt, _, _, _ = traj_stats(cc, nm_list, idx3, tt)
            sa, _, _, _ = quad_stats(cc, nm_list, idx3, tt)
            small = [v for d, row in rr.items() for n in nm_list
                     if tt[(d, n)] == "<0.5%" for v in row[n] if v is not None]
            p95 = pctl(small, 95) if small else float("nan")
            over = (sum(1 for v in small if v > 150) / len(small) * 100) if small else 0.0
            pl = zscore_pool(disp_series(cc, sh, nm_list, idx3, REC_L), cc, "win")
            avg, avg_big, rate, comp, impp = flag_stats(pl, tt, REC_Z)
            chain_rows[lab] = dict(n=len(nm_list), jit=st.median(ja), tail=st.median(jt),
                                   quad=sa, p95=p95, over=over, avg=avg, big=avg_big,
                                   comp=comp, imp=impp)
            if lab == "產業鏈":
                chain_pool = pl
            emit(f"| {lab} | {len(nm_list)} | {st.median(ja):.3f} | {st.median(jt):.3f} | "
                 f"{sa*100:.1f}% | {p95:.1f} | {over:.2f}% | {avg:.1f} | {avg_big:.1f} |")
        emit("")
        emit("產業鏈層 z≥%.1f 名單的分層組成：%s；位於改善象限 %.1f%%。"
             % (REC_Z, fmt_tiers(chain_rows["產業鏈"]["comp"]), chain_rows["產業鏈"]["imp"]))
        emit("")
        emit("### 6a. 產業鏈層的絕對變化量下限（線上真正要套的那組）")
        emit("")
        emit(f"同 5a，但族群改成 {len(chains)} 條產業鏈。**這張表才是前端要照著設定的。**")
        emit("")
        emit("| F(百分點) | 每時點標記數 | 其中 ≥0.5% | 分層組成 ≥3%/1~3%/0.5~1%/<0.5% | 改善象限 |")
        emit("|---:|---:|---:|:--|---:|")
        for F in FLOORS:
            avg, avg_big, rate, comp, impp = flag_stats(chain_pool, ctiers, REC_Z, F)
            chain_floor[F] = dict(avg=avg, big=avg_big, comp=comp, imp=impp)
            emit(f"| {F:.2f} | {avg:.1f} | {avg_big:.1f} | {fmt_tiers(comp)} | {impp:.1f}% |")
    else:
        emit("（找不到 `data/classify.json`，略過本節）")
    emit("")

    write_conclusions(dates, names, T, jit_by, q_by, tier_stat, drift, sm_rng, fl_rng,
                      z_rows, z_step, abs_rows, rank_rows, floor_rows, chain_rows, chain_floor)
    OUT.write_text("\n".join(LINES) + "\n", encoding="utf-8")
    print(f"\n[寫出] {OUT}")
    return 0


def write_conclusions(dates, names, T, jit_by, q_by, tier_stat, drift, sm_rng, fl_rng,
                      z_rows, z_step, abs_rows, rank_rows, floor_rows, chain_rows,
                      chain_floor):
    """「建議參數」與「局限」兩段；數字全部由前面的實測結果代入，不手寫。"""
    K, L, step, Z = REC_K, REC_L, REC_STEP, REC_Z
    vk = f"win{WIN}"
    j = jit_by[(K, L, step)]
    q = q_by[(K, L, step)]
    zr = z_rows[(vk, L, step, Z)]
    zr_day = z_rows[("day", L, step, Z)]
    rk = rank_rows[(L, step)]

    emit("## 建議參數")
    emit("")
    emit(f"**K = {K}　L = {L} 個時點（{L*5} 分）　step = {step} 個時點（{step*5} 分）　"
         f"z 門檻 = {Z}（用 win{WIN} 變體）**")
    emit("")
    emit("| 參數 | 建議值 | 理由（實測數字） |")
    emit("|:--|:--|:--|")
    emit(f"| K 基準交易日數 | **5** | 決勝點是**尾端爆值**不是 P95：K=3 的 <0.5% 層 RS_Ratio "
         f"最大值衝到 {tier_stat[('same_time',3,'<0.5%')]['mx']:,.0f}"
         f"（3 天基準只要有一天該族群幾乎沒量，分母就趨近 0），K=5 降到 "
         f"{tier_stat[('same_time',5,'<0.5%')]['mx']:,.0f}——差兩個數量級。"
         f"P95 三者相近（{tier_stat[('same_time',3,'<0.5%')]['p95']:.0f} / "
         f"{tier_stat[('same_time',5,'<0.5%')]['p95']:.0f} / "
         f"{tier_stat[('same_time',10,'<0.5%')]['p95']:.0f}），但 K=10 的 >200 比例反升到 "
         f"{tier_stat[('same_time',10,'<0.5%')]['o200']:.1f}%（K=5 是 "
         f"{tier_stat[('same_time',5,'<0.5%')]['o200']:.1f}%），還要 10 天暖機——"
         f"{len(dates)} 天樣本只剩 {len(dates)-10} 天可評，且 ≥3% 大族群中位數被拉到 "
         f"{tier_stat[('same_time',10,'≥3%')]['med']:.0f}（基準期含到兩週前的量能水位，"
         f"整條軸系統性偏移）。 |")
    emit(f"| L 動能回看 | **{L*5} 分（{L} 格）** | 決定性的一項。固定 step={step*5} 分時，"
         f"L=10 分的象限不變比例只有 {q_by[(K,2,step)]['all']*100:.1f}%（盤中每格都在跳象限，不可用）；"
         f"L=15 分 {q_by[(K,3,step)]['all']*100:.1f}%；L=30 分升到 {q['all']*100:.1f}%"
         f"（≥0.5% 族群 {q['big']*100:.1f}%）。原因是 L>step 時相鄰兩點的動能窗重疊，"
         f"天然被平滑；**L 必須是 step 的整數倍且 ≥2 倍**（否則 t-L 不落在取樣格上，"
         f"前端還得額外多打 /replay）。 |")
    emit(f"| step 取樣間隔 | **{step*5} 分** | 5 分取樣一天有 {jit_by[(K,L,1)]['npts']} 個點、"
         f"相鄰位移中位僅 {jit_by[(K,L,1)]['step_rs']:.1f} RS 點，線段密到看不出轉折；"
         f"{step*5} 分給 {j['npts']} 個點、位移中位 {j['step_rs']:.1f} RS 點，"
         f"全日比值中位 {j['all']:.3f}、尾巴（末 {WIN} 點）比值中位 {j['tail']:.3f}；"
         f"15 分只剩 {jit_by[(K,L,3)]['npts']} 個點且象限不變降到 "
         f"{q_by[(K,L,3)]['all']*100:.1f}%。 |")
    emit(f"| z 有意義位移門檻 | **{Z}**（自身分布只用最近 {WIN} 個取樣點） | "
         f"次產業層（{len(names)} 個族群）下 z=1.0 每時點標 "
         f"{z_rows[(vk,L,step,1.0)]['avg']:.0f} 個、z=1.5 標 "
         f"{z_rows[(vk,L,step,1.5)]['avg']:.0f} 個都太多；z={Z} 標 {zr['avg']:.0f} 個"
         f"（其中 ≥0.5% 的有 {zr['big']:.0f} 個），z=2.5 只剩 "
         f"{z_rows[(vk,L,step,2.5)]['avg']:.0f} 個、名單常常整段時間是空的。"
         f"**線上是產業鏈層，同一門檻只會標 {chain_rows['產業鏈']['avg']:.1f} 個/時點"
         f"（其中 ≥0.5% 的 {chain_rows['產業鏈']['big']:.1f} 個）**，剛好是側欄放得下的量。 |")
    emit(f"| 自身分布視窗 | **最近 {WIN} 個取樣點**，不足 {MIN_WIN} 筆位移不標 | "
         f"前端要打 {WIN+L//step+ZEXT} 次 `/replay` 才湊得出這 {WIN} 筆位移"
         f"（推導見下方「直接後果」第 1 點；打 {WIN+L//step} 次只有 "
         f"{WIN-L//step} 筆，低於 {MIN_WIN} 筆而永遠不觸發）。win{WIN} vs 理想的全日版（day）"
         f"在 z={Z} 下每時點標記數 {zr['avg']:.0f} vs {zr_day['avg']:.0f}、"
         f"改善象限比例 {zr['imp']:.1f}% vs {zr_day['imp']:.1f}%，"
         f"短視窗甚至更偏向改善象限，可接受。 |")
    emit(f"| F 絕對變化量下限 | **{REC_F:.2f} 個百分點**（鏈層） | 判定 6a：F=0 時鏈層每時點標 "
         f"{chain_floor[0.0]['avg']:.1f} 個、其中 "
         f"{chain_floor[0.0]['comp']['<0.5%']:.0f}% 是小基數；F={REC_F:.2f} 降到 "
         f"{chain_floor[REC_F]['avg']:.1f} 個、小基數只剩 "
         f"{chain_floor[REC_F]['comp']['<0.5%']:.0f}%，改善象限比例仍有 "
         f"{chain_floor[REC_F]['imp']:.1f}%。再往上到 F=0.05 會把小基數壓到 "
         f"{chain_floor[0.05]['comp']['<0.5%']:.1f}%——**等於把規格 §2 想保留的"
         f"「改善象限小族群」全砍光**，違反設計決策，不採用。 |")
    emit("")
    emit("### 前端實作的四個直接後果")
    emit("")
    emit(f"0. **MAD 退化必須用 `MAD <= {MAD_EPS:g}` 判斷，不能用 `MAD > 0`。** "
         f"RS_Ratio 走 `(100*share)/base`，兩個相同的 frame 只要差 1 ULP，"
         f"位移離差就是 1e-14 而不是 0，`MAD > 0` 會成立、z 噴到 1e16 直接誤標。"
         f"盤中一定會撞到：Worker `/replay` 的「缺格往前回退 ≤5 分鐘」會讓相鄰兩個"
         f"請求時點拿到同一格 frame，位移就是「幾乎但不完全等於 0」。"
         f"另外 **MAD 不要乘 1.4826**——本報告的 z 門檻就是對未常態化的 MAD 校準的，"
         f"乘了等於把門檻放寬 1.48 倍。")
    emit(f"1. **`/replay` 要打 {WIN+L//step+ZEXT} 次，不是 {WIN+L//step} 次，"
         f"更不是 {WIN} 次。**（**2026-08-09 修訂**：本點原本寫 {WIN+L//step} 次，"
         f"與同頁「自身分布視窗＝最近 {WIN} 個取樣點，不足 {MIN_WIN} 筆位移不標」"
         f"自相矛盾——{WIN+L//step} 個時點只生得出 {WIN-L//step} 筆位移，"
         f"**「位移異常」會永遠不觸發且畫面上看不出來**。照舊值實作等於把這個訊號靜默關掉。）")
    emit(f"   - **動能前置 {L//step} 格**：mom(t) 需要 ratio(t-{L})，{L} 格 = {L//step} "
         f"個取樣格；要畫 {WIN} 個軌跡點，最早那點還得往前 {L//step} 格 → {WIN+L//step} 個時點。")
    emit(f"   - **z 視窗再前置 ZEXT={ZEXT} 個座標點**：位移 d(t) 同樣回看 {L//step} 個取樣格，"
         f"故 N 個座標點只生得出 N−{L//step} 筆位移。{WIN+L//step} 個時點 → {WIN} 個座標點 "
         f"→ 只有 {WIN-L//step} 筆 < `MIN_WIN={MIN_WIN}`；要湊滿本表校準用的 {WIN} 筆，"
         f"座標點得有 {WIN+ZEXT} 個 → 時點 {WIN+L//step+ZEXT} 個。")
    emit(f"   - **通式：時點數 = WIN + L//step + ZEXT**。前端由 `OV_RRG_ZEXT` 控制："
         f"0→{WIN+L//step} 次（位移異常關閉）／1→{WIN+L//step+1} 次"
         f"（{MIN_WIN} 筆，剛好及格）／**{ZEXT}→{WIN+L//step+ZEXT} 次"
         f"（{WIN} 筆，與 z={Z} 的校準視窗一致，建議值）**。"
         f"涵蓋 {(WIN+L//step+ZEXT-1)*step*5} 分鐘。")
    emit(f"   - 額外成本只在**進入視角的首次**（多 {ZEXT} 個請求）；之後跨 {step*5} 分格"
         f"仍是 1 次/格，不影響 KV 讀取額度的穩態。")
    emit(f"2. **base 要前 {K} 個交易日的同一時點 share**，這不是 `/replay` 給得出來的東西"
         f"（`/replay` 只有今天）。這份基準必須另外備好——夜間 builder 產一份"
         f"「各時點 × 各鏈的近 {K} 日平均 share」小表，或 Worker 端存 KV，"
         f"不能在前端現算。")
    emit(f"3. **軌跡尾巴的抖動比全日軌跡大得多**：全日比值中位 {j['all']:.3f}，"
         f"但只看最後 {WIN} 個點是 {j['tail']:.3f}（鏈層 {chain_rows['產業鏈']['tail']:.3f}）。"
         f"畫面上那條尾巴天生比整日軌跡難讀，建議把尾巴節點畫小、只在最新點放標籤。")
    emit("")
    emit("### 為什麼是「自身標準化」而不是絕對位移")
    emit("")
    emit(f"對照組（判定 4a）：不做標準化直接取絕對位移 Top10，分層組成是 "
         f"{fmt_tiers(abs_rows[10][0])}——**幾乎 100% 被 <0.5% 的小基數族群壟斷**，"
         f"改善象限比例只有 {abs_rows[10][1]:.1f}%。")
    emit(f"改用 z 自身標準化後，各層的**被標比率**變成 {fmt_tiers(zr['rate'])}，"
         f"四層之間差距被壓平；名單組成 {fmt_tiers(zr['comp'])} 之所以仍以 <0.5% 為大宗，"
         f"純粹是因為母體本身就有八成是小基數族群，**不是門檻偏袒它們**。")
    emit("")
    emit("### 必須再疊一層絕對變化量（判定 5 的結論）")
    emit("")
    emit(f"z 只解決「相對誰標準化」，沒解決「這個變化夠不夠大到值得看」。判定 5 顯示："
         f"RS 位移榜 Top10 的成員終盤 share 中位數只有 {rk['rs_sh']:.2f}%，"
         f"share 百分點變化榜是 {rk['pp_sh']:.2f}%，兩榜平均只重疊 {rk['ov']:.1f}/10——"
         f"它們在講兩件不同的事。分層組成也天差地遠"
         f"（RS 位移榜 {fmt_tiers(rk['rs_c'])} vs Δpp 榜 {fmt_tiers(rk['pp_c'])}）。")
    emit("")
    emit("**可實作定義（建議寫進前端）**：")
    emit("")
    emit("```")
    emit(f"# 座標（K={K}, L={L} 格 = {L*5} 分, step={step} 格 = {step*5} 分）")
    emit("ratio(t) = 100 * share(t) / mean(前 5 個交易日同一時點 t 的 share)")
    emit(f"mom(t)   = 100 + ratio(t) - ratio(t-{L})        # t-{L} 落在取樣格上")
    emit("")
    emit("# 有意義位移")
    emit(f"d(t)  = dist( (ratio,mom)@t , (ratio,mom)@t-{L} )")
    emit(f"D     = 最近 {WIN} 個取樣點的 d 值（至少 {MIN_WIN} 筆，否則不標）")
    emit(f"z(t)  = (d(t) - median(D)) / MAD(D)              # MAD 不乘 1.4826")
    emit(f"        若 MAD(D) <= {MAD_EPS:g} 則不標（離差退化的浮點安全判斷，見下）")
    emit(f"異常  = z(t) >= {Z}  AND  |share(t)*100 - share(t-{L})*100| >= {REC_F:.2f}  # 百分點")
    emit("```")
    emit("")
    emit(f"實測（判定 6a，產業鏈層）：這組定義平均每時點標 "
         f"{chain_floor[REC_F]['avg']:.1f} 條鏈，分層組成 "
         f"{fmt_tiers(chain_floor[REC_F]['comp'])}，其中 {chain_floor[REC_F]['imp']:.1f}% "
         f"位於改善象限。次產業層同一組定義是 {floor_rows[REC_F]['avg']:.1f} 個/時點"
         f"（分層組成 {fmt_tiers(floor_rows[REC_F]['comp'])}）。")
    emit("")

    emit("## 局限")
    emit("")
    emit(f"1. **樣本天數極少，且有效獨立樣本更少**。只有 {len(dates)} 個交易日"
         f"（{dates[0]}~{dates[-1]}），K=5 吃掉前 5 天暖機後只剩 {len(dates)-5} 天可評估；"
         f"K=10 那一欄只有 {len(dates)-10} 天，數字僅供參考、不足以下結論。"
         f"日內雖有 {T} 格 × 族群數看似樣本很大，但**同一天的相鄰時點高度自相關**"
         f"（累計額是遞增序列），有效獨立樣本數接近「天數」而非「天數×時點數」。"
         f"所有百分比都應視為量級指標，不是精確估計。")
    emit(f"2. **在次產業層驗證，線上要用產業鏈層**。`data/intraday` 的 `g` 只有次產業"
         f"（{len(names)} 個），key 無法反推產業鏈歸屬，因此第 6 節改用 `classify.json` "
         f"做 sub→chain 映射（跨鏈次產業金額均分）交叉檢查。移轉風險有三："
         f"(a) 族群數從 {chain_rows['次產業']['n']} 掉到 {chain_rows['產業鏈']['n']}，"
         f"**同一 z 門檻的每時點標記數會等比縮小**"
         f"（{chain_rows['次產業']['avg']:.0f} → {chain_rows['產業鏈']['avg']:.1f}），"
         f"前端不可沿用本檔的絕對張數；"
         f"(b) 鏈層是次產業的加總，大數法則讓分母變大、噪音變小，"
         f"軌跡比值 {chain_rows['次產業']['jit']:.3f} → {chain_rows['產業鏈']['jit']:.3f}、"
         f"象限不變 {chain_rows['次產業']['quad']*100:.1f}% → "
         f"{chain_rows['產業鏈']['quad']*100:.1f}%，實測是往好的方向走，"
         f"但 K/L/step 的**相對排序**才是本檔真正主張可移轉的東西，絕對數值不是；"
         f"(c) 均分法本身是近似，與線上 `ovAggBy` 的歸屬邏輯不一定一致。")
    emit(f"3. **近似誤差來源**："
         f"(a) 分母用 `data/intraday` 的 `total`，但規格 §4 指定線上用 `market.tse.amt_yi`，"
         f"兩者口徑（含不含上櫃、零股、鉅額）不保證一致，RS_Ratio 會整體平移；"
         f"(b) intraday 快照多數時點帶 `stale=1`（該格沿用上一筆），本檔未剔除 stale 格，"
         f"實際更新頻率可能低於名目 5 分，等於高估了軌跡的資訊量；"
         f"(c) 部分次產業的累計額序列**非單調遞增**（資料源事後修正），會在位移上製造假跳動；"
         f"(d) 產業鏈多對多歸屬造成重複計數，本檔用均分處理，鏈層 share 加總不等於 1。")
    emit(f"4. **同時點基準只在「有前 K 天同時點資料」時成立**。若前 K 天中任一天缺該時點，"
         f"本檔直接讓座標為 None。線上遇到連假、盤中斷線、或新上市族群（前 K 天沒資料）時"
         f"必須有降級路徑，本檔沒有驗證任何降級策略的效果。")
    emit(f"5. **只驗了「看不看得懂」，沒驗「準不準」**。本檔量的是可讀性（軌跡平滑度、"
         f"象限穩定度）與抗汙染（小基數會不會壟斷名單），"
         f"完全沒有回答「進入改善象限之後是否真的續強」——那需要價格資料與 T+N 報酬，"
         f"屬於另一段獨立回測。因此線上文案**只能描述資金佔比相對自身近期基準的變化**，"
         f"不得有任何價格方向的預測宣稱（CLAUDE.md 鐵律第 8 條）。")
    emit(f"6. **同時點基準的殘餘漂移**。判定 3b 顯示同時點基準的 RS_Ratio 中位數跨時點"
         f"極差 {sm_rng:.1f} 點（終盤基準對照組 {fl_rng:.1f} 點）。"
         f"殘餘漂移不為 0，代表基準沒有 100% 中和日內時間形狀；"
         f"跨時點比較 RS_Ratio 絕對值時要記得這件事。")
    emit("")


if __name__ == "__main__":
    sys.exit(main())
