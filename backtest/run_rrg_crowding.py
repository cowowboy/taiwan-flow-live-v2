#!/usr/bin/env python3
# backtest/run_rrg_crowding.py — RRG 設計分析②：畫面會不會擠爆？（2026-08-09 一次性）
#
# 這支不是回測，是 2026-08-09 為了決定 RRG 規格（docs/rrg-spec-20260809.md §2）
# 而做的一次性設計分析。座標公式的正式驗證在 `run_rrg.py` / `report_rrg.md`。
#
# 【回答的問題】47 條產業鏈全畫上 900×700 的畫布會多擠？用「成交佔比 <X% 就淡化
#   且不顯示標籤」能不能救？會不會誤傷「改善象限的小族群」這個目標訊號？
# 【座標】此處用**日頻**代理座標（share 對前 MA=5 日均、動能回看 MOM_LAG=3 日），
#   只為量畫面幾何；盤中版的正式座標定義見 run_rrg.py。
# 【判定】點距離（最小/P5/中位）、<20px 的配對數、標籤矩形碰撞配對數與重疊標籤佔比、
#   落在中央 1/4 區的比例、軌跡線段數與線段相交次數。
# 【對照組】淡化門檻 X ∈ {0.5, 1, 2, 3}% 對照「全部畫」；
#   另比較「只畫 ≥1% 的 21 條」vs「全部 47 條」的軌跡相交次數。
# 【結論】純佔比門檻 X=1% 雖把碰撞從 50 對降到 19 對，卻淡掉 6/8 個改善象限族群
#   → 規格 §2 改採「Top15 或 改善象限 或 位移異常」的複合顯示規則。
#
# 【用法】python backtest/run_rrg_crowding.py（離線、免 token、只印表不寫檔）

from __future__ import annotations

import io, sys, json, glob, math, itertools
from collections import defaultdict
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

BASE = str(Path(__file__).resolve().parent.parent / 'data')

# ---------- 資料載入 ----------
cmap = json.load(open(f'{BASE}/classify.json', encoding='utf-8'))['map']
sub2chains = defaultdict(set)
for v in cmap.values():
    for chain, sub in (v.get('p') or []):
        sub2chains[sub].add(chain)
sub2chains = {k: sorted(v) for k, v in sub2chains.items()}

days = {}
for f in sorted(glob.glob(f'{BASE}/intraday/????-??-??.json')):
    d = json.load(open(f, encoding='utf-8'))
    g = d.get('g') or {}
    out = defaultdict(float)
    for sub, arr in g.items():
        if not arr or not arr[-1] or arr[-1] <= 0:
            continue
        chains = sub2chains.get(sub)
        if not chains:
            continue
        per = arr[-1] / len(chains)
        for c in chains:
            out[c] += per
    if out:
        days[d['date']] = dict(out)

DATES = sorted(days)
CHAINS = sorted({c for r in days.values() for c in r})
print(f"交易日 {len(DATES)} 天 {DATES[0]}~{DATES[-1]}；產業鏈 {len(CHAINS)} 條")

# ---------- 步驟 1：RRG 座標 ----------
share = {c: [] for c in CHAINS}
for dt in DATES:
    row = days[dt]
    tot = sum(row.values())
    for c in CHAINS:
        share[c].append(row.get(c, 0.0) / tot)

MA, MOM_LAG = 5, 3
ratio = {c: [None] * len(DATES) for c in CHAINS}
mom = {c: [None] * len(DATES) for c in CHAINS}
for c in CHAINS:
    s = share[c]
    for t in range(MA, len(DATES)):
        base = sum(s[t - MA:t]) / MA
        if base > 0:
            ratio[c][t] = 100 * s[t] / base
    for t in range(MA + MOM_LAG, len(DATES)):
        if ratio[c][t] is not None and ratio[c][t - MOM_LAG] is not None:
            mom[c][t] = 100 + (ratio[c][t] - ratio[c][t - MOM_LAG])

VALID = [t for t in range(len(DATES))
         if all(ratio[c][t] is not None and mom[c][t] is not None for c in CHAINS)]
print(f"完整座標日期 {len(VALID)} 個：{[DATES[t] for t in VALID]}")
assert len(VALID) >= 5, "有效日期不足 5 個"

# ---------- 畫布 / 映射 ----------
W, H, M, R = 900, 700, 60, 7
CW, CH = 13, 18   # 中文字寬 / 標籤高
GAP = 10          # 標籤離點邊緣距離

def pct(xs, p):
    xs = sorted(xs)
    k = (len(xs) - 1) * p / 100
    lo, hi = math.floor(k), math.ceil(k)
    return xs[lo] if lo == hi else xs[lo] * (hi - k) + xs[hi] * (k - lo)

allr = [ratio[c][t] for c in CHAINS for t in VALID]
allm = [mom[c][t] for c in CHAINS for t in VALID]
X0, X1 = pct(allr, 2), pct(allr, 98)
Y0, Y1 = pct(allm, 2), pct(allm, 98)
print(f"軸範圍 RS_Ratio {X0:.1f}~{X1:.1f}  RS_Mom {Y0:.1f}~{Y1:.1f}")

def px_of(v):
    v = min(max(v, X0), X1)
    return M + (v - X0) / (X1 - X0) * (W - 2 * M)

def py_of(v):
    v = min(max(v, Y0), Y1)
    return H - M - (v - Y0) / (Y1 - Y0) * (H - 2 * M)

LAST = VALID[-1]
pos = {c: (px_of(ratio[c][LAST]), py_of(mom[c][LAST])) for c in CHAINS}
amt_last = {c: days[DATES[LAST]].get(c, 0.0) for c in CHAINS}
TOT = sum(amt_last.values())
shr_last = {c: amt_last[c] / TOT * 100 for c in CHAINS}
ORDER = sorted(CHAINS, key=lambda c: -amt_last[c])

def label_rect(c):
    x, y = pos[c]
    x0 = x + R + GAP
    return (x0, y - CH / 2, x0 + CW * len(c), y + CH / 2)

def overlap(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]

# ---------- 軌跡線段 ----------
TAIL = 5
tail_idx = VALID[-(TAIL + 1):]

def segments(c):
    pts = [(px_of(ratio[c][t]), py_of(mom[c][t])) for t in tail_idx]
    return list(zip(pts, pts[1:]))

def cross(o, a, b):
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

def seg_int(s1, s2):
    p1, p2 = s1; p3, p4 = s2
    d1, d2 = cross(p3, p4, p1), cross(p3, p4, p2)
    d3, d4 = cross(p1, p2, p3), cross(p1, p2, p4)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))

SEGS = {c: segments(c) for c in CHAINS}

# ---------- 步驟 2 ----------
CX0, CX1 = W / 2 - 225, W / 2 + 225
CY0, CY1 = H / 2 - 175, H / 2 + 175

print("\n=== 步驟 2：擁擠度（最後一日 %s） ===" % DATES[LAST])
print(f"{'N':>4} {'最小距':>7} {'P5距':>7} {'中位距':>7} {'<20px配對':>10} "
      f"{'標籤重疊配對':>12} {'重疊標籤佔比':>13} {'中心1/4區佔比':>14} {'軌跡線段':>9} {'線段相交':>9}")
rows2 = {}
for N in [5, 10, 15, 20, 30, len(CHAINS)]:
    sel = ORDER[:N]
    ds = [math.dist(pos[a], pos[b]) for a, b in itertools.combinations(sel, 2)]
    near = sum(1 for d in ds if d < 20)
    rects = {c: label_rect(c) for c in sel}
    coll = 0
    hit = set()
    for a, b in itertools.combinations(sel, 2):
        if overlap(rects[a], rects[b]):
            coll += 1; hit.add(a); hit.add(b)
    cen = sum(1 for c in sel if CX0 <= pos[c][0] <= CX1 and CY0 <= pos[c][1] <= CY1)
    segs = [(c, s) for c in sel for s in SEGS[c]]
    xi = sum(1 for (ca, sa), (cb, sb) in itertools.combinations(segs, 2)
             if ca != cb and seg_int(sa, sb))
    rows2[N] = dict(coll=coll, hit=len(hit), near=near)
    print(f"{N:>4} {min(ds):>7.1f} {pct(ds,5):>7.1f} {pct(ds,50):>7.1f} {near:>10} "
          f"{coll:>12} {len(hit)/N*100:>12.0f}% {cen/N*100:>13.0f}% {len(segs):>9} {xi:>9}")

# ---------- 步驟 3：淡化方案 ----------
print("\n=== 步驟 3：半透明淡化門檻（<X% 成交佔比 → 半透明且不顯示標籤） ===")
print(f"{'X':>5} {'被淡化數':>8} {'仍有標籤':>8} {'標籤碰撞配對':>12} {'重疊標籤佔比':>13} "
      f"{'淡化成交額佔比':>15} {'淡化中位於改善象限':>18}")
base_coll = rows2[len(CHAINS)]['coll']
for X in [0.5, 1.0, 2.0, 3.0]:
    faded = [c for c in CHAINS if shr_last[c] < X]
    shown = [c for c in CHAINS if c not in faded]
    rects = {c: label_rect(c) for c in shown}
    coll = 0; hit = set()
    for a, b in itertools.combinations(shown, 2):
        if overlap(rects[a], rects[b]):
            coll += 1; hit.add(a); hit.add(b)
    lost = sum(shr_last[c] for c in faded)
    imp = [c for c in faded if ratio[c][LAST] < 100 and mom[c][LAST] > 100]
    pctlab = (len(hit) / len(shown) * 100) if shown else 0
    print(f"{X:>4}% {len(faded):>8} {len(shown):>8} {coll:>12} {pctlab:>12.0f}% "
          f"{lost:>14.1f}% {len(imp):>17}")
    print(f"        └ 改善象限被淡化的鏈：{'、'.join(imp) if imp else '（無）'}")

# 全體象限分佈 + 改善象限族群規模
q = defaultdict(list)
for c in CHAINS:
    r_, m_ = ratio[c][LAST], mom[c][LAST]
    k = ('領先' if r_ >= 100 and m_ >= 100 else '轉弱' if r_ >= 100 else
         '落後' if m_ < 100 else '改善')
    q[k].append(c)
print("\n最後一日象限分佈：", "  ".join(f"{k}={len(v)}" for k, v in sorted(q.items())))
imp_all = sorted(q['改善'], key=lambda c: -shr_last[c])
print("改善象限各鏈成交佔比：", "  ".join(f"{c}{shr_last[c]:.2f}%" for c in imp_all))

# 佔比分佈參考
print("\n成交佔比分佈（最後一日）：",
      "  ".join(f"{lo}~{hi}%:{sum(1 for c in CHAINS if lo<=shr_last[c]<hi)}"
                for lo, hi in [(0,0.5),(0.5,1),(1,2),(2,3),(3,100)]))
print("Top10 佔比：", "  ".join(f"{c}{shr_last[c]:.1f}%" for c in ORDER[:10]))

# 幾何佔位參考：47 點若均勻鋪滿畫布的平均間距
area = (W - 2 * M) * (H - 2 * M)
print(f"\n[參考] 繪圖區 {W-2*M}x{H-2*M}px，47 點理想均勻間距 = {math.sqrt(area/len(CHAINS)):.0f}px"
      f"（點直徑 14px；標籤平均寬 {sum(len(c) for c in CHAINS)/len(CHAINS)*CW:.0f}px）")

# ---------- 附加：clamp 與大小族群空間分佈 ----------
cl = [c for c in CHAINS if not (X0 < ratio[c][LAST] < X1) or not (Y0 < mom[c][LAST] < Y1)]
print(f"\n[附加] 最後一日被 clamp 到軸邊界的鏈：{len(cl)} 條 -> {'、'.join(cl)}")
big = [c for c in CHAINS if shr_last[c] >= 1.0]
sml = [c for c in CHAINS if shr_last[c] < 1.0]
for nm, grp in (('大族群>=1%', big), ('小族群<1%', sml)):
    cen = sum(1 for c in grp if CX0 <= pos[c][0] <= CX1 and CY0 <= pos[c][1] <= CY1)
    ds = [math.dist(pos[a], pos[b]) for a, b in itertools.combinations(grp, 2)]
    print(f"[附加] {nm} n={len(grp)} 中心區佔比={cen/len(grp)*100:.0f}% 內部中位距={pct(ds,50):.0f}px 最小距={min(ds):.1f}px")
# 只畫大族群點但全部畫軌跡 vs 全部
for nm, grp in (('全部47', CHAINS), ('僅>=1%共21', big)):
    segs = [(c, s) for c in grp for s in SEGS[c]]
    xi = sum(1 for (ca, sa), (cb, sb) in itertools.combinations(segs, 2) if ca != cb and seg_int(sa, sb))
    print(f"[附加] 軌跡 {nm}: 線段{len(segs)} 相交{xi} 次（每條鏈平均被穿越 {xi*2/len(grp):.1f} 次）")
