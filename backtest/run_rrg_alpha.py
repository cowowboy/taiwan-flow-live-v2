#!/usr/bin/env python3
# backtest/run_rrg_alpha.py — RRG 設計分析③：候補清單怎麼排？（2026-08-09 一次性）
#
# 這支不是回測，是 2026-08-09 為了決定 RRG 規格（docs/rrg-spec-20260809.md §2）
# 而做的一次性設計分析。座標公式的正式驗證在 `run_rrg.py` / `report_rrg.md`。
#
# 【回答的問題】側欄「注目度候補清單」該用什麼分數排序？「方向一致性」
#   （net 位移 / 路徑長）能不能當品質指標？流動性門檻要不要設、設多高？
# 【座標】同 run_rrg_crowding.py，用日頻代理座標（MA=5、LAG=3）。
# 【判定】依成交佔比分 4 層看 consist=net/path_len 的中位與均值、Spearman 相關；
#   score=net×consist 排序 vs 純 net 排序的 Top12 名單、象限組成、與成交額 Top15 的重複；
#   候補清單的相鄰日交集率（穩定度）。
# 【對照組】純 net 排序、加「剛換象限 ×1.3」加權、先篩象限再排、流動性門檻 5~200 億。
# 【結論】方向一致性**不可用**——小族群中位 0.401 > 大族群 0.170，量到的是
#   「跳躍 vs 震盪」而跳躍正是要濾掉的東西；流動性門檻 ≥25 億會砍掉
#   水泥／金融科技／食品生技三條目標訊號 → 規格 §2 改為「標極小量灰字、不剔除」。
#   日頻版候補清單穩定度不足（相鄰日交集 40~45%），這正是改做盤中版的原因。
#
# 【用法】python backtest/run_rrg_alpha.py（離線、免 token、只印表不寫檔）

from __future__ import annotations

import io, sys, json, glob, math, itertools, statistics as st
from collections import defaultdict
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
BASE = str(Path(__file__).resolve().parent.parent / 'data')

cmap = json.load(open(f'{BASE}/classify.json', encoding='utf-8'))['map']
sub2chains = defaultdict(set)
for v in cmap.values():
    for chain, sub in (v.get('p') or []):
        sub2chains[sub].add(chain)
sub2chains = {k: sorted(v) for k, v in sub2chains.items()}

days = {}
for f in sorted(glob.glob(f'{BASE}/intraday/????-??-??.json')):
    d = json.load(open(f, encoding='utf-8')); g = d.get('g') or {}
    out = defaultdict(float)
    for sub, arr in g.items():
        if not arr or not arr[-1] or arr[-1] <= 0: continue
        ch = sub2chains.get(sub)
        if not ch: continue
        for c in ch: out[c] += arr[-1]/len(ch)
    if out: days[d['date']] = dict(out)
DATES = sorted(days)
CHAINS = sorted({c for r in days.values() for c in r})

def build(ma, lag):
    share = {c: [] for c in CHAINS}
    for dt in DATES:
        tot = sum(days[dt].values())
        for c in CHAINS: share[c].append(days[dt].get(c, 0.0)/tot)
    ra = {c: [None]*len(DATES) for c in CHAINS}
    mo = {c: [None]*len(DATES) for c in CHAINS}
    for c in CHAINS:
        s = share[c]
        for t in range(ma, len(DATES)):
            b = sum(s[t-ma:t])/ma
            if b > 0: ra[c][t] = 100*s[t]/b
        for t in range(ma+lag, len(DATES)):
            if ra[c][t] is not None and ra[c][t-lag] is not None:
                mo[c][t] = 100 + (ra[c][t]-ra[c][t-lag])
    vd = [t for t in range(len(DATES))
          if all(ra[c][t] is not None and mo[c][t] is not None for c in CHAINS)]
    return share, ra, mo, vd

share, ratio, mom, VALID = build(5, 3)
LAST = VALID[-1]
amt = {c: days[DATES[LAST]].get(c, 0.0) for c in CHAINS}
TOT = sum(amt.values())
shr = {c: amt[c]/TOT*100 for c in CHAINS}
yi = {c: amt[c]/1e8 for c in CHAINS}
print(f"最後一日 {DATES[LAST]}  全市場 {TOT/1e8:,.0f} 億  47 條鏈成交額 "
      f"最大 {max(yi.values()):.0f} 億 / 中位 {st.median(yi.values()):.1f} 億 / 最小 {min(yi.values()):.2f} 億")

def pctf(xs, p):
    xs = sorted(xs); k = (len(xs)-1)*p/100
    lo, hi = math.floor(k), math.ceil(k)
    return xs[lo] if lo == hi else xs[lo]*(hi-k)+xs[hi]*(k-lo)

# ---------- 步驟 1：成分 ----------
def traj(c, ra, mo, idxs): return [(ra[c][t], mo[c][t]) for t in idxs]
def plen(p): return sum(math.dist(a,b) for a,b in zip(p, p[1:]))

TIDX = VALID[-5:]
net = {}; length = {}; consist = {}
for c in CHAINS:
    p = traj(c, ratio, mom, TIDX)
    net[c] = math.dist(p[0], p[-1]); length[c] = plen(p)
    consist[c] = net[c]/length[c] if length[c] > 0 else 0

def quad(c, t):
    r_, m_ = ratio[c][t], mom[c][t]
    return ('領先' if r_ >= 100 and m_ >= 100 else '轉弱' if r_ >= 100
            else '落後' if m_ < 100 else '改善')
QUAD = {c: quad(c, LAST) for c in CHAINS}
PREV = VALID[-2]
qcross = {c: (quad(c, PREV) == '落後' and QUAD[c] == '改善')
             or (quad(c, PREV) == '改善' and QUAD[c] == '領先') for c in CHAINS}

print("\n=== 表1：依成交佔比分層的方向一致性（consist = net/path_len） ===")
print(f"{'分層':<9}{'n':>3}{'consist中位':>12}{'consist均值':>12}{'net中位(RS)':>12}{'len中位(RS)':>12}")
tiers = [('≥3%', 3, 1e9), ('1~3%', 1, 3), ('0.5~1%', .5, 1), ('<0.5%', 0, .5)]
for nm, lo, hi in tiers:
    g = [c for c in CHAINS if lo <= shr[c] < hi]
    print(f"{nm:<9}{len(g):>3}{pctf([consist[c] for c in g],50):>12.3f}"
          f"{st.mean([consist[c] for c in g]):>12.3f}"
          f"{pctf([net[c] for c in g],50):>12.1f}{pctf([length[c] for c in g],50):>12.1f}")

def spear(a, b):
    ra_ = {c: i for i, c in enumerate(sorted(CHAINS, key=lambda c: a[c]))}
    rb_ = {c: i for i, c in enumerate(sorted(CHAINS, key=lambda c: b[c]))}
    n = len(CHAINS)
    return 1 - 6*sum((ra_[c]-rb_[c])**2 for c in CHAINS)/(n*(n*n-1))
print(f"\nSpearman(consist, net)={spear(consist, net):+.2f}   "
      f"Spearman(consist, 佔比)={spear(consist, shr):+.2f}   "
      f"Spearman(net, 佔比)={spear(net, shr):+.2f}")

# ---------- 步驟 2：分數 ----------
FLOOR_YI, FLOOR_SH = 5.0, 0.1
elig = [c for c in CHAINS if yi[c] >= FLOOR_YI or shr[c] >= FLOOR_SH]
elig_lo = [c for c in CHAINS if yi[c] >= 1.0]
print(f"\n流動性門檻：成交額≥{FLOOR_YI}億 或 佔比≥{FLOOR_SH}% -> 合格 {len(elig)}/47"
      f"（放寬到 ≥1 億 -> {len(elig_lo)}/47）")
print("  被擋掉：", "、".join(f"{c}({yi[c]:.1f}億/{shr[c]:.2f}%)"
                          for c in CHAINS if c not in elig))

SC = {c: net[c]*consist[c] for c in CHAINS}          # = net^2/len，有向位移
SCB = {c: SC[c]*(1.3 if qcross[c] else 1.0) for c in CHAINS}
def rank(d, pool): return {c: i+1 for i, c in enumerate(sorted(pool, key=lambda c: -d[c]))}
R_SC, R_NET, R_SCB = rank(SC, elig), rank(net, elig), rank(SCB, elig)
TOP15 = set(sorted(CHAINS, key=lambda c: -amt[c])[:15])
IMP6 = ['水泥', '觸控面板', '資通訊安全', '運動科技', '金融科技', '食品生技']

print("\n=== 表2：score=net×consist 排序 Top 12（合格池 %d 條） ===" % len(elig))
print(f"{'#':>3} {'產業鏈':<11}{'佔比%':>7}{'成交億':>8}{'net':>8}{'consist':>8}"
      f"{'score':>8}{'象限':>6}{'剛換象限':>9}{'net單排':>8}{'在Top15':>8}")
for c in sorted(elig, key=lambda c: -SC[c])[:12]:
    print(f"{R_SC[c]:>3} {c:<11}{shr[c]:>7.2f}{yi[c]:>8.0f}{net[c]:>8.1f}{consist[c]:>8.2f}"
          f"{SC[c]:>8.1f}{QUAD[c]:>6}{'是' if qcross[c] else '-':>9}"
          f"{R_NET[c]:>8}{'●' if c in TOP15 else '-':>8}")
print("\n對照：只用 net 排序 Top 12")
print(f"{'#':>3} {'產業鏈':<11}{'佔比%':>7}{'net':>8}{'consist':>8}{'象限':>6}{'score排名':>9}")
for c in sorted(elig, key=lambda c: -net[c])[:12]:
    print(f"{R_NET[c]:>3} {c:<11}{shr[c]:>7.2f}{net[c]:>8.1f}{consist[c]:>8.2f}"
          f"{QUAD[c]:>6}{R_SC[c]:>9}")

print("\n=== 關鍵：改善象限小族群 6 條的排名 ===")
print(f"{'產業鏈':<11}{'佔比%':>7}{'成交億':>8}{'net':>8}{'consist':>8}"
      f"{'score排名':>9}{'net排名':>8}{'含換象限加權':>12}{'合格':>6}")
for c in IMP6:
    ok = c in elig
    print(f"{c:<11}{shr[c]:>7.2f}{yi[c]:>8.2f}{net[c]:>8.1f}{consist[c]:>8.2f}"
          f"{(R_SC.get(c,'—')):>9}{(R_NET.get(c,'—')):>8}{(R_SCB.get(c,'—')):>12}"
          f"{'○' if ok else '✕':>6}")
for K in (10, 15):
    a = sum(1 for c in IMP6 if R_SC.get(c, 99) <= K)
    b = sum(1 for c in IMP6 if R_NET.get(c, 99) <= K)
    print(f"  Top{K}：score 納入 {a}/6 條；純 net 納入 {b}/6 條")
for K in (10, 15):
    t = sorted(elig, key=lambda c: -SC[c])[:K]
    tn = sorted(elig, key=lambda c: -net[c])[:K]
    print(f"  Top{K} 與成交額 Top15 重複：score {sum(1 for c in t if c in TOP15)}/{K}"
          f"；純 net {sum(1 for c in tn if c in TOP15)}/{K}")
    print(f"  Top{K} 象限組成 score：" +
          " ".join(f"{q}{sum(1 for c in t if QUAD[c]==q)}" for q in ('改善','領先','轉弱','落後')) +
          "  ｜純 net：" +
          " ".join(f"{q}{sum(1 for c in tn if QUAD[c]==q)}" for q in ('改善','領先','轉弱','落後')))

# ---------- 步驟 3：穩定度 ----------
def score_on(ra, mo, vd, t, win):
    idx = [i for i in vd if i <= t][-win:]
    if len(idx) < win: return None
    out = {}
    for c in CHAINS:
        p = traj(c, ra, mo, idx)
        n_, l_ = math.dist(p[0], p[-1]), plen(p)
        out[c] = n_*(n_/l_) if l_ > 0 else 0
    return out

def stability(ra, mo, vd, win, label):
    tops = []
    for t in vd:
        s = score_on(ra, mo, vd, t, win)
        if s is None: continue
        pool = [c for c in CHAINS if yi[c] >= FLOOR_YI or shr[c] >= FLOOR_SH]
        tops.append((DATES[t], set(sorted(pool, key=lambda c: -s[c])[:10])))
    if len(tops) < 2:
        print(f"{label}: 可比較日數不足"); return
    ov = [len(a[1] & b[1])/10 for a, b in zip(tops, tops[1:])]
    uni = set().union(*[x[1] for x in tops])
    print(f"{label}: 可評日 {len(tops)} 天({tops[0][0]}~{tops[-1][0]})  "
          f"相鄰日 Top10 交集率 平均 {st.mean(ov)*100:.0f}% ({'/'.join(f'{o*100:.0f}%' for o in ov)})  "
          f"期間曾進 Top10 不重複 {len(uni)} 條")

print("\n=== 步驟 3：候補清單穩定度 ===")
stability(ratio, mom, VALID, 3, "主參數 MA5/LAG3，3 期視窗")
ra2, mo2, vd2 = build(3, 3)[1], build(3, 3)[2], build(3, 3)[3]
print(f"（補充參數 MA3/LAG3：有效座標日 {len(vd2)} 天，換更短均線只為取得更多可比日）")
stability(ra2, mo2, vd2, 3, "補充 MA3/LAG3，3 期視窗")
stability(ra2, mo2, vd2, 4, "補充 MA3/LAG3，4 期視窗")

# ---------- 附錄 A：流動性門檻的實際效果 ----------
print("\n=== 附錄A：流動性門檻要多高才有意義（全市場 %.0f 億） ===" % (TOT/1e8))
for f_ in (5, 25, 50, 100, 200):
    e = [c for c in CHAINS if yi[c] >= f_]
    cut6 = [c for c in IMP6 if yi[c] < f_]
    print(f"  ≥{f_:>3}億：合格 {len(e):>2}/47   擋掉的改善小族群 "
          f"{'、'.join(f'{c}({yi[c]:.0f}億)' for c in cut6) if cut6 else '（無）'}")

# ---------- 附錄 B：改用「象限分組」的候補清單 ----------
print("\n=== 附錄B：候補清單改成「先篩象限、再按 score 排」 ===")
for q in ('改善', '領先'):
    g = sorted([c for c in elig if QUAD[c] == q], key=lambda c: -SC[c])
    print(f"[{q}] 共 {len(g)} 條：" + "  ".join(
        f"{c}({shr[c]:.2f}%,net{net[c]:.0f})" for c in g[:8]))
    print(f"    其中佔比<1%：{sum(1 for c in g if shr[c]<1)} 條；"
          f"與成交額Top15 重複 {sum(1 for c in g if c in TOP15)} 條")

# ---------- 附錄 C：Top10 是不是一次性暴量 ----------
print("\n=== 附錄C：score Top10 的量能背景（share 相對前5日均 = RS_Ratio） ===")
print(f"{'產業鏈':<11}{'佔比%':>7}{'RS_Ratio':>9}{'前5日share均%':>13}{'象限':>6}")
for c in sorted(elig, key=lambda c: -SC[c])[:10]:
    base = st.mean(share[c][LAST-5:LAST])*100
    print(f"{c:<11}{shr[c]:>7.2f}{ratio[c][LAST]:>9.0f}{base:>13.3f}{QUAD[c]:>6}")

# ---------- 附錄 D：象限分組清單的穩定度 ----------
print("\n=== 附錄D：改善象限清單的穩定度（MA3/LAG3，3期視窗） ===")
tops = []
for t in vd2:
    s = score_on(ra2, mo2, vd2, t, 3)
    if s is None: continue
    g = [c for c in CHAINS if ra2[c][t] < 100 and mo2[c][t] > 100]
    tops.append((DATES[t], set(sorted(g, key=lambda c: -s[c])[:5]), len(g)))
if len(tops) >= 2:
    ov = [len(a[1] & b[1])/max(1, min(len(a[1]), len(b[1]))) for a, b in zip(tops, tops[1:])]
    print(f"  每日改善象限族群數：{[x[2] for x in tops]}")
    print(f"  相鄰日 Top5 交集率 平均 {st.mean(ov)*100:.0f}% "
          f"({'/'.join(f'{o*100:.0f}%' for o in ov)})；曾進榜不重複 {len(set().union(*[x[1] for x in tops]))} 條")
