#!/usr/bin/env python3
# backtest/run_rrg_topn.py — RRG 設計分析①：畫布預設要放幾條？（2026-08-09 一次性）
#
# 這支不是回測，是 2026-08-09 為了決定 RRG 規格（docs/rrg-spec-20260809.md §2）
# 而做的一次性設計分析。座標公式的正式驗證在 `run_rrg.py` / `report_rrg.md`。
#
# 【回答的問題】RRG 畫布預設要放幾條族群，才能兼顧「覆蓋足夠成交額」與
#   「RRG 可讀上限 10±2 條」？名單每天會不會大搬風？
# 【判定】對 sub / chain 兩層各算：每日有成交族群數、覆蓋 50/70/80/90% 成交額所需的 k、
#   Top-k 累計佔比、第 N 名的單日佔比、相鄰日 Top-k 的交集率與 Jaccard。
# 【對照組】三份資料互為交叉驗證：
#   sub 層  = daysummary 的 subs_all（盤後正式數字）
#   sub 層  = intraday g 矩陣末時點（本 RRG 實際會用的來源）—— 兩者對得上才可信
#   chain 層 = 上面的 sub 層 + classify.json map[*].p 映射（跨鏈次產業金額均分）
# 【結論】Top10 覆蓋 70.7%、相鄰日名單交集 92% → 規格 §2 定「錨點＝成交額 Top 10」。
#
# 【用法】python backtest/run_rrg_topn.py（離線、免 token、只印表不寫檔）

from __future__ import annotations

import io, sys, json, glob, statistics as st
from collections import defaultdict
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

BASE = str(Path(__file__).resolve().parent.parent / 'data')
KS = [5, 6, 8, 10, 12, 15, 20]
STAB_KS = [8, 10, 12]
TAIL_RANKS = [8, 10, 12, 15]

# ---------- 建 sub -> chains 映射 ----------
cmap = json.load(open(f'{BASE}/classify.json', encoding='utf-8'))['map']
sub2chains = defaultdict(set)
for v in cmap.values():
    for chain, sub in (v.get('p') or []):
        sub2chains[sub].add(chain)
sub2chains = {k: sorted(v) for k, v in sub2chains.items()}

# ---------- 逐日資料載入 ----------
def load_sub_days():
    """sub 層：{date: {name: amt}}，來源 daysummary subs_all"""
    days = {}
    for f in sorted(glob.glob(f'{BASE}/daysummary/????-??-??.json')):
        d = json.load(open(f, encoding='utf-8'))
        if 'subs_all' not in d:
            continue
        days[d['date']] = {x['n']: x['amt_yi'] for x in d['subs_all'] if x['amt_yi'] > 0}
    return days

def load_intraday_days():
    """回傳 {date: {sub_name: amt_last_frame}}（元）"""
    days = {}
    for f in sorted(glob.glob(f'{BASE}/intraday/????-??-??.json')):
        d = json.load(open(f, encoding='utf-8'))
        g = d.get('g') or {}
        row = {}
        for sub, arr in g.items():
            if arr and arr[-1] and arr[-1] > 0:
                row[sub] = arr[-1]
        if row:
            days[d['date']] = row
    return days

def subs_to_chains(subrow):
    """次產業額彙總到產業鏈；歧義 sub 均分"""
    out = defaultdict(float)
    for sub, amt in subrow.items():
        chains = sub2chains.get(sub)
        if not chains:
            continue
        share = amt / len(chains)
        for c in chains:
            out[c] += share
    return dict(out)

# ---------- 統計 ----------
def med_rng(xs):
    return f"{st.median(xs):.0f} ({min(xs)}~{max(xs)})"

def analyze(name, days):
    dates = sorted(days)
    print(f"\n=== {name} ===  交易日數={len(dates)} ({dates[0]}~{dates[-1]})")
    counts, k_needed = [], {t: [] for t in (50, 70, 80, 90)}
    cov = {k: [] for k in KS}
    rank_share = {r: [] for r in TAIL_RANKS}
    topsets = {k: [] for k in STAB_KS}
    for dt in dates:
        row = sorted(days[dt].items(), key=lambda x: -x[1])
        total = sum(a for _, a in row)
        counts.append(len(row))
        cum = 0.0
        got = set()
        for i, (_, a) in enumerate(row, 1):
            cum += a
            for t in (50, 70, 80, 90):
                if t not in got and cum / total >= t / 100:
                    k_needed[t].append(i); got.add(t)
        for k in KS:
            cov[k].append(sum(a for _, a in row[:k]) / total * 100)
        for r in TAIL_RANKS:
            if len(row) >= r:
                rank_share[r].append(row[r-1][1] / total * 100)
        for k in STAB_KS:
            topsets[k].append(set(n for n, _ in row[:k]))

    print(f"每日有成交族群數：中位 {med_rng(counts)}")
    print("覆蓋門檻所需 k：", "  ".join(f"{t}%→{med_rng(k_needed[t])}" for t in (50, 70, 80, 90)))
    print("k   平均累計佔比")
    for k in KS:
        print(f"{k:>2}  {st.mean(cov[k]):5.1f}%")
    print("第N名平均單日佔比：", "  ".join(f"#{r}→{st.mean(rank_share[r]):.2f}%" for r in TAIL_RANKS))
    for k in STAB_KS:
        sets_ = topsets[k]
        ov = [len(a & b) / k for a, b in zip(sets_, sets_[1:])]
        jac = [len(a & b) / len(a | b) for a, b in zip(sets_, sets_[1:])]
        uniq = len(set().union(*sets_))
        print(f"Top{k} 穩定度：相鄰日交集/k={st.mean(ov)*100:.0f}%  Jaccard={st.mean(jac):.2f}  期間曾進榜不重複數={uniq}")

sub_days = load_sub_days()
intra_days = load_intraday_days()
chain_days = {dt: subs_to_chains(row) for dt, row in intra_days.items()}

analyze("次產業層（daysummary subs_all）", sub_days)
analyze("次產業層（intraday g 交叉驗證）", intra_days)
analyze("產業鏈層（intraday g + classify p）", chain_days)

amb = [s for s, cs in sub2chains.items() if len(cs) > 1]
print(f"\n[附註] classify 次產業 {len(sub2chains)} 個，其中 {len(amb)} 個名稱橫跨多條產業鏈（金額均分處理）")
print(f"[附註] 產業鏈總數（classify）：{len(set(c for cs in sub2chains.values() for c in cs))}")
