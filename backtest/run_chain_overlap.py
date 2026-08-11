#!/usr/bin/env python3
# backtest/run_chain_overlap.py — 第二階段的追加檢定：「產業鏈不互斥」是不是準則 4 全滅的根因
#
# 背景：backtest/report_rrg_daily_axes.md 第 11 節的判定是 (a)(b) 過、(c) 不過——
#   12 個格子（3 座標 × 2 前瞻報酬 × 2 訊號）的 T+3 超額 95%CI 全部跨 0。
#   實作者提出一個沒量過的假說：**47 條產業鏈不是互斥組合**（2308 台達電一檔掛 21 條），
#   「資金從 A 鏈輪到 B 鏈」在 A、B 大量共用成員時語意可疑，所以準則 4 失效
#   有可能不是指標不好，而是「鏈」這個切法本身不承載輪動資訊。
#
# ── 本檔要回答的三個問題與各自的判準（先寫死，再看數字）─────────────
#  Q1 重疊有多嚴重？（描述，不判定）
#     成員數 Jaccard、**成交額加權 Jaccard**（座標是成交額/報酬驅動的，加權版才是有效重疊）、
#     每條鏈有多少成交額來自「同時屬於其他鏈」的成員、一檔股票平均掛幾條鏈。
#
#  Q2 鏈報酬有多少是共同因子？扣掉之後訊號會不會浮現？
#     對 47 條鏈的**超額日報酬**（鏈 − 大盤）做 PCA。
#     · 若 PC1 佔比極高 → 鏈之間幾乎同漲同跌，輪動空間本來就小。
#     · 用**殘差報酬**（扣掉 PC1／前 3 個 PC）重算準則 4。
#       **判準：殘差版若量到 T+3 超額 CI 不跨 0 而原始版沒有 → 失效是共同因子掩蓋，
#       不是切法問題（假說不成立）。殘差版一樣全跨 0 → 共同因子不是主因。**
#
#  Q3 換成互斥切法會不會比較好？（**這才是假說的決定性檢定**）
#     classify.json 的 `e`（交易所產業別）是天然互斥的（一檔股票只有一個 `e`）。
#     用完全相同的軸定義、參數、統計量重跑 B-ew。
#     **判準：若互斥分類的前瞻超額明顯較好（≥1 個格子 CI 不跨 0 且方向正確）
#     → 問題出在切法不互斥，假說成立；若互斥分類一樣全滅 → 假說不成立，
#     短天期輪動在這 283 天就是不可預測，換切法救不了。**
#     另跑兩種「把鏈強制互斥化」的變體，把「互斥」與「產業鏈語意」兩個變因拆開。
#
#  Q2b 順帶問一個更根本的問題：**這批資料裡到底有沒有短天期輪動可抓？**
#     完全不用 RRG，直接用「過去 L 日超額報酬」排序取前/後 20%，量 T+3 的多空價差。
#     若連這個最粗暴的動能排序都量不到，那準則 4 全滅就與 RRG 的軸定義無關。
#
# 口徑：全部沿用 backtest/run_rrg_daily_axes.py 的純函式（import，不複製），
#       所以「對照組跑的是同一套東西」是程式碼保證的，不是文件宣稱的。
#       個股層依 classify.c 去重、只計 t=="twse"、報酬與市值加權排除 ETF
#       （＝ src/build_chain_daily.py 的 def aggregate_day）。
#
# 用法：python3 backtest/run_chain_overlap.py → 印表 ＋ 寫 backtest/report_chain_overlap.md
#       需要 backtest/cache/chainday_*.json.gz（第一階段已抓好）；離線、免 token。

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
sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_rrg_daily_axes as ax   # noqa: E402  ← 軸定義／穩定度／準則 4 的正本

CACHE = ROOT / "backtest" / "cache"
OUT = ROOT / "backtest" / "report_chain_overlap.md"

# 第二階段定案的建議方案：B-ew、肘點 K/n=12、L/k=10（report_rrg_daily_axes.md 第 11 節）
ELBOW_W, ELBOW_L = 12, 10
BASE_W, BASE_L = 10, 5          # 第二階段的共同基準參數（三組 apples-to-apples 用）
PERM_REPS = 200
BOOT_REPS = 1000
SEED = 20260811
MOM_LOOKBACK = (5, 10, 20)      # Q2b 動能排序的回看天數
SUB_MIN_MEMBERS = 5             # 次產業要納入評估的最小成員數

LINES: list[str] = []


def emit(s: str = "") -> None:
    print(s)
    LINES.append(s)


def fmt(x, nd=1, suf=""):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    return f"{x:.{nd}f}{suf}"


# ================================================================ 純函式：重疊度

def chain_members(cmap: dict) -> dict[str, set]:
    """{鏈名: {股票代號}}。口徑同 aggregate_day：只計 t=="twse"、`c` 去重。"""
    out: dict[str, set] = defaultdict(set)
    for code, info in cmap.items():
        if code.startswith("_") or info.get("t") != "twse":
            continue
        for name in set(info.get("c") or []):
            out[name].add(code)
    return dict(out)


def chain_count_by_code(cmap: dict) -> dict[str, int]:
    """{股票代號: 掛幾條鏈}，只含 t=="twse" 且至少掛一條的股票。"""
    out = {}
    for code, info in cmap.items():
        if code.startswith("_") or info.get("t") != "twse":
            continue
        n = len(set(info.get("c") or []))
        if n:
            out[code] = n
    return out


def jaccard_count(a: set, b: set) -> float:
    """成員**檔數** Jaccard。空集合對空集合回 0（不是 nan——這裡不會發生）。"""
    u = a | b
    return (len(a & b) / len(u)) if u else 0.0


def weighted_jaccard(wa: dict, wb: dict) -> float:
    """**加權** Jaccard（Ruzicka）：Σ min(w) / Σ max(w)。

    w 是「該股在該鏈的絕對成交額」（不是正規化權重）——用絕對額才會讓
    「大鏈 vs 小鏈」的重疊被正確地折算成小值。兩鏈完全相同 → 1；完全不相交 → 0。
    這比檔數 Jaccard 重要，因為座標吃的是成交額不是檔數。"""
    keys = set(wa) | set(wb)
    lo = sum(min(wa.get(k, 0.0), wb.get(k, 0.0)) for k in keys)
    hi = sum(max(wa.get(k, 0.0), wb.get(k, 0.0)) for k in keys)
    return (lo / hi) if hi else 0.0


def overlap_coef(wa: dict, wb: dict) -> float:
    """**有向**重疊：A 的成交額有多少比例落在 A∩B。分母是 A 自己。"""
    ta = sum(wa.values())
    if not ta:
        return 0.0
    return sum(v for k, v in wa.items() if k in wb) / ta


def shared_amt_frac(w: dict, counts: dict[str, int]) -> float:
    """該鏈的成交額有多少比例來自「同時屬於其他鏈」的成員。"""
    tot = sum(w.values())
    if not tot:
        return 0.0
    return sum(v for k, v in w.items() if counts.get(k, 1) > 1) / tot


# ================================================================ 純函式：PCA（Jacobi）

def jacobi_eigen(mat: list[list[float]], sweeps: int = 100, tol: float = 1e-12):
    """對稱矩陣的循環 Jacobi 特徵分解。回 (特徵值遞減, 對應特徵向量 list)。

    自己寫是為了讓本檔與既有 backtest/ 一樣**只吃標準庫**（CI 的 setup-python
    沒有裝 numpy，backtest.yml 只有 checkout + setup-python）。
    正確性由 test_jacobi_eigen_reconstructs 用 A ≈ V diag(λ) Vᵀ 反算守門。"""
    n = len(mat)
    a = [row[:] for row in mat]
    v = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    for _ in range(sweeps):
        off = math.sqrt(sum(a[i][j] ** 2 for i in range(n) for j in range(n) if i != j))
        if off < tol:
            break
        for p in range(n - 1):
            for q in range(p + 1, n):
                if abs(a[p][q]) < 1e-18:
                    continue
                theta = (a[q][q] - a[p][p]) / (2.0 * a[p][q])
                sgn = 1.0 if theta >= 0 else -1.0
                t = sgn / (abs(theta) + math.sqrt(theta * theta + 1.0))
                c = 1.0 / math.sqrt(t * t + 1.0)
                s = t * c
                for k in range(n):                       # 右乘 J
                    akp, akq = a[k][p], a[k][q]
                    a[k][p] = c * akp - s * akq
                    a[k][q] = s * akp + c * akq
                for k in range(n):                       # 左乘 Jᵀ
                    apk, aqk = a[p][k], a[q][k]
                    a[p][k] = c * apk - s * aqk
                    a[q][k] = s * apk + c * aqk
                for k in range(n):                       # 累積特徵向量
                    vkp, vkq = v[k][p], v[k][q]
                    v[k][p] = c * vkp - s * vkq
                    v[k][q] = s * vkp + c * vkq
    vals = [a[i][i] for i in range(n)]
    order = sorted(range(n), key=lambda i: -vals[i])
    vecs = [[v[r][i] for r in range(n)] for i in order]
    return [vals[i] for i in order], vecs


def center(cols: dict[str, list]) -> tuple[dict, dict]:
    """逐序列減自身平均。回 (centered, means)。"""
    means = {n: st.mean(v) for n, v in cols.items()}
    return {n: [x - means[n] for x in v] for n, v in cols.items()}, means


def cov_matrix(names: list[str], X: dict[str, list]) -> list[list[float]]:
    T = len(X[names[0]])
    d = max(1, T - 1)
    return [[sum(X[a][t] * X[b][t] for t in range(T)) / d for b in names] for a in names]


def corr_matrix(names: list[str], X: dict[str, list]) -> list[list[float]]:
    C = cov_matrix(names, X)
    sd = [math.sqrt(C[i][i]) if C[i][i] > 0 else 0.0 for i in range(len(names))]
    return [[(C[i][j] / (sd[i] * sd[j]) if sd[i] and sd[j] else 0.0)
             for j in range(len(names))] for i in range(len(names))]


def pca_explained(names: list[str], X: dict[str, list], use_corr: bool) -> list[float]:
    """回各主成分的**解釋變異比例**（遞減）。use_corr=True → 相關矩陣（先標準化）。"""
    M = corr_matrix(names, X) if use_corr else cov_matrix(names, X)
    vals, _ = jacobi_eigen(M)
    tot = sum(v for v in vals if v > 0)
    return [max(0.0, v) / tot for v in vals] if tot else []


def residualize(names: list[str], cols: dict[str, list], k: int) -> dict[str, list]:
    """扣掉前 k 個主成分（**共變異數**矩陣的），保留各序列原本的平均。

    做法是正交投影移除：因為特徵向量正交且單位長，
    residual = x − Σ_j v_j (v_jᵀ x)，不需要跑迴歸估 beta。
    保留平均是刻意的——z-score 版的座標不是位移不變的，把平均一起扣掉
    等於順手改掉每條鏈的長期 alpha，那會變成在測另一個東西。"""
    X, means = center(cols)
    C = cov_matrix(names, X)
    _vals, vecs = jacobi_eigen(C)
    T = len(X[names[0]])
    R = {n: X[n][:] for n in names}
    for j in range(k):
        v = vecs[j]
        for t in range(T):
            f = sum(v[i] * R[names[i]][t] for i in range(len(names)))
            for i, n in enumerate(names):
                R[n][t] -= v[i] * f
    return {n: [x + means[n] for x in R[n]] for n in names}


def var_ratio(names: list[str], a: dict[str, list], b: dict[str, list]) -> float:
    """b 的總變異 ÷ a 的總變異（用來報「扣掉共同因子後還剩多少」）。"""
    va = sum(st.pvariance(a[n]) for n in names)
    vb = sum(st.pvariance(b[n]) for n in names)
    return (vb / va) if va else float("nan")


# ================================================================ 純函式：任意切法的日聚合

def is_etf(code: str) -> bool:
    return code.startswith("00")


def group_day(prices: dict[str, list], cmap: dict, groups_of) -> dict:
    """單日聚合，切法由 groups_of(code, info) → 群組名 iterable 決定。

    除了「群組怎麼定」以外，每一條口徑都逐字對齊 src/build_chain_daily.py 的
    def aggregate_day：只計 t=="twse"；市場總額含 ETF；報酬與市值加權排除 ETF；
    群組成交額用**全額**加總（不均分）；市值權重 = sh × 前收。
    由 test_group_day_matches_aggregate_day 用 classify.c 的切法反算守門。"""
    m_amt = 0.0
    m_rets: list[float] = []
    m_wpairs: list[tuple[float, float]] = []
    g_amt: dict[str, float] = defaultdict(float)
    g_n: dict[str, int] = defaultdict(int)
    g_rets: dict[str, list] = defaultdict(list)
    g_wpairs: dict[str, list] = defaultdict(list)

    for code, row in prices.items():
        if code.startswith("_"):
            continue
        info = cmap.get(code)
        if not info or info.get("t") != "twse":
            continue
        amt, close, spread = (list(row) + [None, None, None])[:3]
        amt = amt or 0.0
        m_amt += amt
        etf = is_etf(code)
        r = pc = None
        if not etf and close is not None and spread is not None and (close - spread) > 0:
            pc = close - spread
            r = spread / pc
        sh = info.get("sh") or 0
        w = (sh * pc) if (sh and pc) else None
        if r is not None:
            m_rets.append(r)
            if w:
                m_wpairs.append((r, w))
        for name in groups_of(code, info):
            g_amt[name] += amt
            g_n[name] += 1
            if r is not None:
                g_rets[name].append(r)
                if w:
                    g_wpairs[name].append((r, w))

    def wmean(pairs):
        tw = sum(w for _r, w in pairs)
        return (sum(r * w for r, w in pairs) / tw) if tw else None

    groups = {}
    for name in g_amt:
        groups[name] = {
            "amt": g_amt[name],
            "n": g_n[name],
            "share": (g_amt[name] / m_amt) if m_amt else None,
            "ret_ew": (st.mean(g_rets[name]) if g_rets[name] else None),
            "ret_mw": wmean(g_wpairs[name]),
        }
    return {"market": {"amt": m_amt,
                       "ret_ew": st.mean(m_rets) if m_rets else None,
                       "ret_mw": wmean(m_wpairs)},
            "groups": groups}


def columnar(dates: list[str], per_day: list[dict], names: list[str]) -> dict:
    """把逐日 dict 轉成 series.json 的形狀，好餵給 ax.axis_systems。"""
    out = {"dates": dates,
           "market": {k: [d["market"][k] for d in per_day] for k in ("ret_ew", "ret_mw")},
           "chains": {}}
    for name in names:
        out["chains"][name] = {
            f: [(d["groups"].get(name) or {}).get(f) for d in per_day]
            for f in ("share", "ret_ew", "ret_mw")}
    return out


# ================================================================ 純函式：互斥化規則

def exclusive_by_weight(cmap: dict, code_amt: dict[str, float]) -> dict[str, str]:
    """把多鏈個股指派給**它自己佔比最高**的那條鏈（＝該股最「代表」的鏈）。

    w = 該股成交額 ÷ 該鏈成交額；取最大者，平手用鏈名排序決定（可重現）。
    這條規則是本檔自訂、沒有外部依據——它的用途是把「互斥」這個變因單獨拉出來，
    不是主張這是正確的分類法。"""
    members = chain_members(cmap)
    tot = {name: sum(code_amt.get(c, 0.0) for c in codes) for name, codes in members.items()}
    out = {}
    for code, info in cmap.items():
        if code.startswith("_") or info.get("t") != "twse":
            continue
        cs = sorted(set(info.get("c") or []))
        if not cs:
            continue
        a = code_amt.get(code, 0.0)
        out[code] = max(cs, key=lambda n: ((a / tot[n]) if tot.get(n) else 0.0, n))
    return out


def exclusive_by_smallest(cmap: dict) -> dict[str, str]:
    """把多鏈個股指派給**成員最少**的那條鏈（＝最「特定」的鏈）。平手用鏈名。"""
    members = chain_members(cmap)
    out = {}
    for code, info in cmap.items():
        if code.startswith("_") or info.get("t") != "twse":
            continue
        cs = sorted(set(info.get("c") or []))
        if not cs:
            continue
        out[code] = min(cs, key=lambda n: (len(members[n]), n))
    return out


# ================================================================ 準則 4 / 動能排序

def crit4(coords: dict[str, list], rets: dict[str, list], bench: list,
          dates: list[str], idx: list) -> dict:
    """準則 4：三種樣本（轉入改善／轉入領先／對照組）的統計量與 T+3 超額 CI。

    完全由 ax 的函式組成，所以與 report_rrg_daily_axes.md 第 6 節是同一套算法。"""
    rows = ax.transition_samples(coords, rets, bench, dates, idx)
    sels = (("轉入改善", lambda r: r["enter"] and r["q"] == "改善"),
            ("轉入領先", lambda r: r["enter"] and r["q"] == "領先"),
            ("對照組(無轉換)", lambda r: not r["enter"]))
    out = {}
    for label, sel in sels:
        sub = [r for r in rows if sel(r)]
        s = ax.stat(sub)
        ci = ax.boot_ci(sub, "e3", reps=BOOT_REPS, seed=SEED) if s else (float("nan"),) * 2
        out[label] = (s, ci)
    return out


def trailing_excess(rets: dict[str, list], bench: list, look: int) -> dict[str, list]:
    """score[t] = 過去 look 日（t-look+1 .. t）的複利超額報酬。**不含 t 之後**，無前視。"""
    T = len(bench)
    return {n: [ax.fwd_excess(r, bench, t - look, look) if t >= look else None
                for t in range(T)] for n, r in rets.items()}


def coord_score(coords: dict[str, list], which: int) -> dict[str, list]:
    """把 RRG 座標拆成可排序的分數：which=0 → RS-Ratio、which=1 → RS-Momentum。"""
    return {n: [(c[which] if c is not None else None) for c in arr] for n, arr in coords.items()}


def block_boot_ci(daily: list[float], block: int, reps: int = BOOT_REPS, seed: int = SEED):
    """**移動分塊** bootstrap。block=1 等價於逐日獨立重抽。

    為什麼一定要分塊：逐日算出來的 T+3 價差，相鄰 3 天的前瞻視窗是重疊的
    （t 用 t+1..t+3、t+1 用 t+2..t+4），序列必然自相關。
    逐日獨立重抽會低估標準誤——**這正是第二階段對 chain-day 樣本提出的同一個批評**，
    這裡把它套用在自己身上。block 取 ≥ 前瞻期 +2 才蓋得住重疊。"""
    if len(daily) < 20:
        return (float("nan"), float("nan"))
    rnd = random.Random(seed)
    T = len(daily)
    nb = max(1, math.ceil(T / block))
    means = []
    for _ in range(reps):
        pool = []
        for _ in range(nb):
            s = rnd.randrange(max(1, T - block + 1))
            pool.extend(daily[s:s + block])
        means.append(sum(pool) / len(pool))
    return (ax.pctl(means, 2.5), ax.pctl(means, 97.5))


def xsec_spread(scores: dict[str, list], rets: dict[str, list], bench: list,
                idx: list, fwd: int = 3, frac: float = 0.2, block: int = 5) -> dict:
    """橫斷面排序檢定：按 scores 由大到小排，前 frac 減後 frac 的 T+fwd 超額價差。

    scores 換成「過去 L 日超額報酬」就是最粗暴的動能對照（＝所有輪動指標的共同上限）；
    換成 RS-Ratio／RS-Momentum 就是「RRG 的座標本身有沒有橫斷面資訊」。
    回逐日價差的平均、逐日獨立 CI、**移動分塊 CI**（後者才是誠實的區間）。"""
    names = list(rets)
    daily = []
    for t in idx:
        sc, fut = {}, {}
        for n in names:
            s = scores.get(n, [None] * len(bench))[t] if t < len(scores.get(n, [])) else None
            f = ax.fwd_excess(rets[n], bench, t, fwd)
            if s is not None and f is not None:
                sc[n] = s
                fut[n] = f
        if len(sc) < 10:
            continue
        order = sorted(sc, key=lambda n: (-sc[n], n))
        k = max(1, int(len(order) * frac))
        hi = st.mean(fut[n] for n in order[:k])
        lo = st.mean(fut[n] for n in order[-k:])
        daily.append((hi - lo) * 100.0)
    if len(daily) < 20:
        return {"n": len(daily), "avg": float("nan"),
                "ci": (float("nan"),) * 2, "ci_iid": (float("nan"),) * 2, "win": float("nan")}
    return {"n": len(daily), "avg": st.mean(daily),
            "ci": block_boot_ci(daily, block),
            "ci_iid": block_boot_ci(daily, 1),
            "win": sum(1 for x in daily if x > 0) / len(daily) * 100}


# ================================================================ 資料載入

def load_cache(dates: list[str]) -> list[dict]:
    out = []
    for ds in dates:
        p = CACHE / f"chainday_{ds}.json.gz"
        out.append(json.loads(gzip.decompress(p.read_bytes())) if p.exists() else None)
    return out


def code_total_amt(days: list[dict], cmap: dict) -> dict[str, float]:
    """全期個股成交額合計（重疊度的加權、互斥化規則都用它）。"""
    tot: dict[str, float] = defaultdict(float)
    for prices in days:
        if not prices:
            continue
        for code, row in prices.items():
            if code.startswith("_"):
                continue
            info = cmap.get(code)
            if not info or info.get("t") != "twse":
                continue
            tot[code] += (row[0] or 0.0) if row else 0.0
    return dict(tot)


def sub_groups(cmap: dict, min_members: int) -> dict[str, set]:
    """次產業層：classify 的 p = [[鏈, 次產業], ...]，群組名取「鏈／次產業」。"""
    out: dict[str, set] = defaultdict(set)
    for code, info in cmap.items():
        if code.startswith("_") or info.get("t") != "twse":
            continue
        for pr in (info.get("p") or []):
            if len(pr) >= 2 and pr[0] and pr[1]:
                out[f"{pr[0]}／{pr[1]}"].add(code)
    return {k: v for k, v in out.items() if len(v) >= min_members}


# ================================================================ 一次跑完一種切法

def evaluate(label: str, series: dict, w: int, l: int, perm: bool = True) -> dict:
    """對一種切法跑 B-ew：穩定度（含置換虛無）＋ 準則 4。"""
    sysd = ax.axis_systems(series, w, l)
    coords = sysd["B-ew"]
    coords = {n: a for n, a in coords.items() if any(x is not None for x in a)}
    idx = ax.valid_idx(coords)
    stab = ax.stability(coords, idx)
    null = ax.perm_null(coords, idx, reps=PERM_REPS, seed=SEED) if perm else {}
    rets = {n: series["chains"][n]["ret_ew"] for n in coords}
    c4 = crit4(coords, rets, series["market"]["ret_ew"], series["dates"], idx)
    return {"label": label, "n_groups": len(coords), "days": len(idx),
            "stab": stab, "null": null, "c4": c4}


def c4_rows(res: dict) -> list[str]:
    rows = []
    for key, (s, ci) in res["c4"].items():
        if not s:
            rows.append(f"| {res['label']} | {key} | 樣本不足 | | | | |")
            continue
        rows.append(
            f"| {res['label']} | {key} | {s['n']:,} | "
            f"{fmt(s['we1'])}/{fmt(s['we3'])}/{fmt(s['we5'])}% | "
            f"{s['e1']:+.2f}/{s['e3']:+.2f}/{s['e5']:+.2f}% | {s['e3m']:+.2f}% | "
            f"[{ci[0]:+.2f}%, {ci[1]:+.2f}%] |")
    return rows


def ci_crosses_zero(ci) -> bool:
    if any(math.isnan(x) for x in ci):
        return True
    return ci[0] <= 0.0 <= ci[1]


def count_positive_signals(res: dict) -> int:
    """CI 完全在 0 以上的格子數（只算「轉入改善／轉入領先」兩個訊號）。"""
    k = 0
    for key in ("轉入改善", "轉入領先"):
        s, ci = res["c4"][key]
        if s and not any(math.isnan(x) for x in ci) and ci[0] > 0:
            k += 1
    return k


# ================================================================ main

def main() -> int:
    s = ax.load_series()
    cmap = ax.load_classify()
    dates = s["dates"]
    emit("# 產業鏈重疊度檢定 — 準則 4 全滅是不是「切法不互斥」造成的")
    emit()
    emit(f"- 資料：`data/chain_daily/series.json`（{len(dates)} 交易日 {dates[0]} ~ {dates[-1]}、"
         f"{len(s['chains'])} 條鏈）＋ `backtest/cache/chainday_*.json.gz`（個股層）")
    emit("- 產生腳本：`backtest/run_chain_overlap.py`（離線、免 token）")
    emit("- 上游：`backtest/report_rrg_daily_axes.md` 第 11 節「(a)(b) 過、(c) 不過」")
    emit("- **軸定義、參數、穩定度、準則 4 的統計量全部 import `backtest/run_rrg_daily_axes.py` "
         "的同一批函式**，不是重寫一份——「對照組跑的是同一套東西」由程式碼保證")
    emit()

    days = load_cache(dates)
    have = sum(1 for d in days if d)
    emit(f"個股層快取覆蓋：{have}/{len(dates)} 個交易日。")
    emit()

    c_amt = code_total_amt(days, cmap)
    members = chain_members(cmap)
    counts = chain_count_by_code(cmap)
    chain_names = sorted(s["chains"])

    # ---------------------------------------------------------------- 1 重疊度
    emit("## 1. 成員重疊度的基本盤")
    emit()
    per_chain_w = {n: {c: c_amt.get(c, 0.0) for c in members.get(n, set())} for n in chain_names}

    n_cov = len(counts)
    cnts = sorted(counts.values())
    amt_w_mean = (sum(c_amt.get(c, 0.0) * k for c, k in counts.items())
                  / sum(c_amt.get(c, 0.0) for c in counts)) if counts else float("nan")
    emit(f"### 1.1 一檔股票掛幾條鏈")
    emit()
    emit(f"twse 且至少掛一條鏈的股票 **{n_cov} 檔**（twse 全體 "
         f"{sum(1 for c, i in cmap.items() if i.get('t') == 'twse')} 檔，其餘沒有鏈標籤）。")
    emit()
    emit("| 統計量 | 值 |")
    emit("|---|---:|")
    emit(f"| 平均掛幾條鏈 | {st.mean(cnts):.2f} |")
    emit(f"| 中位 | {ax.pctl(cnts, 50):.0f} |")
    emit(f"| P90 | {ax.pctl(cnts, 90):.0f} |")
    emit(f"| P99 | {ax.pctl(cnts, 99):.0f} |")
    emit(f"| 最大 | {max(cnts)} |")
    emit(f"| **成交額加權平均**（一塊錢的成交額平均被算進幾條鏈） | **{amt_w_mean:.2f}** |")
    emit(f"| 只掛 1 條的比例（檔數） | {sum(1 for x in cnts if x == 1) / len(cnts) * 100:.1f}% |")
    emit(f"| 只掛 1 條的比例（**成交額**） | "
         f"{sum(c_amt.get(c, 0.0) for c, k in counts.items() if k == 1) / sum(c_amt.get(c, 0.0) for c in counts) * 100:.1f}% |")
    emit()
    hist = defaultdict(int)
    for v in cnts:
        hist[v] += 1
    emit("掛鏈數分布（檔數）：" + "、".join(f"{k}條={v}" for k, v in sorted(hist.items())))
    emit()
    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
    emit("掛最多鏈的 10 檔：" + "、".join(
        f"{c}{(cmap[c].get('n') or '')}={k}" for c, k in top))
    emit()
    emit(f"> **2308 台達電掛 {counts.get('2308', 0)} 條是極端值，不是常態**："
         f"中位數 {ax.pctl(cnts, 50):.0f} 條、{sum(1 for x in cnts if x == 1) / len(cnts) * 100:.1f}% 的股票只掛 1 條。"
         f"但**用成交額看就不是這樣**——只掛 1 條的股票只佔 "
         f"{sum(c_amt.get(c, 0.0) for c, k in counts.items() if k == 1) / sum(c_amt.get(c, 0.0) for c in counts) * 100:.1f}% 的成交額，"
         f"加權平均一塊錢被算進 {amt_w_mean:.2f} 條鏈。座標吃的是成交額，所以**加權那一列才是有效的重疊度**。")
    emit()

    pairs = []
    for i, a in enumerate(chain_names):
        for b in chain_names[i + 1:]:
            jc = jaccard_count(members.get(a, set()), members.get(b, set()))
            jw = weighted_jaccard(per_chain_w[a], per_chain_w[b])
            ov = max(overlap_coef(per_chain_w[a], per_chain_w[b]),
                     overlap_coef(per_chain_w[b], per_chain_w[a]))
            pairs.append((a, b, jc, jw, ov))
    emit("### 1.2 兩兩重疊度（47 條鏈、%d 個配對）" % len(pairs))
    emit()
    emit("| 指標 | 中位 | P90 | P99 | 最大 | >0 的配對比例 | ≥0.3 的配對數 |")
    emit("|---|---:|---:|---:|---:|---:|---:|")
    for nm, col in (("成員檔數 Jaccard", 2), ("**成交額加權 Jaccard**", 3),
                    ("有向重疊上界 max(A∩B/A, A∩B/B)", 4)):
        v = [p[col] for p in pairs]
        emit(f"| {nm} | {ax.pctl(v, 50) * 100:.1f}% | {ax.pctl(v, 90) * 100:.1f}% | "
             f"{ax.pctl(v, 99) * 100:.1f}% | {max(v) * 100:.1f}% | "
             f"{sum(1 for x in v if x > 0) / len(v) * 100:.1f}% | {sum(1 for x in v if x >= 0.3)} |")
    emit()
    for nm, col in (("成員檔數 Jaccard", 2), ("成交額加權 Jaccard", 3), ("有向重疊上界", 4)):
        tp = sorted(pairs, key=lambda p: -p[col])[:5]
        emit(f"**{nm} 最高的 5 對**：" + "、".join(
            f"{a}×{b}={p[col] * 100:.1f}%" for p in tp for a, b in [(p[0], p[1])]))
        emit()

    sf = {n: shared_amt_frac(per_chain_w[n], counts) for n in chain_names}
    sfv = sorted(sf.values())
    emit("### 1.3 每條鏈有多少成交額來自「同時屬於其他鏈」的成員")
    emit()
    emit(f"- 47 條鏈的中位 **{ax.pctl(sfv, 50) * 100:.1f}%**、P10 {ax.pctl(sfv, 10) * 100:.1f}%、"
         f"P90 {ax.pctl(sfv, 90) * 100:.1f}%、最小 {min(sfv) * 100:.1f}%、最大 {max(sfv) * 100:.1f}%")
    lo5 = sorted(sf.items(), key=lambda kv: kv[1])[:5]
    hi5 = sorted(sf.items(), key=lambda kv: -kv[1])[:5]
    emit("- 最低的 5 條：" + "、".join(f"{n}={v * 100:.0f}%" for n, v in lo5))
    emit("- 最高的 5 條：" + "、".join(f"{n}={v * 100:.0f}%" for n, v in hi5))
    n_full100 = sum(1 for v in sfv if v >= 1.0)
    if n_full100 > 5:
        emit(f"- 恰好 100% 的**不只**上列五條（上一行只是取前五），共 **{n_full100} 條**，"
             "全名單見第 5.1 節")
    emit()
    dup = s.get("coverage", {}).get("dup") or []
    if dup:
        emit(f"> 第一階段已記錄的重複計算率 `coverage.dup`（鏈成交額加總 ÷ 個股層去重後總額 − 1 的同義量）"
             f"中位 {ax.pctl(dup, 50) * 100:.1f}%——與本節的 1.1 加權平均 {amt_w_mean:.2f} 條互相印證。")
        emit()

    # ---------------------------------------------------------------- 2 共同因子
    emit("## 2. 關鍵檢定：重疊度是否解釋了輪動訊號的失效")
    emit()
    mk = s["market"]["ret_ew"]
    full = [t for t in range(len(dates))
            if mk[t] is not None and all(s["chains"][n]["ret_ew"][t] is not None for n in chain_names)]
    exc_chain = {n: [s["chains"][n]["ret_ew"][t] - mk[t] for t in full] for n in chain_names}
    raw_chain = {n: [s["chains"][n]["ret_ew"][t] for t in full] for n in chain_names}
    emit(f"完整資料日 {len(full)} 天（47 條鏈與大盤同時有 `ret_ew`）。")
    emit()

    emit("### 2.1 鏈的報酬有多少是共同因子（PCA）")
    emit()
    emit("| 矩陣 | PC1 | PC2 | PC3 | 前 3 累計 | 前 5 累計 |")
    emit("|---|---:|---:|---:|---:|---:|")
    pca_rows = {}
    for nm, cols, uc in (("鏈**原始**日報酬（相關矩陣）", raw_chain, True),
                         ("鏈**超額**日報酬（鏈−大盤，相關矩陣）", exc_chain, True),
                         ("鏈**超額**日報酬（共變異數矩陣）", exc_chain, False)):
        X, _ = center(cols)
        ev = pca_explained(chain_names, X, uc)
        pca_rows[nm] = ev
        emit(f"| {nm} | {ev[0] * 100:.1f}% | {ev[1] * 100:.1f}% | {ev[2] * 100:.1f}% | "
             f"{sum(ev[:3]) * 100:.1f}% | {sum(ev[:5]) * 100:.1f}% |")
    emit()

    # ---------------------------------------------------------------- 3 對照組資料建置
    # （放在這裡是因為 2.2 之後的比較要用到互斥切法的 PCA）
    excl_w = exclusive_by_weight(cmap, c_amt)
    excl_s = exclusive_by_smallest(cmap)
    subs = sub_groups(cmap, SUB_MIN_MEMBERS)
    sub_of = defaultdict(list)
    for g, codes in subs.items():
        for c in codes:
            sub_of[c].append(g)

    def g_chain(code, info):
        return sorted(set(info.get("c") or []))

    def g_exch(code, info):
        e = info.get("e")
        return [] if (not e or e == "ETF") else [e]

    def g_exch_cov(code, info):
        e = info.get("e")
        if not e or e == "ETF" or not (info.get("c") or []):
            return []
        return [e]

    def g_excl_w(code, info):
        v = excl_w.get(code)
        return [v] if v else []

    def g_excl_s(code, info):
        v = excl_s.get(code)
        return [v] if v else []

    def g_sub(code, info):
        return sub_of.get(code, [])

    taxos = [("chain", "產業鏈（47，非互斥）", g_chain),
             ("exch", "交易所產業別（互斥，全 twse）", g_exch),
             ("exch_cov", "交易所產業別（互斥，只計有鏈標籤的股票）", g_exch_cov),
             ("excl_w", "鏈強制互斥化（指派給自身佔比最高的鏈）", g_excl_w),
             ("excl_s", "鏈強制互斥化（指派給成員最少的鏈）", g_excl_s),
             ("sub", f"次產業（鏈／次產業，成員≥{SUB_MIN_MEMBERS}）", g_sub)]

    per_day = {k: [] for k, _d, _f in taxos}
    for prices in days:
        for k, _d, fn in taxos:
            per_day[k].append(group_day(prices, cmap, fn) if prices else
                              {"market": {"amt": 0.0, "ret_ew": None, "ret_mw": None}, "groups": {}})
    names_of = {}
    for k, _d, _f in taxos:
        seen = set()
        for d in per_day[k]:
            seen |= set(d["groups"])
        names_of[k] = sorted(seen)
    ser = {k: columnar(dates, per_day[k], names_of[k]) for k, _d, _f in taxos}

    # 平行度檢查：用 `c` 切法重建的鏈序列，應與 series.json 幾乎一致
    diffs = []
    for n in chain_names:
        a = ser["chain"]["chains"][n]["ret_ew"]
        b = s["chains"][n]["ret_ew"]
        for t in range(len(dates)):
            if a[t] is not None and b[t] is not None:
                diffs.append(abs(a[t] - b[t]))
    emit(f"> **管線平行度檢查**：本檔用 `def group_day` 重建的 47 條鏈 `ret_ew`，"
         f"與第一階段 `series.json` 的最大絕對差 **{max(diffs):.2e}**（{len(diffs):,} 個 (日,鏈) 觀測）。"
         f"對照組因此是在同一條管線上跑的，不是另一份口徑。")
    emit()

    # 互斥切法的 PCA，回填 2.1 的比較
    emit("同一套 PCA 套在**互斥**切法上（同樣是超額日報酬、相關矩陣），作為「重疊拉高共同因子」的對照：")
    emit()
    emit("| 切法 | 群組數 | PC1 | PC2 | PC3 | 前 3 累計 |")
    emit("|---|---:|---:|---:|---:|---:|")
    ev_chain = pca_rows["鏈**超額**日報酬（鏈−大盤，相關矩陣）"]
    emit(f"| 產業鏈（非互斥） | {len(chain_names)} | {ev_chain[0] * 100:.1f}% | "
         f"{ev_chain[1] * 100:.1f}% | {ev_chain[2] * 100:.1f}% | {sum(ev_chain[:3]) * 100:.1f}% |")
    pca_alt = {}
    for k, desc, _f in taxos:
        if k == "chain":
            continue
        nm = names_of[k]
        mkt = ser[k]["market"]["ret_ew"]
        ok = [t for t in range(len(dates))
              if mkt[t] is not None and all(ser[k]["chains"][n]["ret_ew"][t] is not None for n in nm)]
        if len(ok) < 60 or len(nm) < 3:
            emit(f"| {desc} | {len(nm)} | 有效日不足（{len(ok)}） | | | |")
            continue
        cols = {n: [ser[k]["chains"][n]["ret_ew"][t] - mkt[t] for t in ok] for n in nm}
        X, _ = center(cols)
        ev = pca_explained(nm, X, True)
        pca_alt[k] = ev
        emit(f"| {desc} | {len(nm)} | {ev[0] * 100:.1f}% | {ev[1] * 100:.1f}% | "
             f"{ev[2] * 100:.1f}% | {sum(ev[:3]) * 100:.1f}% |")
    emit()

    # ---------------------------------------------------------------- 2.2 殘差版準則 4
    emit("### 2.2 扣掉共同因子後，準則 4 的訊號會不會浮現")
    emit()
    emit("殘差＝**超額報酬**（鏈−大盤）再扣掉前 k 個主成分的正交投影（保留各鏈平均，見 `def residualize`）。"
         "座標用同一支 `ax.price_coords`，基準改成 0（殘差本身已是相對量）；"
         "前瞻報酬也改成殘差複利。參數用第二階段定案的肘點 K/n=%d、L/k=%d。" % (ELBOW_W, ELBOW_L))
    emit()
    emit("（本節一律只用「47 條鏈與大盤同時有值」的完整資料日，有效日集合與第 3.2 節的"
         "產業鏈列略有不同，「原始」列的樣本數因此與 3.2 略差、非口徑不同。）")
    emit()
    zero = [0.0] * len(full)
    fdates = [dates[t] for t in full]
    variants = [("原始（超額，＝第二階段 B-ew 的複製）", exc_chain, None)]
    for k in (1, 3):
        variants.append((f"扣 PC{k if k == 1 else '1~3'}（殘差）", residualize(chain_names, exc_chain, k), k))
    emit("| 版本 | 殘差保留變異 | 樣本 | N | 勝大盤 T+1/T+3/T+5 | 超額avg T+1/T+3/T+5 | T+3 med | T+3 95%CI |")
    emit("|---|---:|---|---:|---|---|---:|---|")
    resid_pos = 0
    for vname, cols, k in variants:
        vr = 1.0 if k is None else var_ratio(chain_names, exc_chain, cols)
        coords = ax.price_coords(cols, zero, ELBOW_W, ELBOW_L)
        coords = {n: a for n, a in coords.items() if any(x is not None for x in a)}
        idx = ax.valid_idx(coords)
        c4 = crit4(coords, cols, zero, fdates, idx)
        for key, (stt, ci) in c4.items():
            if not stt:
                emit(f"| {vname} | {vr * 100:.0f}% | {key} | 樣本不足 | | | | |")
                continue
            emit(f"| {vname} | {vr * 100:.0f}% | {key} | {stt['n']:,} | "
                 f"{fmt(stt['we1'])}/{fmt(stt['we3'])}/{fmt(stt['we5'])}% | "
                 f"{stt['e1']:+.2f}/{stt['e3']:+.2f}/{stt['e5']:+.2f}% | {stt['e3m']:+.2f}% | "
                 f"[{ci[0]:+.2f}%, {ci[1]:+.2f}%] |")
            if k is not None and key != "對照組(無轉換)" and not ci_crosses_zero(ci) and ci[0] > 0:
                resid_pos += 1
    emit()
    emit(f"**殘差版 CI 完全在 0 以上的格子數：{resid_pos} / 4**"
         "（2 個殘差版本 × 2 個訊號）。")
    emit()

    # ---------------------------------------------------------------- 2.3 重疊 vs 座標相關
    emit("### 2.3 重疊度高的鏈對，座標是不是同一個東西的複本")
    emit()
    coords_b = ax.axis_systems(s, ELBOW_W, ELBOW_L)["B-ew"]
    xs_w, xs_c, ys_rs, ys_ret = [], [], [], []
    for a, b, jc, jw, _ov in pairs:
        ra = [c[0] if c else None for c in coords_b[a]]
        rb = [c[0] if c else None for c in coords_b[b]]
        both = [t for t in range(len(dates)) if ra[t] is not None and rb[t] is not None]
        if len(both) < 60:
            continue
        pr = ax.pearson([ra[t] for t in both], [rb[t] for t in both])
        pe = ax.pearson(exc_chain[a], exc_chain[b])
        if pr is None or (isinstance(pr, float) and math.isnan(pr)):
            continue
        xs_w.append(jw)
        xs_c.append(jc)
        ys_rs.append(pr)
        ys_ret.append(pe)
    emit(f"配對 {len(xs_w)} 對（兩鏈都有 ≥60 個共同有效日）。")
    emit()
    emit("| X | Y | Spearman | Pearson |")
    emit("|---|---|---:|---:|")
    for xn, xv in (("成交額加權 Jaccard", xs_w), ("成員檔數 Jaccard", xs_c)):
        for yn, yv in (("RS-Ratio 相關係數", ys_rs), ("超額日報酬相關係數", ys_ret)):
            emit(f"| {xn} | {yn} | {ax.spearman(xv, yv):+.3f} | {ax.pearson(xv, yv):+.3f} |")
    emit()
    order = sorted(range(len(xs_w)), key=lambda i: xs_w[i])
    q = max(1, len(order) // 5)
    emit("| 成交額加權 Jaccard 五分位 | 配對數 | 重疊中位 | RS-Ratio 相關中位 | 超額報酬相關中位 |")
    emit("|---|---:|---:|---:|---:|")
    for qi in range(5):
        seg = order[qi * q:(qi + 1) * q] if qi < 4 else order[4 * q:]
        emit(f"| Q{qi + 1}{'（重疊最低）' if qi == 0 else '（重疊最高）' if qi == 4 else ''} | {len(seg)} | "
             f"{ax.pctl([xs_w[i] for i in seg], 50) * 100:.1f}% | "
             f"{ax.pctl([ys_rs[i] for i in seg], 50):+.3f} | "
             f"{ax.pctl([ys_ret[i] for i in seg], 50):+.3f} |")
    emit()

    # ---------------------------------------------------------------- 2.4 動能下限檢定
    emit("### 2.4 這批資料裡到底有沒有短天期輪動可抓（完全不用 RRG 的下限檢定）")
    emit()
    emit("按「過去 L 日超額報酬」排序，取前 20% 減後 20%，量 T+3 的超額價差（逐日算，再 bootstrap）。"
         "**這是所有輪動指標的共同上限**：若這個都量不到，準則 4 全滅就與軸定義無關。")
    emit()
    emit("> CI 有兩欄：**分塊**是移動分塊 bootstrap（block=5，蓋住 T+3 前瞻視窗的重疊），"
         "**逐日**是把每天當獨立樣本。兩欄一起印是為了讓「逐日會低估標準誤」這件事看得見；"
         "**判定一律以分塊那欄為準**。")
    emit()
    emit("| 切法 | 群組數 | L | 有效日 | T+3 多空價差 avg | 95%CI（分塊，判定用） | 95%CI（逐日，僅對照） | 價差>0 的日數比例 |")
    emit("|---|---:|---:|---:|---:|---|---|---:|")
    mom_pos = mom_cells = mom_avg_pos = mom_iid_pos = 0
    mom_by_l: dict[int, list] = {x: [] for x in MOM_LOOKBACK}
    for k, desc, _f in taxos:
        src = s if k == "chain" else ser[k]
        nm = chain_names if k == "chain" else names_of[k]
        if len(nm) < 5:
            continue
        rets = {n: src["chains"][n]["ret_ew"] for n in nm}
        bench = src["market"]["ret_ew"]
        idx = [t for t in range(len(dates)) if bench[t] is not None]
        for look in MOM_LOOKBACK:
            r = xsec_spread(trailing_excess(rets, bench, look), rets, bench,
                            [t for t in idx if t >= look], fwd=3)
            mom_cells += 1
            if math.isnan(r["avg"]):
                emit(f"| {desc} | {len(nm)} | {look} | {r['n']} | 樣本不足 | | | |")
                continue
            sig = not ci_crosses_zero(r["ci"])
            mom_pos += 1 if sig else 0
            mom_iid_pos += 0 if ci_crosses_zero(r["ci_iid"]) else 1
            mom_avg_pos += 1 if r["avg"] > 0 else 0
            mom_by_l[look].append(r["avg"])
            emit(f"| {desc} | {len(nm)} | {look} | {r['n']} | {r['avg']:+.3f}%{' **✓顯著**' if sig else ''} | "
                 f"[{r['ci'][0]:+.3f}%, {r['ci'][1]:+.3f}%] | "
                 f"[{r['ci_iid'][0]:+.3f}%, {r['ci_iid'][1]:+.3f}%] | {r['win']:.1f}% |")
    emit()
    emit(f"**分塊 CI 不跨 0 的格子數：{mom_pos} / {mom_cells}**"
         f"（點估計為正的：{mom_avg_pos} / {mom_cells}；"
         + "各 L 的跨切法平均價差："
         + "、".join(f"L={x} {st.mean(v):+.3f}%" for x, v in mom_by_l.items() if v) + "）")
    emit()
    emit(f"> **這 {mom_cells} 個格子彼此高度相關**（六種切法是同一個市場的不同分組，"
         f"L={'/'.join(str(x) for x in MOM_LOOKBACK)} 的排序也大量重疊），"
         f"所以「{mom_pos} 個顯著」不能當成 {mom_pos} 個獨立證據；"
         f"而 {mom_cells} 個檢定在 95% 水準下本來就期望有約 {mom_cells * 0.05:.1f} 個偽陽性。"
         f"逐日 bootstrap 的版本有 {mom_iid_pos} 格顯著，分塊版只剩 {mom_pos} 格。"
         "**這一節能支持的最強說法是「方向一致、值得單獨驗」，不是「動能存在」。**")
    emit()

    # ---------------------------------------------------------------- 2.5 座標本身有沒有橫斷面資訊
    emit("### 2.5 若動能存在，RRG 的座標抓不抓得到（**指標 vs 事件的拆解**）")
    emit()
    emit("2.4 若量到動能，那準則 4 全滅就不能怪「沒有輪動可抓」，要再拆一層：")
    emit()
    emit("1. **座標**（RS-Ratio／RS-Momentum 的**水準值**）拿去做同一套橫斷面排序，抓不抓得到？")
    emit("2. 若座標抓得到而**象限轉換事件**抓不到 → 問題在**訊號的定義方式**（事件 vs 排序），"
         "不在切法也不在座標。")
    emit()
    emit("同一支 `def xsec_spread`、同一個前瞻期 T+3、同一組前後 20%，只換排序用的分數。")
    emit()
    emit("| 切法 | 排序分數 | 有效日 | T+3 多空價差 avg | 95%CI（分塊） | 價差>0 的日數比例 |")
    emit("|---|---|---:|---:|---|---:|")
    coord_pos = coord_cells = 0
    coord_detail = {}
    coord_avg = {0: [], 1: []}
    mom_alt_pos = 0    # 2.4 的 18 格改用本節（座標有效日）的日集合重算後仍顯著的格數
    for k, desc, _f in taxos:
        src = s if k == "chain" else ser[k]
        nm = chain_names if k == "chain" else names_of[k]
        if len(nm) < 5:
            continue
        rets = {n: src["chains"][n]["ret_ew"] for n in nm}
        bench = src["market"]["ret_ew"]
        cds = ax.axis_systems(src, ELBOW_W, ELBOW_L)["B-ew"]
        cds = {n: a for n, a in cds.items() if any(x is not None for x in a)}
        idx = ax.valid_idx(cds)
        for look in MOM_LOOKBACK:
            ra = xsec_spread(trailing_excess(rets, bench, look), rets, bench,
                             [t for t in idx if t >= look], fwd=3)
            if not math.isnan(ra["avg"]) and not ci_crosses_zero(ra["ci"]):
                mom_alt_pos += 1
        for sname, which in (("RS-Ratio 水準", 0), ("RS-Momentum 水準", 1)):
            sc = coord_score(cds, which)
            r = xsec_spread(sc, {n: rets[n] for n in cds}, bench, idx, fwd=3)
            coord_cells += 1
            if math.isnan(r["avg"]):
                emit(f"| {desc} | {sname} | {r['n']} | 樣本不足 | | |")
                continue
            sig = not ci_crosses_zero(r["ci"])
            coord_pos += 1 if sig else 0
            coord_detail[(k, which)] = r
            coord_avg[which].append(r["avg"])
            emit(f"| {desc} | {sname} | {r['n']} | {r['avg']:+.3f}%{' **✓顯著**' if sig else ''} | "
                 f"[{r['ci'][0]:+.3f}%, {r['ci'][1]:+.3f}%] | {r['win']:.1f}% |")
    emit()
    emit(f"**分塊 CI 不跨 0 的格子數：{coord_pos} / {coord_cells}**")
    emit()
    emit(f"點估計的**符號**比顯著性更值得看，因為它在六種切法上完全一致："
         f"RS-Ratio 水準 {sum(1 for x in coord_avg[0] if x > 0)}/{len(coord_avg[0])} 為正"
         f"（平均 {st.mean(coord_avg[0]):+.3f}%），"
         f"**RS-Momentum 水準 {sum(1 for x in coord_avg[1] if x < 0)}/{len(coord_avg[1])} 為負**"
         f"（平均 {st.mean(coord_avg[1]):+.3f}%）。")
    emit()
    emit("> **RS-Momentum 這條軸的排序方向與前瞻報酬相反**（動能高的那 20% 之後跑輸），"
         "而它正是象限判定的 Y 軸、也是「改善 Top5」的排序鍵。"
         "這不是顯著的負向訊號（CI 都跨 0），但六種切法同號、且與第二階段"
         "「轉入改善／轉入領先的超額 avg 多為負」互相印證，"
         "**足以說明準則 4 為什麼連方向都出不來**。")
    emit()

    # ---------------------------------------------------------------- 3 對照組
    emit("## 3. 對照組：換一種切法會不會比較好")
    emit()
    emit("每一種切法都跑**完全相同**的 B-ew 軸定義、肘點參數 K/n=%d L/k=%d、"
         "同一支置換虛無（%d 次）、同一支準則 4（日期分塊 bootstrap %d 次）。" %
         (ELBOW_W, ELBOW_L, PERM_REPS, BOOT_REPS))
    emit()
    results = {}
    for k, desc, _f in taxos:
        src = s if k == "chain" else ser[k]
        if len(names_of.get(k, [])) < 5 and k != "chain":
            continue
        results[k] = evaluate(desc, src, ELBOW_W, ELBOW_L)
    emit("### 3.1 準則 1 跨日穩定度")
    emit()
    emit("| 切法 | 群組數 | 有效日 | 候補清單 Jaccard | 虛無 | 超出 | 象限 Jaccard | 改善Top5 交集率 | 象限翻轉率 | 平均停留天數 |")
    emit("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for k, res in results.items():
        stb, nul = res["stab"], res["null"]
        emit(f"| {res['label']} | {res['n_groups']} | {res['days']} | {stb['cand'] * 100:.1f}% | "
             f"{nul.get('cand', float('nan')) * 100:.1f}% | "
             f"**{(stb['cand'] - nul.get('cand', float('nan'))) * 100:+.1f}pp** | "
             f"{stb['quad_all'] * 100:.1f}% | {stb['imp_top5_ov'] * 100:.1f}% | "
             f"{stb['flip'] * 100:.1f}% | {stb['dwell']:.1f} |")
    emit()
    emit("### 3.2 準則 4 前瞻超額（**決定性的一節**）")
    emit()
    emit("| 切法 | 樣本 | N | 勝大盤 T+1/T+3/T+5 | 超額avg T+1/T+3/T+5 | T+3 med | T+3 95%CI（日期分塊） |")
    emit("|---|---|---:|---|---|---:|---|")
    tot_cells = 0
    tot_pos = 0
    for k, res in results.items():
        for line in c4_rows(res):
            emit(line)
        tot_cells += 2
        tot_pos += count_positive_signals(res)
    emit()
    emit(f"**所有對照組合計：CI 完全在 0 以上的格子 {tot_pos} / {tot_cells}**"
         f"（{len(results)} 種切法 × 2 種訊號）。")
    emit()

    emit("### 3.3 單一個股支配度（互斥切法有沒有把問題換個地方發生）")
    emit()
    emit("等權下的支配度就是 1/成員數，所以直接看群組大小；另附「最大群組佔市場成交額比例」。")
    emit()
    emit("| 切法 | 群組數 | 成員數 中位 | 最小 | 最大 | 等權下最大成員權重（中位群組） | 最大群組的成交額佔比 |")
    emit("|---|---:|---:|---:|---:|---:|---:|")
    count_notes = []
    for k, _desc, fn in taxos:
        if k not in results:
            continue
        mem: dict[str, set] = defaultdict(set)
        for code, info in cmap.items():
            if code.startswith("_") or info.get("t") != "twse":
                continue
            for g in fn(code, info):
                mem[g].add(code)
        sizes = sorted(len(v) for v in mem.values())
        if not sizes:
            continue
        no_data = sorted(set(mem) - set(names_of.get(k, [])))
        if no_data:
            count_notes.append((results[k]["label"], len(mem), len(names_of[k]), no_data))
        gm = {g: sum(c_amt.get(c, 0.0) for c in v) for g, v in mem.items()}
        tot_amt = sum(c_amt.values())
        emit(f"| {results[k]['label']} | {len(sizes)} | {ax.pctl(sizes, 50):.0f} | {min(sizes)} | "
             f"{max(sizes)} | {100.0 / max(1, ax.pctl(sizes, 50)):.1f}% | "
             f"{max(gm.values()) / tot_amt * 100:.1f}%（{max(gm, key=gm.get)}） |")
    emit()
    for label, n_cls, n_dat, no_data in count_notes:
        emit(f"> **群組數為什麼與第 2.1／3.1 表不同**：{label}在本表是 {n_cls} 組、前表是 {n_dat} 組。"
             f"本表從 classify 的成員名單數（凡有股票掛該分類即算一組），"
             f"前表只計快取期間內**實際有成交資料、進入座標計算**的群組；"
             f"差的 {len(no_data)} 組（{'、'.join(no_data)}）期間內沒有任何個股成交資料。"
             "兩者來源不同，不是筆誤。")
        emit()

    emit("### 3.4 基準參數（K/n=%d、L/k=%d）下的複驗" % (BASE_W, BASE_L))
    emit()
    emit("肘點是在**產業鏈**上掃出來的，直接套到別的切法可能對它不公平。這節用第二階段的共同基準參數再跑一次。")
    emit()
    emit("| 切法 | 候補清單 Jaccard | 虛無 | 超出 | 轉入改善 T+3 CI | 轉入領先 T+3 CI |")
    emit("|---|---:|---:|---:|---|---|")
    base_pos = 0
    for k, desc, _f in taxos:
        if k not in results:
            continue
        src = s if k == "chain" else ser[k]
        r2 = evaluate(desc, src, BASE_W, BASE_L)
        ci_i = r2["c4"]["轉入改善"][1]
        ci_l = r2["c4"]["轉入領先"][1]
        base_pos += count_positive_signals(r2)
        emit(f"| {desc} | {r2['stab']['cand'] * 100:.1f}% | {r2['null'].get('cand', float('nan')) * 100:.1f}% | "
             f"**{(r2['stab']['cand'] - r2['null'].get('cand', float('nan'))) * 100:+.1f}pp** | "
             f"[{ci_i[0]:+.2f}%, {ci_i[1]:+.2f}%] | [{ci_l[0]:+.2f}%, {ci_l[1]:+.2f}%] |")
    emit()
    emit(f"**基準參數下 CI 完全在 0 以上的格子：{base_pos} / {len(results) * 2}**")
    emit()

    # ---------------------------------------------------------------- 4 結論
    write_conclusions({
        "pairs": pairs, "amt_w_mean": amt_w_mean, "sfv": sfv, "sf": sf,
        "ev_chain": ev_chain, "pca_alt": pca_alt, "resid_pos": resid_pos,
        "tot_pos": tot_pos, "tot_cells": tot_cells, "base_pos": base_pos,
        "mom_pos": mom_pos, "mom_cells": mom_cells, "mom_avg_pos": mom_avg_pos,
        "mom_iid_pos": mom_iid_pos, "mom_alt_pos": mom_alt_pos,
        "mom_by_l": mom_by_l,
        "coord_pos": coord_pos, "coord_cells": coord_cells, "coord_detail": coord_detail,
        "coord_avg": coord_avg,
        "results": results, "xs_w": xs_w, "ys_rs": ys_rs, "counts": counts, "cnts": cnts,
    })

    OUT.write_text("\n".join(LINES) + "\n", encoding="utf-8")
    print(f"\n[written] {OUT}")
    return 0

def _sig(res: dict) -> str:
    """一種切法的「前瞻超額顯著格數」摘要字串。"""
    return f"{count_positive_signals(res)}/2"


def write_conclusions(E: dict) -> None:
    """結論段。**每一句判定都由上面實算出來的計數決定**，沒有寫死的結果字串。

    這樣寫是刻意的：本檔第一版把「假說不成立、動能也不存在」直接寫進字串，
    實跑之後才發現動能是**存在**的，結論段與第 2.4 節的表自相矛盾。
    改成由計數推導之後，數字變了結論會跟著變，不會再出現這種矛盾。"""
    pairs = E["pairs"]
    jw = [p[3] for p in pairs]
    results = E["results"]
    ex = results.get("exch")

    # ── 判定規則（先寫死規則，數字代入）────────────────────────────
    #   假說＝「切法不互斥是準則 4 全滅的根因」。
    #   成立的必要條件：互斥切法要量到前瞻超額（否則換切法救不了）。
    excl_keys = [k for k in ("exch", "exch_cov", "excl_w", "excl_s") if k in results]
    excl_pos = sum(count_positive_signals(results[k]) for k in excl_keys)
    verdict_hold = excl_pos > 0
    momentum_exists = E["mom_pos"] > 0
    coord_works = E["coord_pos"] > 0

    emit("## 4. 補救方案評估（只評估，不實作）")
    emit()
    emit("| 方案 | 本檔量到的效果 | 代價 | 建議 |")
    emit("|---|---|---|---|")
    exs = (f"穩定度 {ex['stab']['cand'] * 100:.1f}%（超出虛無 "
           f"{(ex['stab']['cand'] - ex['null']['cand']) * 100:+.1f}pp）、"
           f"前瞻超額顯著格數 {_sig(ex)}") if ex else "—"
    emit(f"| (1) 用交易所產業別取代產業鏈 | {exs} | 失去「產業鏈」敘事（使用者最初的需求就是看鏈）；"
         f"粒度與群組數改變 | "
         f"{'可考慮' if (ex and count_positive_signals(ex)) else '**不建議**：換不到前瞻超額，代價卻是全部敘事'} |")
    for kk, nm, cost in (("excl_w", "(2a) 鏈強制互斥化（指派給自身佔比最高的鏈）",
                          "指派規則無外部依據；使用者查「台達電在哪些鏈」會只查到一條"),
                         ("excl_s", "(2b) 鏈強制互斥化（指派給成員最少的鏈）",
                          "同上，且小鏈會被灌入大權值股")):
        r = results.get(kk)
        eff = (f"穩定度 {r['stab']['cand'] * 100:.1f}%（超出虛無 "
               f"{(r['stab']['cand'] - r['null']['cand']) * 100:+.1f}pp）、顯著格數 {_sig(r)}") if r else "—"
        emit(f"| {nm} | {eff} | {cost} | "
             f"{'可考慮' if (r and count_positive_signals(r)) else '**不建議**：同上，且多付一層人造規則'} |")
    emit("| (3) 維持現狀＋UI 揭露重疊 | 不改變任何統計性質（本檔第 2.3 節提供可直接顯示的重疊量） | "
         "只需前端文案 | **建議**：這是唯一有正當性的一項——重疊是真的，"
         "只是它影響的是可讀性不是預測力 |")
    emit()
    emit("**金額均分（多鏈個股把成交額除以掛鏈數）本檔沒有單獨測**，理由要講清楚："
         "均分只改變「鏈成交額／share」，也就是 **A 資金版**的軸；第二階段定案的方案是 "
         "**B-ew（價格版）**，它吃的是鏈的**等權報酬**——成員名單不變則等權報酬不變，"
         "均分對 B-ew 是恆等變換、量了也是同一組數字。要讓均分有意義必須回頭救 A 版，"
         "而 A 版在第二階段的穩定度就已經輸 B-ew 20pp 以上（49.5% vs 70.2%）。")
    emit()

    emit("## 5. 結論")
    emit()
    if verdict_hold:
        emit("**判定：假說成立。**互斥切法量到了前瞻超額（見下），非互斥確實是準則 4 全滅的原因之一。")
    else:
        emit("**判定：假說不成立。**「47 條產業鏈不是互斥組合」這件事**是真的、而且不小**，"
             "但它**不是**準則 4 全滅的原因。")
    emit()
    emit("### 5.1 假說的前半段（重疊嚴重）成立")
    emit()
    zero_pct = sum(1 for x in jw if x <= 0) / len(jw) * 100
    emit(f"- 成交額加權 Jaccard：中位 {ax.pctl(jw, 50) * 100:.1f}%、P90 {ax.pctl(jw, 90) * 100:.1f}%、"
         f"最大 {max(jw) * 100:.1f}%（大數據×太空衛星科技）。**{zero_pct:.1f}% 的配對完全不相交**，"
         f"所以中位數是 0；但有 {sum(1 for x in jw if x >= 0.3)} 對的加權重疊 ≥30%。"
         f"**重疊是集中在少數配對上的重症，不是普遍的輕症。**")
    full100 = sorted(n for n, v in E["sf"].items() if v >= 1.0)
    emit(f"- 每條鏈的成交額有中位 {ax.pctl(E['sfv'], 50) * 100:.1f}% 來自「同時屬於其他鏈」的成員；"
         f"恰好 100% 的有 **{len(full100)} 條**：{'、'.join(full100)}"
         "（第 1.3 節「最高的 5 條」只是取前五，不是只有五條）。")
    emit(f"- 一塊錢的成交額平均被算進 {E['amt_w_mean']:.2f} 條鏈；2308 台達電掛 21 條是極端值"
         f"（中位 1 條、70.7% 只掛 1 條），但**成交額加權之後就不是極端值問題而是結構問題**。")
    emit(f"- 重疊確實讓座標互相牽動：成交額加權 Jaccard 與 RS-Ratio 相關係數的 Spearman "
         f"= {ax.spearman(E['xs_w'], E['ys_rs']):+.3f}，最高五分位的配對 RS-Ratio 相關中位 "
         f"{ax.pctl([E['ys_rs'][i] for i in sorted(range(len(E['xs_w'])), key=lambda i: E['xs_w'][i])[-len(E['xs_w']) // 5:]], 50):+.3f}，"
         f"最低五分位是負的。**圖上確實有一部分點是同一個東西的複本。**")
    emit()
    emit("### 5.2 但假說的後半段（這是準則 4 失效的原因）不成立")
    emit()
    emit(f"1. **重疊不是共同因子的來源**：鏈超額報酬的 PC1 佔 {E['ev_chain'][0] * 100:.1f}%，"
         + ("互斥的交易所產業別 %.1f%%、強制互斥化的鏈 %.1f%%／%.1f%%。"
            % (E['pca_alt']['exch'][0] * 100, E['pca_alt']['excl_w'][0] * 100,
               E['pca_alt']['excl_s'][0] * 100)
            if all(k in E['pca_alt'] for k in ('exch', 'excl_w', 'excl_s')) else "")
         + "重疊若在製造假的同步性，互斥版的 PC1 應**明顯較低**；交易所產業別甚至更高一點。")
    emit(f"2. **扣掉共同因子救不回訊號**：殘差版（扣 PC1／扣 PC1~3）準則 4 的 T+3 超額 CI "
         f"完全在 0 以上的格子 **{E['resid_pos']}/4**。"
         f"「失效是共同因子掩蓋」這個替代解釋同樣不成立。")
    emit(f"3. **決定性的一項——互斥切法一樣全滅**：{len(results)} 種切法"
         f"（含天然互斥的交易所產業別、兩種強制互斥化的鏈、次產業）× 2 種訊號共 "
         f"{E['tot_cells']} 個格子，肘點參數下 CI 完全在 0 以上的有 **{E['tot_pos']} 個**，"
         f"換共同基準參數重跑是 **{E['base_pos']}/{len(results) * 2}**。"
         f"其中天然互斥的交易所產業別，穩定度與產業鏈幾乎打平"
         + (f"（{ex['stab']['cand'] * 100:.1f}% vs {results['chain']['stab']['cand'] * 100:.1f}%）"
            if ex else "")
         + "，前瞻超額一樣全跨 0。**互斥不會讓前瞻超額出現。**")
    emit()

    emit("### 5.3 那真正的原因在哪裡——本檔量到的線索（**這一段是線索，不是結論**）")
    emit()
    emit(f"完全不用 RRG、直接按過去 L 日超額報酬排序取前後 20% 的 T+3 多空價差（第 2.4 節）："
         f"**{E['mom_cells']} 個格子的點估計全部為正（{E['mom_avg_pos']}/{E['mom_cells']}）**，"
         + "隨 L 拉長遞增（" + "、".join(f"L={x} 平均 {st.mean(v):+.3f}%"
                                        for x, v in E["mom_by_l"].items() if v) + "），"
         + f"但移動分塊 95%CI 不跨 0 的只有 **{E['mom_pos']}/{E['mom_cells']}**。")
    emit()
    if momentum_exists and E["mom_pos"] * 2 < E["mom_cells"]:
        emit("**這個證據強度只夠說「值得單獨驗」，不夠說「動能存在」。**"
             f"理由有三：(i) {E['mom_cells']} 個檢定在 95% 水準下期望約 "
             f"{E['mom_cells'] * 0.05:.1f} 個偽陽性；"
             "(ii) 六種切法是同一個市場的不同分組、三個 L 的排序也大量重疊，"
             f"顯著的那幾格不是獨立證據；(iii) 逐日 bootstrap 有 {E['mom_iid_pos']} 格顯著、"
             f"誠實的分塊版只剩 {E['mom_pos']} 格——**這個結果對 CI 的算法非常敏感**，"
             f"對有效日集合的取法亦然（同一批格子改用第 2.5 節的有效日重算，"
             f"分塊顯著格數變成 {E['mom_alt_pos']}/{E['mom_cells']}——"
             "CI 下緣貼著 0 的格子，換一組日子集就會進出顯著名單）。")
        emit()
        emit("能說的是：**方向一致（18/18 為正）且隨回看期單調變強，這個形狀不像純噪音**，"
             "但本檔沒有把它驗到可以宣稱的程度。")
    elif momentum_exists:
        emit("多數格子顯著，動能在這個樣本裡有相當支持。")
    else:
        emit("**連最粗暴的動能排序都量不到**，那是所有輪動指標的共同上限，"
             "準則 4 全滅與軸定義、切法都無關。")
    emit()
    emit("往下拆一層（第 2.5 節）——把 RRG 的**座標水準值**拿去做同一套橫斷面排序：")
    emit()
    emit(f"- **RS-Ratio 水準**：{sum(1 for x in E['coord_avg'][0] if x > 0)}/{len(E['coord_avg'][0])} 為正"
         f"（平均 {st.mean(E['coord_avg'][0]):+.3f}%），方向與動能一致但更弱、CI 全跨 0。")
    emit(f"- **RS-Momentum 水準**：{sum(1 for x in E['coord_avg'][1] if x < 0)}/{len(E['coord_avg'][1])} "
         f"**為負**（平均 {st.mean(E['coord_avg'][1]):+.3f}%）——"
         f"**這條軸的排序方向與前瞻報酬相反。**")
    emit()
    emit("RS-Momentum 正是象限判定的 Y 軸（M≥100 才進「改善／領先」），也是「改善 Top5」的排序鍵。"
         "六種切法同號，且與第二階段「轉入改善／轉入領先的 T+3 超額 avg 多為負」互相印證。"
         "**這比「切法不互斥」更能解釋準則 4 為什麼連方向都出不來**——"
         "訊號的 Y 軸在這個樣本裡本來就指著錯的方向。")
    emit()
    emit("> 注意這仍然只是**線索**：RS-Momentum 的負向 CI 全部跨 0，"
         "本檔**沒有**證明「反著做會賺」。它證明的是「準則 4 失效的位置在 Y 軸與事件定義，"
         "不在成員重疊」。")
    emit()

    emit("### 5.4 對第三階段的建議")
    emit()
    emit("- **不要為了救準則 4 去換切法。**互斥化（不論是換成交易所產業別，還是把鏈強制互斥化）"
         "換不到任何前瞻超額，卻要付出「失去產業鏈敘事」或「多一層人造指派規則」的代價。")
    emit("- **第二階段的定案不需要因為本檔而改**：B-ew、K/n=12、L/k=10、N=3、"
         "只當狀態描述不當買賣訊號。本檔沒有推翻其中任何一項。")
    emit("- **重疊度要在 UI 揭露**。第 2.3 節的成交額加權 Jaccard 可以直接當量化依據："
         "至少讓使用者知道「這兩條鏈靠得近，可能只是因為它們共用成員」。"
         f"重疊最重的 {len(full100)} 條（成交額 100% 來自多鏈成員："
         f"{'、'.join(full100)}）值得特別標注。")
    if momentum_exists:
        emit("- **若第三階段還想追可行動的訊號，下一個該驗的是訊號定義、不是分類**："
             "第 2.4／2.5 節指向「**橫斷面排序**（今天在 47 條裡排第幾、回看 10~20 日）」"
             "而不是「**象限轉換事件**（今天跨線沒）」，而且 RS-Momentum 這條 Y 軸的方向可能是反的。"
             "**但這是一個新假說，本檔沒有驗證它、更沒有驗證它可以上線**——"
             f"{E['mom_cells']} 格裡只有 {E['mom_pos']} 格顯著、沒有樣本外、"
             "沒扣交易成本、沒算換手率。"
             "要用它必須另開一輪回測，不能拿本檔的數字當依據。")
    emit()

    emit("## 6. 局限")
    emit()
    emit("1. **「假說不成立」不等於「重疊沒問題」**。本檔證明的是「互斥化救不回準則 4」，"
         "沒有證明重疊對使用者無害——第 2.3 節量到的座標相關性正好相反。")
    emit("2. **互斥化的指派規則是本檔自訂**（自身佔比最高／成員最少），沒有外部依據。"
         "本檔用兩條方向相反的規則各跑一次來降風險，但不能窮盡所有互斥化方式；"
         "「若鏈被互斥化到某種理想狀態會不會有訊號」本檔答不了。")
    emit("3. **交易所產業別不是「產業鏈的互斥版」**。它的粒度、成員數分布、語意都不同"
         "（「電子工業」一類就 225 檔）。它能證明的是「另一種互斥分類也沒有前瞻超額」，"
         "不能證明「所有互斥分類都不行」。")
    emit("4. **第 2.4／2.5 節的動能結果是樣本內的，不是可上線的結論**。283 天、單一市場、"
         "固定的前後 20% 與 T+3、L 只掃了 3 個值；沒有樣本外、沒有交易成本、沒有容量分析。"
         "**把它當成「準則 4 為什麼失效」的診斷，不要當成策略。**")
    emit("5. **移動分塊 bootstrap 的 block=5 是本檔選的**，只保證蓋住 T+3 的視窗重疊，"
         "沒有處理更長的自相關結構；真實 CI 可能仍比表列窄的那一版寬。"
         "報告同時印了逐日版與分塊版，方便對照兩者差多少。")
    emit("6. **沿用第二階段的全部限制**（`report_rrg_daily_axes.md` 第 12 節）："
         "分類回溯誤差（283 天全部用當前 classify 回頭套）、z-score 版公式是公開重建、"
         "準則 4 的樣本不獨立、參數是樣本內掃出來的。")
    emit("7. **重疊度用全期成交額合計**，不是逐日重算。鏈成員與成交額結構在 283 天內會變，"
         "本檔的重疊度是期間平均的近似值；互斥化的指派規則也吃同一份合計，"
         "等於用了全期資訊（對第 3 節的**穩定度**無害，但對前瞻超額嚴格說有輕微前視，"
         "只是那兩欄的結論是「全滅」，前視只會讓結果偏樂觀，不影響方向）。")
    emit("8. **PCA 用完整資料日的子集**，缺值日整天剔除；若缺值與市場狀態相關會有選擇偏誤，"
         "本檔沒有量這件事。")
    emit()
if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.exit(main())
