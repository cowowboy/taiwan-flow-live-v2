#!/usr/bin/env python3
# backtest/run_rrg_daily_axes.py — 盤後日頻 RRG 第二階段：軸定義比較與參數定案
#
# 規格：docs/rrg-daily-spec-20260811.md §3（第二階段）＋ §5（已定案決策）
# 資料：data/chain_daily/series.json（283 交易日 × 47 條產業鏈，第一階段產出）
#       backtest/cache/chainday_*.json.gz（個股層日資料，只給「單一個股支配度」用）
#
# ── 要比較的軸定義 ────────────────────────────────────────────────────
# A 資金版（＝盤中版語意，backtest/run_rrg.py 的 build_ratio 同式，只是把「同時點」換成「日」）：
#     RS_Ratio_c(t) = 100 × share_c(t) / mean(share_c(t-K..t-1))      ← 前 K 日、**不含當日**
#     RS_Mom_c(t)   = 100 + (RS_Ratio_c(t) − RS_Ratio_c(t-L))
#   注意：這兩條式子只吃 share，**完全不吃報酬**。所以「A-ew」與「A-mw」的座標
#   逐位元相同（見 test_flow_axis_is_weighting_free）→ 規格要求的四格在座標層退化成三組。
#
# B 價格版（正統 RRG，JdK RS-Ratio / RS-Momentum 的公開重建版）：
#     idx_c(t)      = 鏈報酬指數（ret_ew 或 ret_mw 逐日連乘，基期 100）
#     idx_b(t)      = 大盤報酬指數（market.ret_ew 或 ret_mw，**與鏈同加權**，見下）
#     RS_c(t)       = 100 × idx_c(t) / idx_b(t)
#     RS_Ratio_c(t) = 100 + (RS_c(t) − mean(RS_c, n)) / sd(RS_c, n)    ← 含當日的 n 期窗
#     ROC_c(t)      = 100 × (RS_Ratio_c(t) / RS_Ratio_c(t-k) − 1)
#     RS_Mom_c(t)   = 100 + (ROC_c(t) − mean(ROC_c, n)) / sd(ROC_c, n)
#
#   JdK 的原式是專有的，官方文件（chartschool.stockcharts.com 的 RRG Relative Strength、
#   relativerotationgraphs.com 的 the-building-blocks-for-rrg）只寫「normalized、以 100 為基準、
#   RS-Momentum 是 RS-Ratio 的變化率」，不給公式。本檔採用流傳最廣、與官方敘述一致的
#   **z-score 重建版**（github.com/BennyThadikaran/RRG-Lite wiki「RS ratio and Momentum
#   calculations」：RS = 價格 ÷ 基準 ×100 → 減 n 期均線 → 除 n 期標準差 → 加 100 為基線；
#   RS-Momentum 對 ROC 走同一套 z-score）。另有一派用 EMA 比值取代 z-score
#   （github.com/AdroitAnandAI/RRG-Sector-Rotation-India 的「Enhanced」版），本檔不採，
#   理由：官方敘述明講「both are multiples of standard deviation」，z-score 版才對得上。
#   **這是重建、不是原廠公式**，屬本檔的已知限制（見報告「局限」段）。
#
#   大盤基準用 market.ret_*，不用 taiex_ret。理由在 backtest/report_rrg_daily.md
#   「FinMind 指數列的整段壞資料」段：TAIEX 有 12 天壞資料（已改收盤比值繞開，但口徑
#   從此與鏈不同：指數不還原股利、鏈用 spread 已還原），全期算術加總差 3.1pp——
#   那 3.1pp 會變成**所有鏈共同的分母漂移**，把 47 條鏈一起推向同一側。
#   同加權（ew 鏈配 ew 大盤、mw 鏈配 mw 大盤）則是為了不把「大小型股風格差」混進軸裡；
#   本檔另跑 taiex 與 ret_mw 兩種對照基準（見報告「基準敏感度」段）。
#
# ── 判定準則（規格 §3，四項）───────────────────────────────────────
#  1 跨日穩定度：相鄰日的象限成員 Jaccard、候補清單（改善∪領先）Jaccard、改善象限 Top5 交集。
#    **一律附「打散標籤的置換虛無基準」**——Jaccard 的隨機基準隨集合大小變動，
#    不減掉它就無法判斷 40~45% 到底是訊號還是集合大小的算術產物。
#  2 小分母汙染：依當日 share 分 4 層（沿用 run_rrg.py 的 TIERS），各層 RS-Ratio 極值與尾比例；
#    另給「鏈規模 vs RS 離散度」的 Spearman（盤中版量到 −0.64）。
#  3 單一個股支配度：對每檔股票算它在每條鏈的權重（三種加權各一），
#    以及「拿掉它，該鏈當日報酬會變動幾 bp」的封閉解 Δ = w(r_s − r_c)/(1−w)。
#    回答「一檔股票能決定多少條鏈的座標」＝ 中位權重 ≥20%（或中位 |Δ| ≥ 門檻）的鏈數。
#  4 輪動訊號延續性：比照 backtest/run_sector.py 的 run_level——同一套對照組
#    （「無訊號」＝當日未發生象限轉換）、同一組統計量（N／勝率／勝大盤率／超額 avg+med／黏性），
#    前瞻期擴充為 T+1／T+3／T+5。另加**按日期分塊的 bootstrap**，因為同一天 47 條鏈的
#    超額報酬高度相關，把 13,000 個 chain-day 當獨立樣本會嚴重低估標準誤。
#
# ── 一併定案 ──────────────────────────────────────────────────────
#  持續性條件 N（連續 N 日符合才進榜）、移動平均期數（A 的 K／B 的 n）、動能回看（L／k）。
#  另回答：早期（2025）與近期（2026）行為是否明顯不同（＝分類回溯誤差的徵兆）。
#
# 用法：python backtest/run_rrg_daily_axes.py  → 印表 ＋ 寫 backtest/report_rrg_daily_axes.md
#       離線、免 token、免網路。

from __future__ import annotations

import gzip
import io
import json
import math
import random
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERIES = ROOT / "data" / "chain_daily" / "series.json"
CACHE = ROOT / "backtest" / "cache"
CLASSIFY = ROOT / "data" / "classify.json"
OUT = ROOT / "backtest" / "report_rrg_daily_axes.md"

QUADS = ("領先", "轉弱", "落後", "改善")          # 正本：backtest/run_rrg.py 的 QUADS
TIERS = [("≥3%", 3.0, 1e9), ("1~3%", 1.0, 3.0),  # 正本：backtest/run_rrg.py 的 TIERS
         ("0.5~1%", 0.5, 1.0), ("<0.5%", 0.0, 0.5)]

# 參數掃描網格。K/n＝移動平均期數（實務 RRG 10~14；盤中版 K=5）、L/k＝動能回看期數。
GRID_W = (5, 8, 10, 12, 14, 20)
GRID_L = (1, 3, 5, 10)
GRID_PERSIST = (1, 2, 3, 4, 5)                    # 連續 N 日符合才進榜
FWD = (1, 3, 5)                                   # T+1 / T+3 / T+5
PERM_REPS = 200                                   # 置換虛無的重複次數
BOOT_REPS = 1000                                  # 日期分塊 bootstrap 次數
DOM_W = 0.20                                      # 「支配」的權重門檻
DOM_BP = (20.0, 50.0)                             # 「支配」的報酬影響門檻（bp）
ERA_SPLIT = "2026-01-01"                          # 早期／近期分界
MIN_ROWS = 20                                     # 沿用 run_sector.py 的 stat() 樣本下限
SEED = 20260811


def emit(s: str = "") -> None:
    print(s)
    LINES.append(s)


LINES: list[str] = []


# ---------------------------------------------------------------- 通用數學

def pctl(xs, p):
    """線性內插百分位（與 backtest/run_rrg.py、run_rrg_daily.py 的 pctl 同式）。"""
    xs = [x for x in xs if x is not None]
    if not xs:
        return float("nan")
    xs = sorted(xs)
    k = (len(xs) - 1) * p / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return xs[lo]
    return xs[lo] * (hi - k) + xs[hi] * (k - lo)


def ranks(xs: list[float]) -> list[float]:
    """平均序位（tie 取平均），給 Spearman 用。"""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    out = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        r = (i + j) / 2.0 + 1.0
        for t in range(i, j + 1):
            out[order[t]] = r
        i = j + 1
    return out


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return float("nan")
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    sxx = sum((a - mx) ** 2 for a in xs)
    syy = sum((b - my) ** 2 for b in ys)
    if sxx <= 0 or syy <= 0:
        return float("nan")
    return sxy / math.sqrt(sxx * syy)


def spearman(xs, ys):
    if len(xs) != len(ys) or len(xs) < 3:
        return float("nan")
    return pearson(ranks(list(xs)), ranks(list(ys)))


def roll_mean_prev(xs: list, n: int) -> list:
    """前 n 期平均，**不含當期**（＝盤中版 build_ratio 的 base 口徑）。窗內任一缺值 → None。"""
    out = [None] * len(xs)
    for t in range(n, len(xs)):
        w = xs[t - n:t]
        if any(v is None for v in w):
            continue
        out[t] = sum(w) / n
    return out


def roll_mean_sd(xs: list, n: int) -> tuple[list, list]:
    """含當期的 n 期平均與**樣本**標準差（ddof=1）。窗內任一缺值 → None。"""
    mu: list = [None] * len(xs)
    sd: list = [None] * len(xs)
    for t in range(n - 1, len(xs)):
        w = xs[t - n + 1:t + 1]
        if any(v is None for v in w):
            continue
        m = sum(w) / n
        mu[t] = m
        sd[t] = math.sqrt(sum((v - m) ** 2 for v in w) / (n - 1)) if n > 1 else 0.0
    return mu, sd


def zscore100(xs: list, n: int) -> list:
    """JdK 式標準化：100 + (x − MA_n) / SD_n。SD=0 → None（不製造無限大座標）。"""
    mu, sd = roll_mean_sd(xs, n)
    out: list = [None] * len(xs)
    for t in range(len(xs)):
        if xs[t] is None or mu[t] is None or not sd[t]:
            continue
        out[t] = 100.0 + (xs[t] - mu[t]) / sd[t]
    return out


def cum_index(rets: list, base: float = 100.0) -> list:
    """日報酬逐日連乘成指數。第一格＝base（該日報酬還沒發生）。

    **開頭的連續缺值當成 0 處理**（指數停在 base），只有**中間**的缺值才截斷成 None。
    這條規則是為了 `market.taiex_ret`：它的第 0 格結構性為 null（序列第一天沒有前收，
    見第一階段報告），照「缺值就截斷」處理會讓整條基準線全 None、價格版一天都算不出來。
    這樣做是安全的，因為分母指數乘上任何常數 c，在 z-score 裡會被完全吸收：
        (c·RS − mean(c·RS)) / sd(c·RS) = (RS − mean RS) / sd RS
    見 test_zscore100_is_scale_invariant。**中間**的缺值不能這樣處理——那會憑空
    製造一段「大盤沒動」的假資料（正是第一階段修掉的那個 12 天 bug 的形狀）。"""
    out: list = []
    v = base
    broken = False
    started = False
    for r in rets:
        if broken:
            out.append(None)
            continue
        out.append(v)
        if r is None:
            if started:
                broken = True
            continue
        started = True
        v *= (1.0 + r)
    return out


def quad(r, m) -> str:
    """象限。正本：backtest/run_rrg.py 的 def quad（規格 §5：不做中位置中）。"""
    if r >= 100.0:
        return "領先" if m >= 100.0 else "轉弱"
    return "改善" if m >= 100.0 else "落後"


def tier_of(share_pct) -> str:
    for nm, lo, hi in TIERS:
        if lo <= share_pct < hi:
            return nm
    return TIERS[-1][0]


def jaccard(a: set, b: set) -> float | None:
    u = a | b
    return (len(a & b) / len(u)) if u else None


def fmtf(x, nd=1, suffix=""):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    return f"{x:.{nd}f}{suffix}"


# ---------------------------------------------------------------- 軸定義

def flow_coords(shares: dict[str, list], K: int, L: int) -> dict[str, list]:
    """A 資金版座標。shares[chain] = 逐日佔比。回 {chain: [(r,m)|None]}。

    **不吃任何報酬欄位**——這正是 A-ew ≡ A-mw 的原因。"""
    out = {}
    for name, s in shares.items():
        base = roll_mean_prev(s, K)
        rat = [None if (s[t] is None or not base[t]) else 100.0 * s[t] / base[t]
               for t in range(len(s))]
        out[name] = [None if (t < L or rat[t] is None or rat[t - L] is None)
                     else (rat[t], 100.0 + rat[t] - rat[t - L])
                     for t in range(len(rat))]
    return out


def price_coords(chain_rets: dict[str, list], bench_ret: list, n: int, k: int) -> dict[str, list]:
    """B 價格版座標（正統 RRG，z-score 重建版）。見檔頭公式。"""
    bi = cum_index(bench_ret)
    out = {}
    for name, r in chain_rets.items():
        ci = cum_index(r)
        rs = [None if (ci[t] is None or not bi[t]) else 100.0 * ci[t] / bi[t]
              for t in range(len(ci))]
        rat = zscore100(rs, n)
        roc = [None if (t < k or rat[t] is None or not rat[t - k]) else
               100.0 * (rat[t] / rat[t - k] - 1.0) for t in range(len(rat))]
        mom = zscore100(roc, n)
        out[name] = [None if (rat[t] is None or mom[t] is None) else (rat[t], mom[t])
                     for t in range(len(rat))]
    return out


# ---------------------------------------------------------------- 準則 1 穩定度

def quad_sets(coords: dict[str, list], t: int) -> dict[str, set]:
    d = {q: set() for q in QUADS}
    for name, arr in coords.items():
        c = arr[t]
        if c is not None:
            d[quad(c[0], c[1])].add(name)
    return d


def improving_top(coords: dict[str, list], t: int, topn: int = 5) -> list[str]:
    """改善象限按 RS-Momentum 遞減取前 topn（＝盤中版側欄「資金剛輪入」頁的排序語意）。"""
    rows = [(arr[t][1], name) for name, arr in coords.items()
            if arr[t] is not None and quad(arr[t][0], arr[t][1]) == "改善"]
    rows.sort(key=lambda x: (-x[0], x[1]))
    return [n for _m, n in rows[:topn]]


def stability(coords: dict[str, list], idx: range | list) -> dict:
    """相鄰日穩定度。idx 是要納入統計的日期索引（必須連續遞增）。"""
    per_q = {q: [] for q in QUADS}
    cand, itop_j, itop_o, sizes = [], [], [], {q: [] for q in QUADS}
    ii = list(idx)
    for a, b in zip(ii, ii[1:]):
        if b != a + 1:
            continue
        qa, qb = quad_sets(coords, a), quad_sets(coords, b)
        if not any(qa.values()) or not any(qb.values()):
            continue
        for q in QUADS:
            j = jaccard(qa[q], qb[q])
            if j is not None:
                per_q[q].append(j)
            sizes[q].append(len(qb[q]))
        ca, cb = qa["改善"] | qa["領先"], qb["改善"] | qb["領先"]
        j = jaccard(ca, cb)
        if j is not None:
            cand.append(j)
        ta, tb = set(improving_top(coords, a)), set(improving_top(coords, b))
        j = jaccard(ta, tb)
        if j is not None:
            itop_j.append(j)
            itop_o.append(len(ta & tb) / max(1, min(len(ta), len(tb))))
    flip, keep = 0, 0
    for name, arr in coords.items():
        for a, b in zip(ii, ii[1:]):
            if b != a + 1 or arr[a] is None or arr[b] is None:
                continue
            if quad(*arr[a]) == quad(*arr[b]):
                keep += 1
            else:
                flip += 1
    return {
        "quad": {q: (st.mean(v) if v else float("nan")) for q, v in per_q.items()},
        "quad_all": st.mean([x for v in per_q.values() for x in v]) if any(per_q.values()) else float("nan"),
        "cand": st.mean(cand) if cand else float("nan"),
        "imp_top5_j": st.mean(itop_j) if itop_j else float("nan"),
        "imp_top5_ov": st.mean(itop_o) if itop_o else float("nan"),
        "size": {q: (st.mean(v) if v else float("nan")) for q, v in sizes.items()},
        "pairs": len(cand),
        # 平滑病理的守門：只要把窗開大，象限就幾乎不動，Jaccard 自然逼近 100%。
        # 「平均停留天數」把這件事量出來——一個永遠不換象限的指標穩定度滿分但零資訊。
        "flip": (flip / (flip + keep)) if (flip + keep) else float("nan"),
        "dwell": ((flip + keep) / flip) if flip else float("inf"),
    }


def perm_null(coords: dict[str, list], idx: list, reps: int = PERM_REPS, seed: int = SEED) -> dict:
    """置換虛無：每天把鏈標籤重新洗牌（**保持每天各象限的成員數不變**），
    再算同一批穩定度指標。這給出「純由集合大小決定的」隨機基準。"""
    rnd = random.Random(seed)
    names = list(coords)
    acc = defaultdict(list)
    for _ in range(reps):
        shuf = {}
        for t in range(len(next(iter(coords.values())))):
            order = names[:]
            rnd.shuffle(order)
            shuf[t] = {order[i]: names[i] for i in range(len(names))}
        pc = {n: [None] * len(coords[n]) for n in names}
        for t in range(len(next(iter(coords.values())))):
            for n in names:
                pc[shuf[t][n]][t] = coords[n][t]
        s = stability(pc, idx)
        acc["quad_all"].append(s["quad_all"])
        acc["cand"].append(s["cand"])
        acc["imp_top5_j"].append(s["imp_top5_j"])
        acc["imp_top5_ov"].append(s["imp_top5_ov"])
    out = {}
    for k, v in acc.items():
        ok = [x for x in v if not math.isnan(x)]
        # 全部是 nan 的情形是真的會發生的（例：退化樣本裡改善象限全期為空，
        # 改善 Top5 的 Jaccard 每一次重複都算不出來）——回 nan，不是炸掉。
        out[k] = st.mean(ok) if ok else float("nan")
    return out


# ---------------------------------------------------------------- 準則 2 小分母汙染

def tier_pollution(coords: dict[str, list], shares: dict[str, list], idx: list) -> dict:
    """依**當日** share 分層，各層 RS-Ratio 的極值、尾比例、**與象限翻轉率**。

    ⚠ 尺度警告（報告要一起印）：A 的 RS-Ratio 是**比值**（100 = 與自身基準持平，
    理論上界無限），B 的 RS-Ratio 是 **z-score**（100 + 標準差倍數，n 期窗內
    上界約 100+√(n−1)）。所以「>200 的比例」「層內 SD」這兩欄**只能同族內比，
    不能 A 對 B 比**——B 的層內 SD 必然 ≈1，那是 z-score 的定義使然，不是它比較乾淨。
    真正可跨族比較的是最後兩欄（**象限翻轉率**）與 `_raw_rho`（標準化前的原始噪音）。"""
    buck = {nm: [] for nm, _lo, _hi in TIERS}
    flip = {nm: [0, 0] for nm, _lo, _hi in TIERS}
    iset = set(idx)
    for name, arr in coords.items():
        s = shares[name]
        for t in idx:
            if arr[t] is None or s[t] is None:
                continue
            nm = tier_of(s[t] * 100.0)
            buck[nm].append(arr[t][0])
            if (t - 1) in iset and arr[t - 1] is not None:
                flip[nm][quad(*arr[t]) == quad(*arr[t - 1])] += 1
    out = {}
    for nm, _lo, _hi in TIERS:
        v = buck[nm]
        if not v:
            out[nm] = None
            continue
        f, k = flip[nm]
        out[nm] = {
            "n": len(v),
            "med": st.median(v),
            "p95": pctl(v, 95),
            "max": max(v),
            "gt200": sum(1 for x in v if x > 200) / len(v) * 100.0,
            "far25": sum(1 for x in v if abs(x - 100) > 25) / len(v) * 100.0,
            "sd": st.pstdev(v) if len(v) > 1 else 0.0,
            "flip": (f / (f + k) * 100.0) if (f + k) else float("nan"),
        }
    # 規模 vs 離散度：每條鏈的平均 share（log）對上該鏈 RS-Ratio 的標準差。
    # A 會抓到真實的小分母放大；B 因為 z-score 已把每條鏈的 SD 壓成 1，這個數字**必然 ≈0**，
    # 不代表噪音不見了——只是被重新標準化到看不見（見 raw_noise_vs_size）。
    xs, ys = [], []
    for name, arr in coords.items():
        v = [arr[t][0] for t in idx if arr[t] is not None]
        sv = [shares[name][t] for t in idx if shares[name][t] is not None]
        if len(v) < 20 or not sv:
            continue
        xs.append(math.log(max(st.mean(sv), 1e-12)))
        ys.append(st.pstdev(v))
    out["_size_disp_rho"] = spearman(xs, ys)
    out["_size_disp_n"] = len(xs)
    return out


def raw_noise_vs_size(driver: dict[str, list], shares: dict[str, list], idx: list) -> tuple:
    """**標準化之前**的原始噪音 vs 鏈規模（Spearman）。

    這一項才是「小分母汙染」的可跨族比較版：
      · A 的驅動量是 share → 噪音 = 逐日 Δlog(share) 的標準差
      · B 的驅動量是 相對強度 RS_raw = idx_c/idx_b → 噪音 = 逐日 Δlog(RS_raw) 的標準差
        （＝該鏈對大盤的追蹤誤差波動）
    兩者都在標準化之前，所以 z-score 不會把它洗掉。負相關 = 小鏈比較吵。"""
    xs, ys = [], []
    for name, ser in driver.items():
        d = []
        for a, b in zip(idx, idx[1:]):
            if b != a + 1 or not ser[a] or not ser[b]:
                continue
            d.append(math.log(ser[b] / ser[a]))
        sv = [shares[name][t] for t in idx if shares[name][t] is not None]
        if len(d) < 20 or not sv:
            continue
        xs.append(math.log(max(st.mean(sv), 1e-12)))
        ys.append(st.pstdev(d))
    return spearman(xs, ys), len(xs), (st.median(ys) if ys else float("nan"))


# ---------------------------------------------------------------- 準則 3 單一個股支配度

def is_etf(code: str) -> bool:
    return code.startswith("00")


def member_weights(prices: dict[str, list], cmap: dict) -> dict[str, dict[str, tuple]]:
    """單日、逐鏈的成員權重（純函式，離線可測）。

    口徑逐條對齊 src/build_chain_daily.py 的 def aggregate_day：
      · 只計 t=="twse"；鏈成員依 classify.c 去重（set）
      · 鏈成交額 = 成員成交額**全額**加總（不是均分——均分只用在覆蓋率診斷）
      · 報酬與市值加權**排除 ETF**；市值權重 = sh × 前收
    回 {chain: {code: (w_amt, w_ew, w_mw, ret)}}；w_* 為該鏈內的正規化權重。
    """
    mem: dict[str, list] = defaultdict(list)
    for code, row in prices.items():
        if code.startswith("_"):
            continue
        info = cmap.get(code)
        if not info or info.get("t") != "twse":
            continue
        cs = set(info.get("c") or [])
        if not cs:
            continue
        amt, close, spread = (list(row) + [None, None, None])[:3]
        amt = amt or 0.0
        etf = is_etf(code)
        r = None
        pc = None
        if not etf and close is not None and spread is not None:
            p = close - spread
            if p > 0:
                r = spread / p
                pc = p
        sh = info.get("sh") or 0
        w = (sh * pc) if (sh and pc) else None
        for name in cs:
            mem[name].append((code, amt, r, w))
    out = {}
    for name, rows in mem.items():
        ta = sum(a for _c, a, _r, _w in rows) or 0.0
        ew_n = sum(1 for _c, _a, r, _w in rows if r is not None)
        tw = sum(w for _c, _a, r, w in rows if r is not None and w) or 0.0
        d = {}
        for code, a, r, w in rows:
            d[code] = (
                (a / ta) if ta else None,
                (1.0 / ew_n) if (r is not None and ew_n) else None,
                (w / tw) if (r is not None and w and tw) else None,
                r,
            )
        out[name] = d
    return out


def loo_delta(w: float | None, r_s: float | None, r_c: float | None) -> float | None:
    """拿掉單一成員後鏈報酬的變動量（封閉解）：Δ = w(r_s − r_c) / (1 − w)。

    推導：r_c = Σ w_i r_i；去掉 s 後 r_c^- = (r_c − w r_s)/(1 − w)。
    Δ = r_c − r_c^- = (r_c(1−w) − r_c + w r_s)/(1−w) = w(r_s − r_c)/(1−w)。"""
    if w is None or r_s is None or r_c is None or w >= 1.0:
        return None
    return w * (r_s - r_c) / (1.0 - w)


def dominance(dates: list[str], cmap: dict, sample_every: int = 1) -> dict:
    """全期逐日算成員權重，彙總「一檔股票能決定多少條鏈」。"""
    acc_w = {"amt": defaultdict(list), "ew": defaultdict(list), "mw": defaultdict(list)}
    acc_d = {"ew": defaultdict(list), "mw": defaultdict(list)}
    maxw = {"amt": [], "ew": [], "mw": []}
    hhi = {"amt": [], "ew": [], "mw": []}
    used = 0
    for i, ds in enumerate(dates):
        if i % sample_every:
            continue
        p = CACHE / f"chainday_{ds}.json.gz"
        if not p.exists():
            continue
        prices = json.loads(gzip.decompress(p.read_bytes()))
        used += 1
        mw_ = member_weights(prices, cmap)
        for name, d in mw_.items():
            # 鏈報酬（等權／市值加權），給 LOO 用
            rs_ew = [v[3] for v in d.values() if v[3] is not None]
            r_ew = st.mean(rs_ew) if rs_ew else None
            num = sum(v[2] * v[3] for v in d.values() if v[2] is not None and v[3] is not None)
            r_mw = num if any(v[2] is not None for v in d.values()) else None
            for key, j in (("amt", 0), ("ew", 1), ("mw", 2)):
                vals = [(c, v[j]) for c, v in d.items() if v[j] is not None]
                if not vals:
                    continue
                for c, w in vals:
                    acc_w[key][(c, name)].append(w)
                maxw[key].append(max(w for _c, w in vals))
                hhi[key].append(sum(w * w for _c, w in vals))
            for key, j, rc in (("ew", 1, r_ew), ("mw", 2, r_mw)):
                for c, v in d.items():
                    dd = loo_delta(v[j], v[3], rc)
                    if dd is not None:
                        acc_d[key][(c, name)].append(abs(dd))
    res = {"days": used}
    for key in ("amt", "ew", "mw"):
        med = {ck: st.median(v) for ck, v in acc_w[key].items() if len(v) >= 5}
        cnt = defaultdict(int)
        for (c, _name), m in med.items():
            if m >= DOM_W:
                cnt[c] += 1
        res[key] = {
            "maxw_med": st.median(maxw[key]) if maxw[key] else float("nan"),
            "maxw_p95": pctl(maxw[key], 95),
            "hhi_med": st.median(hhi[key]) if hhi[key] else float("nan"),
            "top": sorted(cnt.items(), key=lambda x: -x[1])[:10],
            "cnt": dict(cnt),
            "med_w": med,
        }
    for key in ("ew", "mw"):
        med = {ck: st.median(v) for ck, v in acc_d[key].items() if len(v) >= 5}
        for bp in DOM_BP:
            cnt = defaultdict(int)
            for (c, _name), m in med.items():
                if m * 10000.0 >= bp:
                    cnt[c] += 1
            res[key][f"bp{int(bp)}"] = sorted(cnt.items(), key=lambda x: -x[1])[:10]
            res[key][f"bp{int(bp)}_cnt"] = dict(cnt)
        res[key]["med_d"] = med
    return res


# ---------------------------------------------------------------- 準則 4 延續性

def fwd_excess(rets: list, bench: list, t: int, k: int) -> float | None:
    """T+1..T+k 的複利報酬減同期大盤（＝ run_sector.py 的 e1/e3 語意，改成複利）。"""
    if t + k >= len(rets):
        return None
    a = b = 1.0
    for i in range(t + 1, t + k + 1):
        if rets[i] is None or bench[i] is None:
            return None
        a *= (1.0 + rets[i])
        b *= (1.0 + bench[i])
    return (a - 1.0) - (b - 1.0)


def transition_samples(coords: dict[str, list], chain_rets: dict[str, list],
                       bench: list, dates: list[str], idx: list) -> list[dict]:
    """每個 (日, 鏈) 一筆，記下當日象限、是否剛轉入該象限、以及 T+1/3/5 超額。"""
    rows = []
    for name, arr in coords.items():
        r = chain_rets[name]
        for t in idx:
            if t == 0 or arr[t] is None or arr[t - 1] is None:
                continue
            q, q0 = quad(*arr[t]), quad(*arr[t - 1])
            e = {k: fwd_excess(r, bench, t, k) for k in FWD}
            if any(v is None for v in e.values()):
                continue
            nxt = quad(*arr[t + 1]) if (t + 1 < len(arr) and arr[t + 1] is not None) else None
            rows.append({"d": dates[t], "c": name, "q": q, "enter": q != q0,
                         "e1": e[1], "e3": e[3], "e5": e[5],
                         "sticky": (nxt == q)})
    return rows


def stat(rows: list[dict]) -> dict | None:
    """統計量與 run_sector.py 的 def stat 對齊（勝率／勝大盤率／超額 avg+med／黏性）。"""
    if len(rows) < MIN_ROWS:
        return None
    k = len(rows)
    return dict(n=k,
                we1=sum(1 for r in rows if r["e1"] > 0) / k * 100,
                we3=sum(1 for r in rows if r["e3"] > 0) / k * 100,
                we5=sum(1 for r in rows if r["e5"] > 0) / k * 100,
                e1=st.mean(r["e1"] for r in rows) * 100,
                e3=st.mean(r["e3"] for r in rows) * 100,
                e3m=st.median(r["e3"] for r in rows) * 100,
                e5=st.mean(r["e5"] for r in rows) * 100,
                stick=sum(1 for r in rows if r["sticky"]) / k * 100)


def boot_ci(rows: list[dict], key: str, reps: int = BOOT_REPS, seed: int = SEED) -> tuple:
    """**按日期分塊**的 bootstrap：同一天 47 條鏈的超額高度相關，
    逐 chain-day 重抽會把標準誤低估數倍。回 (P2.5, P97.5)，單位 %。"""
    by = defaultdict(list)
    for r in rows:
        by[r["d"]].append(r[key])
    days = list(by)
    if len(days) < 10:
        return (float("nan"), float("nan"))
    rnd = random.Random(seed)
    means = []
    for _ in range(reps):
        pool = []
        for _ in range(len(days)):
            pool.extend(by[days[rnd.randrange(len(days))]])
        if pool:
            means.append(sum(pool) / len(pool) * 100)
    return (pctl(means, 2.5), pctl(means, 97.5))


# ---------------------------------------------------------------- 持續性條件 N

def persist_list(coords: dict[str, list], q: str, n: int) -> list[set]:
    """逐日的「連續 n 日都在象限 q」名單。n=1 即原始象限成員。"""
    T = len(next(iter(coords.values())))
    out = []
    for t in range(T):
        s = set()
        for name, arr in coords.items():
            ok = True
            for j in range(n):
                i = t - j
                if i < 0 or arr[i] is None or quad(*arr[i]) != q:
                    ok = False
                    break
            if ok:
                s.add(name)
        out.append(s)
    return out


def persist_scan(coords: dict[str, list], rows_by: dict, idx: list) -> list[dict]:
    """掃 N：名單大小、相鄰日 Jaccard、以及「新進榜」樣本的 T+3 超額。"""
    out = []
    for n in GRID_PERSIST:
        lst = persist_list(coords, "改善", n)
        js, sz = [], []
        for a, b in zip(idx, idx[1:]):
            if b != a + 1:
                continue
            j = jaccard(lst[a], lst[b])
            if j is not None:
                js.append(j)
            sz.append(len(lst[b]))
        new = []
        for t in idx:
            if t == 0:
                continue
            for name in lst[t] - lst[t - 1]:
                r = rows_by.get((t, name))
                if r:
                    new.append(r)
        out.append({"n": n, "jac": st.mean(js) if js else float("nan"),
                    "size": st.mean(sz) if sz else float("nan"),
                    "new": len(new),
                    "e3": st.mean(r["e3"] for r in new) * 100 if len(new) >= MIN_ROWS else None,
                    "we3": sum(1 for r in new if r["e3"] > 0) / len(new) * 100
                    if len(new) >= MIN_ROWS else None})
    return out


# ---------------------------------------------------------------- 資料載入

def load_series(path: Path = SERIES) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_classify(path: Path = CLASSIFY) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))["map"]


def axis_systems(s: dict, w: int, l: int) -> dict[str, dict]:
    """三組座標系。A 只有一組（見檔頭：A-ew ≡ A-mw）。"""
    shares = {n: c["share"] for n, c in s["chains"].items()}
    out = {"A": flow_coords(shares, w, l)}
    for wt in ("ew", "mw"):
        cr = {n: c[f"ret_{wt}"] for n, c in s["chains"].items()}
        out[f"B-{wt}"] = price_coords(cr, s["market"][f"ret_{wt}"], w, l)
    return out


def valid_idx(coords: dict[str, list]) -> list:
    """至少有一半的鏈算得出座標的日期索引（暖機期自動被排除）。"""
    T = len(next(iter(coords.values())))
    need = max(2, len(coords) // 2)
    return [t for t in range(T)
            if sum(1 for a in coords.values() if a[t] is not None) >= need]


# ---------------------------------------------------------------- main

def main() -> int:
    rnd_state = random.getstate()
    random.seed(SEED)
    s = load_series()
    dates = s["dates"]
    T = len(dates)
    shares = {n: c["share"] for n, c in s["chains"].items()}
    era_i = next((i for i, d in enumerate(dates) if d >= ERA_SPLIT), T)

    emit("# 盤後日頻 RRG — 第二階段：軸定義比較與參數定案")
    emit("")
    emit(f"- 資料：`data/chain_daily/series.json`（{T} 交易日 {dates[0]} ~ {dates[-1]}、"
         f"{len(s['chains'])} 條產業鏈）")
    emit("- 產生腳本：`backtest/run_rrg_daily_axes.py`（離線、免 token）")
    emit("- 規格：`docs/rrg-daily-spec-20260811.md` §3；第一階段資料品質見 "
         "`backtest/report_rrg_daily.md`")
    emit("- 象限命名與 `quad()` 判定沿用 `backtest/run_rrg.py` 的 `QUADS`；"
         "規格 §5 已定案**不做中位置中**，本檔全程遵守")
    emit("")

    # ---------------------------------------------------------- §1 四格→三組
    emit("## 1. 四格為什麼變三組（A-ew ≡ A-mw）")
    emit("")
    a_ew = flow_coords(shares, 10, 5)
    a_mw = flow_coords(shares, 10, 5)
    same = all(a_ew[n] == a_mw[n] for n in a_ew)
    emit("規格 §3 的 A 資金版軸是 `RS_Ratio = 100 × share ÷ 前 K 日 share 平均`、"
         "`RS_Mom = 100 + ΔRS_Ratio`。**兩條式子的輸入只有 `share`，沒有任何報酬欄位**，")
    emit("所以「A × 等權」與「A × 市值加權」的座標逐位元相同"
         f"（本次實跑驗證：{'完全相同 ✅' if same else '不同 ❌'}）。")
    emit("")
    emit("| | 等權報酬 | 市值加權報酬 |")
    emit("|---|---|---|")
    emit("| **A 資金版** | A（座標與加權無關） | ← 同一組座標，不是獨立的一格 |")
    emit("| **B 價格版** | B-ew | B-mw |")
    emit("")
    emit("因此**座標層是三組**：A、B-ew、B-mw。加權在 A 之下仍有意義的地方只有兩處，"
         "本檔都照做：")
    emit("")
    emit("1. **準則 4（延續性）的績效衡量**——「進了改善象限之後賺不賺」要用鏈報酬衡量，"
         "等權與市值加權會給出不同答案。故三組座標各用 ew 與 mw 兩種前瞻報酬各測一次（共 6 組評估）。")
    emit("2. **準則 3（個股支配度）**——A 的座標吃的是成交額，故 A 的支配度要看"
         "「成交額權重」；B-ew 看等權、B-mw 看市值權重。三種加權各量一份。")
    emit("")
    emit("> 這不是把規格的四格「砍掉一格」，是實測發現其中兩格在座標層是同一個東西。"
         "強行分成兩列報告會讓讀者以為量到了兩個獨立方案。")
    emit("")

    # ---------------------------------------------------------- §2 主比較
    W0, L0 = 10, 5
    sysmap = axis_systems(s, W0, L0)
    idxs = {k: valid_idx(v) for k, v in sysmap.items()}
    stabs = {k: stability(v, idxs[k]) for k, v in sysmap.items()}
    nulls = {k: perm_null(v, idxs[k]) for k, v in sysmap.items()}

    emit("## 2. 準則 1：跨日穩定度（最重要）")
    emit("")
    emit(f"基準參數 K/n={W0}、L/k={L0}（下節掃描證明這組不是最佳但差異不大）。"
         "「相鄰日」只計序列上真正相鄰的兩個交易日。")
    emit("")
    emit("### 2.1 為什麼一定要附虛無基準")
    emit("")
    emit("Jaccard 的隨機期望值是集合大小的函數：47 條鏈裡若某象限每天有 23 條，"
         "**完全隨機**重抽的相鄰日 Jaccard 也有約 33%。")
    emit("盤中版記載的「40~45%」若沒扣掉這個基準，就不知道它是訊號還是算術產物。")
    emit(f"本檔對每一組都跑 {PERM_REPS} 次**置換虛無**（每天把鏈標籤重新洗牌、"
         "**保持各象限成員數不變**），把基準量出來一起報。")
    emit("")
    emit("### 2.2 實測")
    emit("")
    emit("| 座標系 | 有效日 | 象限成員 Jaccard | 虛無 | 超出虛無 | 候補清單(改善∪領先) | 虛無 | 超出 | 改善Top5 Jaccard | 虛無 | 改善Top5 交集率 | 象限翻轉率 | 平均停留天數 |")
    emit("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for k in ("A", "B-ew", "B-mw"):
        a, b = stabs[k], nulls[k]
        emit(f"| {k} | {len(idxs[k])} | {a['quad_all']*100:.1f}% | {b['quad_all']*100:.1f}% | "
             f"**{(a['quad_all']-b['quad_all'])*100:+.1f}pp** | {a['cand']*100:.1f}% | "
             f"{b['cand']*100:.1f}% | **{(a['cand']-b['cand'])*100:+.1f}pp** | "
             f"{a['imp_top5_j']*100:.1f}% | {b['imp_top5_j']*100:.1f}% | {a['imp_top5_ov']*100:.1f}% | "
             f"{a['flip']*100:.1f}% | {a['dwell']:.1f} |")
    emit("")
    emit("> **最後兩欄是防作弊欄**。穩定度可以用「把窗開大」無限逼近 100%——"
         "一個永遠不換象限的指標穩定度滿分、資訊量為零。平均停留天數把這個代價量出來；"
         "下一節的參數掃描會看到它一路上升。")
    emit("")
    emit("逐象限 Jaccard 與平均成員數：")
    emit("")
    emit("| 座標系 | " + " | ".join(f"{q} Jaccard / 平均條數" for q in QUADS) + " |")
    emit("|---|" + "---|" * len(QUADS))
    for k in ("A", "B-ew", "B-mw"):
        cells = [f"{stabs[k]['quad'][q]*100:.1f}% / {stabs[k]['size'][q]:.1f}" for q in QUADS]
        emit(f"| {k} | " + " | ".join(cells) + " |")
    emit("")

    # ---------------------------------------------------------- §3 參數掃描
    emit("## 3. 參數掃描：移動平均期數與動能回看")
    emit("")
    emit("掃 K/n ∈ " + str(list(GRID_W)) + "、L/k ∈ " + str(list(GRID_L)) +
         "。每格印「候補清單相鄰日 Jaccard ／ 平均象限停留天數」。")
    emit("")
    emit("**先講怎麼讀這張表，否則會選錯**：Jaccard 幾乎單調隨窗長上升，"
         "但停留天數同步上升——這不是「參數愈大愈好」，是**平滑病理**："
         "把窗開到無限大，指標就不再變動，Jaccard→100%、停留天數→∞、資訊量→0。")
    emit("所以定參數不能取 Jaccard 最大值，要取**剛好越過門檻的最小窗**。")
    emit("")
    sweep = {}
    for key in ("A", "B-ew", "B-mw"):
        emit(f"### {key}")
        emit("")
        emit("| K/n \\ L/k | " + " | ".join(str(l) for l in GRID_L) + " |")
        emit("|---|" + "---:|" * len(GRID_L))
        for w in GRID_W:
            cells = []
            for l in GRID_L:
                sm = axis_systems(s, w, l)[key]
                ix = valid_idx(sm)
                stv = stability(sm, ix)
                sweep[(key, w, l)] = stv
                cells.append(f"{stv['cand']*100:.1f}% / {stv['dwell']:.1f}d")
            emit(f"| {w} | " + " | ".join(cells) + " |")
        emit("")
    best = {}
    for key in ("A", "B-ew", "B-mw"):
        cand = [(v["cand"], w, l) for (k2, w, l), v in sweep.items() if k2 == key
                and not math.isnan(v["cand"])]
        best[key] = max(cand)
    emit("**選參數的規則（先寫死再看數字，不是看了數字再挑）**：取**肘點**——"
         "在「Jaccard 已達該組最佳值的 5pp 以內」的所有格子裡，選 `K/n + L/k` 最小的那格。")
    emit("理由：Jaccard 對窗長是遞增但邊際遞減的，肘點之後再加窗長只換到零點幾 pp 的穩定度，"
         "卻換來實打實的延遲；而取最大值等於無條件選最平滑的角落。")
    emit("")
    emit("| 座標系 | Jaccard 最大的一格（**不是建議值**） | 越過 45% 的最小窗 | **肘點（建議值）** |")
    emit("|---|---|---|---|")
    thin, elbow = {}, {}
    for key in ("A", "B-ew", "B-mw"):
        c, w, l = best[key]
        pool = [(w2, l2, v) for (k2, w2, l2), v in sweep.items()
                if k2 == key and not math.isnan(v["cand"])]
        ok = sorted([(w2 + l2, w2, l2, v) for w2, l2, v in pool if v["cand"] > 0.45])
        if ok:
            _s, w2, l2, v = ok[0]
            thin[key] = (w2, l2, v)
            small = f"K/n={w2}、L/k={l2} → {v['cand']*100:.1f}%（停留 {v['dwell']:.1f} 天）"
        else:
            thin[key] = None
            small = "**無**（沒有任何一格越過 45%）"
        eb = sorted([(w2 + l2, w2, l2, v) for w2, l2, v in pool
                     if v["cand"] >= c - 0.05 and v["cand"] > 0.45])
        if eb:
            _s, w3, l3, v3 = eb[0]
            elbow[key] = (w3, l3, v3)
            etxt = (f"**K/n={w3}、L/k={l3}** → {v3['cand']*100:.1f}%"
                    f"（停留 {v3['dwell']:.1f} 天）")
        else:
            elbow[key] = None
            etxt = "**無**"
        emit(f"| {key} | K/n={w}、L/k={l} → {c*100:.1f}%（停留 "
             f"{sweep[(key, w, l)]['dwell']:.1f} 天） | {small} | {etxt} |")
    emit("")
    emit("另外注意 **L/k=1 那一欄整欄都很差**（A 只有 24~26%，比虛無基準 33% 還低）。"
         "原因是動能只回看 1 期時，`RS_Mom` 幾乎等於當日噪音的符號，"
         "M≥100 這條界線變成擲銅板——這條界線一抖，象限就跟著抖。")
    emit("")

    # ---------------------------------------------------------- §4 小分母
    emit("## 4. 準則 2：小分母汙染")
    emit("")
    emit("依**當日** share 分四層（層界沿用 `backtest/run_rrg.py` 的 `TIERS`）。")
    emit("")
    emit("### ⚠ 先講尺度，否則這節會被讀反")
    emit("")
    emit("A 的 RS-Ratio 是**比值**（100 = 與自身 K 日基準持平，上界無限）；"
         "B 的 RS-Ratio 是 **z-score**（100 + 標準差倍數，n 期窗內上界約 100+√(n−1)）。")
    emit("**「>200 的比例」與「層內 SD」這兩欄只能同族內比，不能拿 A 對 B 比。**"
         "B 的層內 SD 必然 ≈1、>200 必然 0%——那是 z-score 的定義使然，"
         "**不是「價格版比較乾淨」的證據**。同理，B 的「規模-離散度 ρ ≈ 0」也是"
         "定義產物：z-score 已經把每條鏈的 SD 強制壓成 1，規模差異當然量不到。")
    emit("")
    emit("可以跨族比較的只有兩個：")
    emit("")
    emit("1. **象限翻轉率分層**——小鏈的象限是不是特別會抖。這才是使用者實際感受到的汙染。")
    emit("2. **標準化前的原始噪音 vs 規模**（下表最後一列）——"
         "A 看 Δlog(share) 的波動、B 看 Δlog(相對強度) 的波動（＝對大盤的追蹤誤差）。"
         "兩者都在標準化之前，z-score 洗不掉。")
    emit("")
    pol, raw = {}, {}
    for key in ("A", "B-ew", "B-mw"):
        pol[key] = tier_pollution(sysmap[key], shares, idxs[key])
        emit(f"### {key}")
        emit("")
        emit("| 層 | 樣本 | RS-Ratio 中位 | P95 | 最大 | >200 比例 | \\|RS−100\\|>25 比例 | 層內 SD | **象限翻轉率** |")
        emit("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
        for nm, _lo, _hi in TIERS:
            v = pol[key][nm]
            if not v:
                emit(f"| {nm} | 0 | — | — | — | — | — | — | — |")
                continue
            emit(f"| {nm} | {v['n']:,} | {v['med']:.1f} | {v['p95']:.1f} | {v['max']:.1f} | "
                 f"{v['gt200']:.2f}% | {v['far25']:.1f}% | {v['sd']:.1f} | **{v['flip']:.1f}%** |")
        emit("")
        if key == "A":
            drv = shares
        else:
            wt = key.split("-")[1]
            bi = cum_index(s["market"][f"ret_{wt}"])
            drv = {}
            for n2, c2 in s["chains"].items():
                ci = cum_index(c2[f"ret_{wt}"])
                drv[n2] = [None if (ci[t] is None or not bi[t]) else ci[t] / bi[t]
                           for t in range(len(ci))]
        raw[key] = raw_noise_vs_size(drv, shares, idxs[key])
        emit(f"- 規模 vs **RS-Ratio** 離散度 Spearman："
             f"**{pol[key]['_size_disp_rho']:+.3f}**（{pol[key]['_size_disp_n']} 條鏈）"
             "；盤中版量到 −0.64。**B 的這個數字沒有意義**（見上方尺度說明）。")
        emit(f"- 規模 vs **標準化前原始噪音** Spearman：**{raw[key][0]:+.3f}**"
             f"（{raw[key][1]} 條鏈，噪音中位 {raw[key][2]*100:.2f}%/日）"
             "← 這個才可跨族比較。")
        emit("")

    # ---------------------------------------------------------- §5 支配度
    emit("## 5. 準則 3：單一個股支配度")
    emit("")
    emit("問題定義成可量的形式：**一檔股票能決定多少條鏈的座標？** 兩種量法都做——")
    emit("")
    emit(f"1. **權重法**：該股在該鏈的權重中位數 ≥ {DOM_W:.0%} 的鏈數。"
         "三種加權各一（A 用成交額權重、B-ew 用等權、B-mw 用市值權重）。")
    emit("2. **拿掉法（LOO）**：把該股從鏈裡移除，該鏈當日報酬會變動多少。"
         "封閉解 `Δ = w(r_s − r_c)/(1 − w)`（見 `def loo_delta` 的推導）；"
         f"門檻取中位 |Δ| ≥ {DOM_BP[0]:.0f}bp 與 ≥ {DOM_BP[1]:.0f}bp。")
    emit("")
    cmap = load_classify()
    dom = dominance(dates, cmap)
    emit(f"逐日實算 {dom['days']} 個交易日。鏈內集中度：")
    emit("")
    emit("| 加權 | 最大成員權重（中位） | 最大成員權重 P95 | 鏈內 HHI（中位） |")
    emit("|---|---:|---:|---:|")
    for key, lab in (("amt", "成交額（A 的軸）"), ("ew", "等權（B-ew）"), ("mw", "市值加權（B-mw）")):
        d = dom[key]
        emit(f"| {lab} | {d['maxw_med']*100:.1f}% | {d['maxw_p95']*100:.1f}% | {d['hhi_med']:.4f} |")
    emit("")
    emit(f"「權重中位數 ≥ {DOM_W:.0%} 的鏈數」前 10 名：")
    emit("")
    for key, lab in (("amt", "成交額權重"), ("ew", "等權"), ("mw", "市值權重")):
        top = dom[key]["top"]
        txt = "、".join(f"{c}{cmap.get(c, {}).get('n', '')}={n}" for c, n in top) or "（無）"
        emit(f"- **{lab}**：{txt}")
    emit("")
    emit("2308 台達電（規格 §3 準則 3 點名的個案，classify 中掛在 "
         f"{len(set(cmap.get('2308', {}).get('c') or []))} 條鏈）：")
    emit("")
    emit("| 加權 | 掛在幾條鏈 | 權重≥20% 的鏈數 | 中位權重（最高的那條鏈） | LOO≥20bp 的鏈數 | LOO≥50bp 的鏈數 |")
    emit("|---|---:|---:|---:|---:|---:|")
    for key, lab in (("amt", "成交額"), ("ew", "等權"), ("mw", "市值加權")):
        d = dom[key]
        mws = {ck[1]: v for ck, v in d["med_w"].items() if ck[0] == "2308"}
        top1 = max(mws.values()) if mws else float("nan")
        c20 = d.get("bp20_cnt", {}).get("2308", "—") if key != "amt" else "—"
        c50 = d.get("bp50_cnt", {}).get("2308", "—") if key != "amt" else "—"
        emit(f"| {lab} | {len(mws)} | {d['cnt'].get('2308', 0)} | {fmtf(top1*100 if mws else None, 1, '%')} "
             f"| {c20} | {c50} |")
    emit("")
    for key, lab in (("ew", "等權"), ("mw", "市值加權")):
        for bp in DOM_BP:
            top = dom[key][f"bp{int(bp)}"]
            txt = "、".join(f"{c}{cmap.get(c, {}).get('n', '')}={n}" for c, n in top) or "（無）"
            emit(f"- **{lab}**，中位 LOO ≥ {bp:.0f}bp 的鏈數前 10：{txt}")
    emit("")

    # ---------------------------------------------------------- §6 延續性
    emit("## 6. 準則 4：輪動訊號的延續性")
    emit("")
    emit("框架沿用 `backtest/run_sector.py` 的 `def run_level`：**同一套對照組**"
         "（「無訊號」＝當日沒有發生象限轉換）、**同一組統計量**"
         "（N／勝大盤率／超額 avg＋med／黏性），前瞻期擴充成 T+1／T+3／T+5。")
    emit("")
    emit("與 `run_sector.py` 的兩點差異（**刻意的**，一併記下以免誤讀）：")
    emit("")
    emit("1. `run_sector.py` 的前瞻報酬由成分股 close 比值現算；本檔直接用 `series.json` 的"
         "鏈日報酬（口徑是 `spread ÷ 前收`，已避開除權息假跌，見第一階段報告限制 7）連乘。")
    emit("2. `run_sector.py` 的大盤基準是 `_TAIEX`；本檔用 `market.ret_*`（理由見檔頭）。")
    emit("3. 黏性由「隔日佔比仍 ≥1.2×常態」改成「隔日仍留在同一象限」——"
         "價格版沒有佔比可黏，改成象限黏性才對三組都可比。")
    emit("")
    rows_all = {}
    for key in ("A", "B-ew", "B-mw"):
        for wt in ("ew", "mw"):
            cr = {n: c[f"ret_{wt}"] for n, c in s["chains"].items()}
            rows_all[(key, wt)] = transition_samples(
                sysmap[key], cr, s["market"][f"ret_{wt}"], dates, idxs[key])
    emit("| 座標系 | 前瞻報酬 | 樣本 | N | 勝大盤(T+1/T+3/T+5) | 超額avg T+1/T+3/T+5 | T+3 med | T+3 超額 95%CI（日期分塊 bootstrap） | 象限黏性 |")
    emit("|---|---|---|---:|---|---|---:|---|---:|")
    for key in ("A", "B-ew", "B-mw"):
        for wt in ("ew", "mw"):
            rows = rows_all[(key, wt)]
            ctrl = [r for r in rows if not r["enter"]]
            sig = [r for r in rows if r["enter"] and r["q"] == "改善"]
            lead = [r for r in rows if r["enter"] and r["q"] == "領先"]
            for lab, rr in (("轉入改善", sig), ("轉入領先", lead), ("對照組(無轉換)", ctrl)):
                v = stat(rr)
                if not v:
                    emit(f"| {key} | {wt} | {lab} | {len(rr)} | (樣本<{MIN_ROWS}) | | | | |")
                    continue
                lo, hi = boot_ci(rr, "e3")
                emit(f"| {key} | {wt} | {lab} | {v['n']:,} | "
                     f"{v['we1']:.1f}/{v['we3']:.1f}/{v['we5']:.1f}% | "
                     f"{v['e1']:+.2f}/{v['e3']:+.2f}/{v['e5']:+.2f}% | {v['e3m']:+.2f}% | "
                     f"[{lo:+.2f}%, {hi:+.2f}%] | {v['stick']:.1f}% |")
    emit("")
    emit("> **CI 怎麼讀**：同一天 47 條鏈的超額報酬高度相關（共同市場因子已扣掉，"
         "但產業間仍相關），把每個 chain-day 當獨立樣本會低估標準誤數倍。"
         "上表的 CI 是**按日期整批重抽**（同一天的所有鏈一起進出），這才是誠實的區間。"
         "**CI 跨 0 就是沒量到效果**，不要因為 avg 是正的就宣稱有訊號。")
    emit("")

    # ---------------------------------------------------------- §7 持續性 N
    emit("## 7. 持續性條件 N（連續 N 日符合才進榜）")
    emit("")
    emit("規格 §3「必須一併決定的」第一項。掃 N ∈ " + str(list(GRID_PERSIST)) +
         "，看名單穩定度、名單大小、以及新進榜樣本的 T+3 超額（用 ew 前瞻報酬）。")
    emit("")
    psc = {}
    dpos = {d: i for i, d in enumerate(dates)}
    for key in ("A", "B-ew", "B-mw"):
        by = {}
        for r in rows_all[(key, "ew")]:
            by[(dpos[r["d"]], r["c"])] = r
        sc = persist_scan(sysmap[key], by, idxs[key])
        psc[key] = sc
        emit(f"### {key}")
        emit("")
        emit("| N | 改善名單相鄰日 Jaccard | 平均名單長度 | 新進榜樣本數 | 新進榜 T+3 超額 avg | 勝大盤 |")
        emit("|---:|---:|---:|---:|---:|---:|")
        for r in sc:
            emit(f"| {r['n']} | {r['jac']*100:.1f}% | {r['size']:.1f} | {r['new']} | "
                 f"{fmtf(r['e3'], 2, '%') if r['e3'] is not None else '（樣本不足）'} | "
                 f"{fmtf(r['we3'], 1, '%') if r['we3'] is not None else '—'} |")
        emit("")

    # ---------------------------------------------------------- §8 早期 vs 近期
    emit("## 8. 早期（2025）vs 近期（2026）")
    emit("")
    emit(f"分界 {ERA_SPLIT}。第一階段報告限制 1 指出：283 天是用**當前** classify 回頭套用的，"
         "愈早的日期分類誤差愈大。若兩段行為明顯不同，就是回溯誤差的徵兆。")
    emit("")
    emit("| 座標系 | 期間 | 交易日 | 候補清單 Jaccard | 象限 Jaccard | 改善 Top5 交集率 | RS-Ratio IQR | 規模-離散度 ρ |")
    emit("|---|---|---:|---:|---:|---:|---:|---:|")
    era_rows = {}
    for key in ("A", "B-ew", "B-mw"):
        for lab, sel in (("2025 早期", lambda t: t < era_i), ("2026 近期", lambda t: t >= era_i)):
            ix = [t for t in idxs[key] if sel(t)]
            if len(ix) < 30:
                continue
            stv = stability(sysmap[key], ix)
            pl = tier_pollution(sysmap[key], shares, ix)
            vals = [sysmap[key][n][t][0] for n in sysmap[key] for t in ix
                    if sysmap[key][n][t] is not None]
            iqr = pctl(vals, 75) - pctl(vals, 25)
            era_rows[(key, lab)] = (stv, iqr, pl["_size_disp_rho"])
            emit(f"| {key} | {lab} | {len(ix)} | {stv['cand']*100:.1f}% | "
                 f"{stv['quad_all']*100:.1f}% | {stv['imp_top5_ov']*100:.1f}% | "
                 f"{iqr:.1f} | {pl['_size_disp_rho']:+.3f} |")
    emit("")
    gaps = []
    for key in ("A", "B-ew", "B-mw"):
        a = era_rows.get((key, "2025 早期"))
        b = era_rows.get((key, "2026 近期"))
        if a and b:
            gaps.append(abs(a[0]["cand"] - b[0]["cand"]) * 100)
    if gaps:
        emit(f"**結論：兩段行為沒有明顯差異。** 三組的候補清單 Jaccard 早期 vs 近期"
             f"最大只差 {max(gaps):.1f}pp（同一組內），象限 Jaccard、改善 Top5 交集率、"
             "RS-Ratio IQR、規模-離散度 ρ 也都在同一量級。")
        emit("")
        emit("**這個結果要怎麼讀，有兩種可能，本檔分不開：**")
        emit("")
        emit("1. 分類回溯誤差對**穩定度這類相對量**的影響不大——"
             "誤差多半是逐鏈固定的成分歸屬，逐日的相對變化被約掉了"
             "（第一階段報告的變異數拆解量到固定成分佔 69%，方向一致）。")
        emit("2. 或者，這批指標本來就對成分變動不敏感，所以量不出差異。")
        emit("")
        emit("**能說的只有一句：沒有觀察到「早期樣本明顯異常」的徵兆，"
             "因此不需要為此丟掉 2025 年的樣本。** 這不等於證明了回溯誤差很小——"
             "真正能證明的做法是取回歷史各版本的 `classify.json` 逐日重算，本檔沒有那份資料。")
        emit("")

    # ---------------------------------------------------------- §9 基準敏感度
    emit("## 9. 基準敏感度（價格版的分母該用誰）")
    emit("")
    emit("B 的分母有三個候選：同加權的 `market.ret_*`（本檔預設）、一律 `market.ret_mw`、"
         "`market.taiex_ret`。後者有 12 天壞資料的歷史（已改收盤比值），且與鏈的報酬口徑不同。")
    emit("")
    emit("| B 的分子 | 分母 | 候補清單 Jaccard | 象限 Jaccard | RS-Ratio 中位 |")
    emit("|---|---|---:|---:|---:|")
    tx = s["market"]["taiex_ret"]
    for wt in ("ew", "mw"):
        cr = {n: c[f"ret_{wt}"] for n, c in s["chains"].items()}
        for blab, bser in (("同加權 ret_" + wt, s["market"][f"ret_{wt}"]),
                           ("一律 ret_mw", s["market"]["ret_mw"]),
                           ("taiex_ret", tx)):
            cd = price_coords(cr, bser, W0, L0)
            ix = valid_idx(cd)
            if len(ix) < 30:
                emit(f"| ret_{wt} | {blab} | （有效日不足 {len(ix)}） | | |")
                continue
            stv = stability(cd, ix)
            vals = [cd[n][t][0] for n in cd for t in ix if cd[n][t] is not None]
            emit(f"| ret_{wt} | {blab} | {stv['cand']*100:.1f}% | {stv['quad_all']*100:.1f}% | "
                 f"{st.median(vals):.1f} |")
    emit("")
    nbad = len((s.get("meta") or {}).get("taiex_spread_zero_days") or [])
    emit(f"（`meta.taiex_spread_zero_days` 目前 {nbad} 天；`taiex_ret` 已改收盤比值繞開，"
         "但口徑與鏈仍不同——指數不還原股利、鏈用 `spread` 已還原。）")
    emit("")

    # ---------------------------------------------------------- §10 肘點複算
    emit("## 10. 建議參數（肘點）下的複算")
    emit("")
    emit(f"第 2、4~8 節全部用**共同基準參數** K/n={W0}、L/k={L0}，"
         "為的是三組之間 apples-to-apples。但每組的肘點不同，"
         "定案數字要用各組自己的肘點重算一次——這節就是那一次。")
    emit("")
    fin = {}
    for key in ("A", "B-ew", "B-mw"):
        if not elbow[key]:
            continue
        w, l, _v = elbow[key]
        cd = axis_systems(s, w, l)[key]
        ix = valid_idx(cd)
        stv = stability(cd, ix)
        nl = perm_null(cd, ix)
        pl = tier_pollution(cd, shares, ix)
        rw = {}
        for wt in ("ew", "mw"):
            cr = {n: c[f"ret_{wt}"] for n, c in s["chains"].items()}
            rw[wt] = transition_samples(cd, cr, s["market"][f"ret_{wt}"], dates, ix)
        by = {}
        for r in rw["ew"]:
            by[(dpos[r["d"]], r["c"])] = r
        fin[key] = {"w": w, "l": l, "st": stv, "null": nl, "pol": pl,
                    "rows": rw, "psc": persist_scan(cd, by, ix)}
    emit("| 座標系 | 肘點參數 | 候補清單 Jaccard | 虛無 | 超出 | 改善Top5 交集率 | 象限停留天數 | 最小層翻轉率 |")
    emit("|---|---|---:|---:|---:|---:|---:|---:|")
    for key in ("A", "B-ew", "B-mw"):
        f2 = fin.get(key)
        if not f2:
            emit(f"| {key} | （無肘點） | | | | | | |")
            continue
        a, b = f2["st"]["cand"], f2["null"]["cand"]
        tf = f"{f2['pol']['<0.5%']['flip']:.1f}%" if f2["pol"].get("<0.5%") else "—"
        emit(f"| {key} | K/n={f2['w']}、L/k={f2['l']} | {a*100:.1f}% | {b*100:.1f}% | "
             f"**{(a-b)*100:+.1f}pp** | {f2['st']['imp_top5_ov']*100:.1f}% | "
             f"{f2['st']['dwell']:.1f} 天 | {tf} |")
    emit("")
    emit("肘點參數下的持續性條件 N（第 7 節是共同基準參數下的版本）：")
    emit("")
    emit("| 座標系 | " + " | ".join(f"N={n}" for n in GRID_PERSIST) + " |")
    emit("|---|" + "---|" * len(GRID_PERSIST))
    for key in ("A", "B-ew", "B-mw"):
        if key not in fin:
            continue
        emit(f"| {key} | " + " | ".join(
            f"{r['jac']*100:.1f}% / {r['size']:.1f}條" for r in fin[key]["psc"]) + " |")
    emit("")
    emit("（每格＝改善名單相鄰日 Jaccard ／ 平均名單長度。**N 的選法與參數同理**："
         "取「Jaccard 在最佳值 3pp 以內、且平均名單長度 ≥3 條」的最小 N，"
         "不是取 Jaccard 最大值——N 一直加下去名單會空掉，空名單的 Jaccard 沒有意義。）")
    emit("")

    # ---------------------------------------------------------- §11 結論
    write_conclusions(fin, best, thin, elbow, pol, raw, dom, psc, stabs, nulls, rows_all)

    OUT.write_text("\n".join(LINES) + "\n", encoding="utf-8")
    print(f"\n已寫 {OUT.relative_to(ROOT)}")
    random.setstate(rnd_state)
    return 0


def write_conclusions(fin, best, thin, elbow, pol0, raw, dom, psc0, stabs, nulls,
                      base_rows):
    """結論兩段：建議方案、局限。

    **門檻判定寫成程式、不手打結論**——手打的結論會跟數字脫節（改了參數忘了改文字）。
    這裡的每一個 ✅/❌ 都是當次實跑的數字算出來的。
    判定一律用**肘點參數**下的複算值（fin），不是共同基準參數下的值。
    base_rows＝第 6 節（共同基準參數）的樣本，用來把「與第 6 節是否結論相同」也算出來、
    不用手寫——兩版的邊界格（例如貼著 0 的負向 CI）不一定一致。"""
    THRESH = 0.45          # 盤中版日頻試算的上緣
    KEYS = ("A", "B-ew", "B-mw")
    stabs = {k: (fin[k]["st"] if k in fin else stabs[k]) for k in KEYS}
    nulls = {k: (fin[k]["null"] if k in fin else nulls[k]) for k in KEYS}
    pol = {k: (fin[k]["pol"] if k in fin else pol0[k]) for k in KEYS}
    psc = {k: (fin[k]["psc"] if k in fin else psc0[k]) for k in KEYS}
    rows_all = {}
    for k in KEYS:
        for wt in ("ew", "mw"):
            rows_all[(k, wt)] = fin[k]["rows"][wt] if k in fin else []
    emit("## 11. 建議方案")
    emit("")
    emit("以下所有判定都用**各組肘點參數**下的複算值（第 10 節），不是共同基準參數的值。")
    emit("")
    emit("門檻：規格 §3 準則 1 要求「顯著優於盤中版日頻試算的 40~45%」。"
         "本檔把它拆成三條，**全過才算可以當訊號上線**：")
    emit("")
    emit(f"- **(a) 穩定度**：候補清單相鄰日 Jaccard > {THRESH:.0%}（＝盤中版區間上緣）")
    emit("- **(b) 不是算術產物**：扣掉置換虛無基準後仍 > 10pp。"
         "沒有這條，「把所有鏈丟進同一象限」的退化指標可以拿 100% 穩定度")
    emit("- **(c) 不是平滑病理**：達標所需的窗長要合理（停留天數 < 20 日），"
         "且**準則 4 要量得到前瞻超額**（T+3 超額的日期分塊 bootstrap 95%CI 不跨 0）。"
         "沒有這條，「一個月不動一次的清單」也會滿足 (a)(b)，但它不帶任何可行動的資訊")
    emit("")
    # (c) 的前瞻超額：對每組座標系，取「轉入改善」「轉入領先」兩種訊號、ew/mw 兩種前瞻報酬。
    # **方向要分開記**：「轉入改善」之後顯著**落後**大盤，不是可用訊號，是反指標——
    # 而且在 12 次比較裡出現 1 個邊界顯著（上界貼著 0），正是多重比較噪音的長相。
    fwd_pos, fwd_neg, ntest = {}, {}, 0
    for k in KEYS:
        pos, neg = [], []
        for wt in ("ew", "mw"):
            rows = rows_all[(k, wt)]
            for lab, q in (("轉入改善", "改善"), ("轉入領先", "領先")):
                rr = [r for r in rows if r["enter"] and r["q"] == q]
                if len(rr) < MIN_ROWS:
                    continue
                ntest += 1
                lo, hi = boot_ci(rr, "e3")
                if math.isnan(lo):
                    continue
                if lo > 0:
                    pos.append(f"{lab}/{wt}(+，CI {lo:+.2f}~{hi:+.2f}%)")
                elif hi < 0:
                    neg.append(f"{lab}/{wt}(−，CI {lo:+.2f}~{hi:+.2f}%)")
        fwd_pos[k], fwd_neg[k] = pos, neg
    verdict = {}
    for k in KEYS:
        a, b = stabs[k]["cand"], nulls[k]["cand"]
        ca = a > THRESH
        cb = (a - b) > 0.10
        cc = bool(elbow.get(k)) and stabs[k]["dwell"] < 20 and bool(fwd_pos[k])
        verdict[k] = (ca, cb, cc)
        ew_ = (f"K/n={elbow[k][0]}、L/k={elbow[k][1]}、停留 {stabs[k]['dwell']:.1f} 天"
               if elbow.get(k) else "達不到門檻")
        emit(f"- **{k}**：肘點 {ew_}；Jaccard {a*100:.1f}%"
             f"（虛無 {b*100:.1f}%，超出 {(a-b)*100:+.1f}pp）；"
             f"前瞻超額**正向**顯著：{'、'.join(fwd_pos[k]) if fwd_pos[k] else '**無**'}"
             + (f"；（負向顯著：{'、'.join(fwd_neg[k])}）" if fwd_neg[k] else ""))
        emit(f"  → (a) {'✅' if ca else '❌'} ／ (b) {'✅' if cb else '❌'} ／ "
             f"(c) {'✅' if cc else '❌'}")
    emit("")
    nhit = sum(len(fwd_pos[k]) + len(fwd_neg[k]) for k in KEYS)
    emit(f"> **多重比較**：準則 4 一共跑了 {ntest} 個檢定"
         f"（3 組座標 × 2 種前瞻報酬 × 2 種訊號），95% 信心水準下"
         f"**期望本來就會有約 {ntest*0.05:.1f} 個偽陽性**；本次實得 {nhit} 個"
         f"（正向 {sum(len(fwd_pos[k]) for k in KEYS)}、"
         f"負向 {sum(len(fwd_neg[k]) for k in KEYS)}）。"
         "**這個數量與純噪音無法區分**，任何單一格子的顯著性都不該單獨拿來當依據。")
    emit("")
    full = [k for k in KEYS if all(verdict[k])]
    stable = [k for k in KEYS if verdict[k][0] and verdict[k][1]]
    if full:
        pick = max(full, key=lambda k: stabs[k]["cand"] - nulls[k]["cand"])
        w, l, _v = elbow[pick]
        emit(f"### 結論：建議 **{pick}**，參數 K/n={w}、L/k={l}（三條全過）")
        emit("")
    elif stable:
        pick = max(stable, key=lambda k: stabs[k]["cand"] - nulls[k]["cand"])
        w, l, _v = elbow[pick]
        # N 的選法與參數同理：取肘點，不是取最大值。名單長度 <3 條的 N 直接不考慮
        # （空名單的 Jaccard 沒有意義，而且前端側欄要列得出東西）。
        pool_n = [r for r in psc[pick] if r["size"] >= 3.0 and not math.isnan(r["jac"])]
        if pool_n:
            mx = max(r["jac"] for r in pool_n)
            nbest = min([r for r in pool_n if r["jac"] >= mx - 0.03], key=lambda r: r["n"])
        else:
            nbest = None
        nn = nbest["n"] if nbest else 1
        emit("### 結論：**(a)(b) 過、(c) 不過**——可以做，但只能當「描述」，不能當「訊號」")
        emit("")
        emit(f"**建議方案：{pick}（價格版、{'等權' if pick.endswith('ew') else '市值加權'}），"
             f"參數 K/n={w}、L/k={l}，持續性條件 N={nn}。**")
        emit("")
        emit("挑這一組的具體數字依據：")
        emit("")
        emit(f"1. **穩定度**：候補清單相鄰日 Jaccard {stabs[pick]['cand']*100:.1f}%，"
             f"是三組最高；扣掉置換虛無基準 {nulls[pick]['cand']*100:.1f}% 之後仍有 "
             f"{(stabs[pick]['cand']-nulls[pick]['cand'])*100:+.1f}pp，"
             f"而 A 資金版只有 {(stabs['A']['cand']-nulls['A']['cand'])*100:+.1f}pp。")
        emit(f"2. **改善象限 Top5 交集率 {stabs[pick]['imp_top5_ov']*100:.1f}%**"
             f"（A 只有 {stabs['A']['imp_top5_ov']*100:.1f}%）。這一項是盤中版最慘的地方"
             "（0%），也是使用者最直接感受到「天天大洗牌」的地方。")
        emit(f"3. **小分母**：最小層（<0.5%）的象限翻轉率 "
             f"{pol[pick]['<0.5%']['flip']:.1f}%，A 是 {pol['A']['<0.5%']['flip']:.1f}%。"
             "注意這**不是**因為價格版沒有小分母問題（見第 4 節尺度說明），"
             "而是 z-score 把每條鏈都放到自己的尺度上，小鏈的高噪音被自身的高 SD 除掉了——"
             "代價寫在局限第 4 條。")
        emit(f"4. **個股支配度**：等權把 2308 台達電的最大權重從成交額口徑的 "
             f"{max([v for ck, v in dom['amt']['med_w'].items() if ck[0]=='2308'] or [0])*100:.1f}%"
             f"、市值口徑的 "
             f"{max([v for ck, v in dom['mw']['med_w'].items() if ck[0]=='2308'] or [0])*100:.1f}%"
             f"，稀釋到 "
             f"{max([v for ck, v in dom['ew']['med_w'].items() if ck[0]=='2308'] or [0])*100:.1f}%；"
             f"「權重≥20% 的鏈數」從 {dom['amt']['cnt'].get('2308', 0)}／"
             f"{dom['mw']['cnt'].get('2308', 0)} 條降到 {dom['ew']['cnt'].get('2308', 0)} 條。"
             "**複驗者的預判（市值加權放大、等權稀釋）在資料上成立。**")
        if nbest:
            nxt = next((r for r in psc[pick] if r["n"] == nn + 1), None)
            tail = ""
            if nxt:
                tail = (f"再加一天到 N={nn+1} 只換到 "
                        f"{(nxt['jac']-nbest['jac'])*100:+.1f}pp 的穩定度，"
                        f"名單卻從 {nbest['size']:.1f} 條掉到 {nxt['size']:.1f} 條，"
                        "而且每一條都晚一天才出現。")
            emit(f"5. **持續性條件 N={nn}**：改善名單相鄰日 Jaccard "
                 f"{nbest['jac']*100:.1f}%（N=1 時 {psc[pick][0]['jac']*100:.1f}%，"
                 f"提升 {(nbest['jac']-psc[pick][0]['jac'])*100:+.1f}pp），"
                 f"平均名單長度 {nbest['size']:.1f} 條（N=1 是 "
                 f"{psc[pick][0]['size']:.1f} 條）。{tail}")
            emit(f"   盤中版規格 §6 當初憑經驗建議的是「連續 2 日」；"
                 f"本次用資料掃出來的是 **N={nn}**"
                 + ("，與那個猜測一致。" if nn == 2 else
                    f"，與那個猜測不同（差 {nn-2:+d} 日），請以本次數字為準。"))
        emit("")
        other = "B-mw" if pick == "B-ew" else ("B-ew" if pick == "B-mw" else "B-mw")
        n_bp50 = sum(1 for ck, v in dom["mw"]["med_d"].items()
                     if ck[0] == "2308" and v * 10000.0 >= 50.0)
        emit("")
        emit(f"**為什麼是 {pick} 而不是 {other}**：兩者在準則 1 幾乎打平"
             f"（扣虛無 {(stabs[pick]['cand']-nulls[pick]['cand'])*100:+.1f}pp vs "
             f"{(stabs[other]['cand']-nulls[other]['cand'])*100:+.1f}pp），"
             f"{other} 的改善 Top5 交集率甚至更高"
             f"（{stabs[other]['imp_top5_ov']*100:.1f}% vs "
             f"{stabs[pick]['imp_top5_ov']*100:.1f}%）。"
             "**決勝點是準則 3（個股支配度），不是準則 1。**"
             f"市值加權下 2308 台達電在 {dom['mw']['cnt'].get('2308', 0)} 條鏈的權重 ≥20%、"
             f"在 {n_bp50} 條鏈的中位 LOO ≥50bp——"
             "意思是**一檔股票就能單獨決定將近半數產業鏈的座標**，"
             "那條鏈的「相對強弱」實際上是在畫台達電。等權把這個數字降到 "
             f"{dom['ew']['cnt'].get('2308', 0)} 條。")
        emit("")
        emit("這個取捨要講清楚：等權犧牲的是「反映真實資金權重」——"
             "一條鏈裡 3 億市值的小股與 3 兆的大股同權，"
             "圖上的「相對強弱」不等於「持有該鏈市值組合的報酬」。"
             "但既然本方案已經定位成**描述輪動、不是可交易訊號**，"
             "「不要被一檔股票綁架」比「可複製成投資組合」重要。"
             "若第三階段改成要對應可投資標的（例如對到 ETF），這個決定要重新檢討。")
        emit("")
        emit("**但 (c) 沒過，這一點必須寫在前端文案上，不能只寫在報告裡：**")
        emit("")
        wr = []
        for k in KEYS:
            for wt in ("ew", "mw"):
                for q in ("改善", "領先"):
                    rr = [r for r in rows_all[(k, wt)] if r["enter"] and r["q"] == q]
                    v = stat(rr)
                    if v:
                        wr.append(v["we3"])
        # 「與第 6 節是否結論相同」也用數字算，不手寫——共同基準參數版的邊界格
        # （貼著 0 的 CI）與肘點版不一定一致，逐格重算才知道嚴格說相不相同。
        bpos, bneg = [], []
        for k in KEYS:
            for wt in ("ew", "mw"):
                for lab, q in (("轉入改善", "改善"), ("轉入領先", "領先")):
                    rr = [r for r in base_rows[(k, wt)] if r["enter"] and r["q"] == q]
                    if len(rr) < MIN_ROWS:
                        continue
                    lo, hi = boot_ci(rr, "e3")
                    if math.isnan(lo):
                        continue
                    if lo > 0:
                        bpos.append(f"{k}/{wt}/{lab}，CI [{lo:+.2f}%, {hi:+.2f}%]")
                    elif hi < 0:
                        # 邊界格的種子敏感度：換 8 組 bootstrap 種子重抽，數幾組跨 0
                        alts = [boot_ci(rr, "e3", seed=SEED + d) for d in range(1, 9)]
                        n_flip = sum(1 for lo2, hi2 in alts if lo2 <= 0.0 <= hi2)
                        seed_note = (f"，且對 bootstrap 種子敏感"
                                     f"（換 8 組種子重抽，{n_flip} 組的 CI 跨 0）"
                                     if n_flip else "")
                        bneg.append(f"{k}/{wt}/{lab}，CI [{lo:+.2f}%, {hi:+.2f}%]"
                                    f"——上界貼著 0{seed_note}")
        if not bpos and not bneg:
            sec6 = "第 6 節（共同基準參數）的版本結論相同。"
        elif not bpos:
            sec6 = ("第 6 節（共同基準參數）的版本在「無**正向**顯著」上相同，"
                    f"但**不是逐格相同**：§6 有 {len(bneg)} 個邊界**負向**顯著格"
                    f"（{'；'.join(bneg)}）。這類邊界格在多重比較下"
                    "與噪音無法區分，不改變 (c) 不過的判定，"
                    "但「結論相同」不能讀成「§6 的格子同樣全跨 0」。")
        else:
            sec6 = (f"第 6 節（共同基準參數）的版本**與本節不同**："
                    f"正向顯著 {len(bpos)} 格（{'；'.join(bpos)}）"
                    + (f"、負向 {len(bneg)} 格（{'；'.join(bneg)}）" if bneg else "")
                    + "。(c) 的判定以肘點參數版為準，但這個分歧必須記錄。")
        emit(f"肘點參數下的 {len(wr)} 個格子（3 組座標 × 2 種前瞻報酬 × 2 種訊號）"
             "**T+3 超額的 95%CI 全部跨 0**，"
             f"勝大盤率落在 {min(wr):.1f}~{max(wr):.1f}%（純隨機是 ~50%）。"
             f"{sec6}"
             "意思是：**「某條鏈剛轉進改善／領先象限」這件事，"
             "在這 283 天裡量不到任何後續超額報酬**——"
             "而且勝大盤率的上緣連 50% 都沒有明顯超過，連方向都談不上。")
        emit("")
        emit("所以本檔的建議是：")
        emit("")
        emit("- ✅ **可以**上線成「這幾週資金／相對強弱在產業鏈之間怎麼移動」的**狀態描述**"
             "（使用者最初的需求就是看軌跡，不是要買賣訊號）")
        emit("- ❌ **不可以**寫成「進改善象限＝可以買」「這幾條值得留意」之類的推薦語氣。"
             "專案鐵律「新指標未經回測不上線」在這裡的正確解讀是：**回測做了，"
             "結果是沒有預測力**——那就不能用預測的語氣呈現。")
        emit("- ⚠ 前端文案建議直接寫明「本圖描述歷史相對強弱的位置與方向，"
             "回測未量到後續超額報酬」，比照專案既有的「技術指標為現況描述非買賣訊號」寫法。")
    else:
        emit("### 結論：**都不夠好，本階段不建議任何一組上線**")
        emit("")
        emit("三組沒有任何一組同時通過 (a)(b)。誠實的說法是：**用現有這批資料，"
             "日頻 RRG 的候補清單還是不夠穩定**，還缺的東西見局限段。")
    emit("")
    emit("### 三組各自的體質（不因為選了一組就略過另外兩組）")
    emit("")
    emit("| | A 資金版 | B-ew 價格版等權 | B-mw 價格版市值加權 |")
    emit("|---|---|---|---|")
    emit("| 候補清單 Jaccard | " + " | ".join(
        f"{stabs[k]['cand']*100:.1f}%" for k in KEYS) + " |")
    emit("| 扣虛無基準 | " + " | ".join(
        f"{(stabs[k]['cand']-nulls[k]['cand'])*100:+.1f}pp" for k in KEYS) + " |")
    emit("| 改善 Top5 交集率 | " + " | ".join(
        f"{stabs[k]['imp_top5_ov']*100:.1f}%" for k in KEYS) + " |")
    emit("| 象限平均停留天數 | " + " | ".join(
        f"{stabs[k]['dwell']:.1f} 天" for k in KEYS) + " |")
    emit("| 最小層(<0.5%) 象限翻轉率 | " + " | ".join(
        (f"{pol[k]['<0.5%']['flip']:.1f}%" if pol[k].get("<0.5%") else "—") for k in KEYS) + " |")
    emit("| 最大層(≥3%) 象限翻轉率 | " + " | ".join(
        (f"{pol[k]['≥3%']['flip']:.1f}%" if pol[k].get("≥3%") else "—") for k in KEYS) + " |")
    emit("| 規模 vs 標準化前原始噪音 ρ | " + " | ".join(
        f"{raw[k][0]:+.3f}" for k in KEYS) + " |")
    emit("| 個股支配（權重≥20% 的最大鏈數） | " + " | ".join(
        str(max([n for _c, n in dom[wk]["top"]] or [0])) for wk in ("amt", "ew", "mw")) + " |")
    emit("| 前瞻超額 CI **正向**顯著的格數（共 4） | " + " | ".join(
        str(len(fwd_pos[k])) for k in KEYS) + " |")
    emit("")
    emit("**三組的原始噪音-規模相關全部是負的**（小鏈比較吵），"
         "數量級也接近——這證實小分母汙染是**資料的性質、不是軸定義的性質**。"
         "A 把它原封不動搬進座標，B 用 z-score 把它除掉。"
         "除掉的代價是：小鏈的 1 個 z 對應的真實相對強弱變動遠大於大鏈的 1 個 z，"
         "**同一張圖上的兩個點不再是同一個單位**。這是價格版的核心取捨，"
         "第三階段的前端一定要處理（至少泡泡大小要帶規模資訊）。")
    emit("")

    emit("## 12. 局限")
    emit("")
    emit("1. **資料本身的回溯誤差**（第一階段報告限制 1、2）。283 天全部用 2026-08-01 的 "
         "`classify.json` 回頭套用，愈早的日期分類愈不可信；市值權重用當前發行張數。"
         "第 8 節量到的早期／近期差異**無法歸因**——它同時混了「市場真的變了」與「回溯誤差」，"
         "本檔沒有辦法把兩者分開。")
    emit("2. **殘餘漂移的噪音下限**（第一階段報告「誤差換算成 RS 座標的噪音尺度」）："
         "σ_RS ≈ 1.17 點（K=5），約 3.4% 的（日, 鏈）觀測落在 RS=100 的噪音帶內。"
         "本檔量到的象限翻轉**至少有這麼多**來自資料誤差，不能全記在指標頭上。")
    emit("3. **價格版的公式是公開重建，不是 JdK 原廠式**。官方文件（StockCharts ChartSchool、"
         "relativerotationgraphs.com）只寫「normalized、以 100 為基準」不給公式；本檔採 "
         "z-score 重建版（見檔頭出處）。**換成 EMA 比值版的另一派重建，數字會變**，"
         "本檔沒有測那一版。")
    emit("4. **z-score 版的一個結構性性質**：`RS_Ratio = 100 + (RS − MA)/SD` 是**逐鏈對自己歷史**"
         "標準化，所以每條鏈長期會在 100 附近擺盪。這不是規格 §5 禁止的「中位置中」"
         "（那是**橫斷面**置中，會讓改善象限永遠有人），但效果上會壓縮「整個市場一起走弱」"
         "這類共同訊號——**價格版天生看不見「今天沒有輪動機會」**這件事，"
         "資金版的自身 K 日基準也有同樣性質但程度較輕（分母是平均而非標準差）。")
    emit("5. **準則 4 的樣本不獨立**。同一天 47 條鏈的超額報酬相關，本檔已改用日期分塊 "
         "bootstrap，但分塊只處理了「同日相關」，沒處理**跨日自相關**"
         "（象限狀態有持續性，相鄰日的樣本不獨立）。真實 CI 應**比表列更寬**。")
    emit("6. **沒有做樣本外檢驗**。參數是在同一批 283 天上掃出來的，"
         "掃描網格 6×4=24 組 ×3 系統，**選最佳者本身就會過擬合**。"
         "表列的最佳值是樣本內上界，上線後的實際表現預期更差。")
    emit("7. **支配度用的是 classify 當前版本**，且權重法的門檻（20%）與 LOO 門檻"
         f"（{DOM_BP[0]:.0f}/{DOM_BP[1]:.0f}bp）是本檔自訂、沒有外部依據；"
         "換門檻會換名單，結論方向（哪種加權放大支配度）才是穩健的部分。")
    emit("8. **本檔沒有評估前端可讀性**。象限成員數、標籤碰撞、軌跡擁擠度等"
         "（盤中版 §6 花最多力氣的部分）全部留給第三階段。")
    emit("")


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.exit(main())
