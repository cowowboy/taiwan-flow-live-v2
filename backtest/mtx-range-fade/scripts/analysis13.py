#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
v12: playbook 進場優化 — 早盤觀察(a/b/c/量) + 10點後擇價
  方向由費半桶決定(同 v11), 出場一律 13:44, 不停損, 成本2點
  進場候選 (10:00-11:59 觸價, 沒觸到=當日不進):
    E0 = 10:00 市價
    E_mid = 掛 (a+b)/2  (多單等回檔到中點 / 空單等反彈到中點)
    E_ext = 多單掛 b+20 / 空單掛 a-20 (貼近極值端)
  過濾: 早盤量能 rv = 早盤量/近20日早盤量中位數 (>1 高量 / <1 低量)
        區間 rc = c/近20日 c 中位數
  對照: v1(08:45 進場) 同日重算
"""
import numpy as np
import pandas as pd

COST = 2.0
D = pd.read_csv("output/v10_days.csv", dtype={"yr": str})
bars = pd.read_parquet("data/all_bars.parquet")

# 每日早盤觀察值與各進場的成交/出場
recs = []
for date, g in bars.groupby("date"):
    g = g.sort_index()
    pre = g.between_time("08:45", "09:59")
    win = g.between_time("10:00", "11:59")
    if len(pre) < 30 or len(win) < 50:
        continue
    a = float(pre["high"].max()); b = float(pre["low"].min())
    vol = float(pre["volume"].sum())
    o10 = float(win.iloc[0]["open"])
    c1344 = float(g.iloc[-1]["close"])
    o0845 = float(pre.iloc[0]["open"])
    win_hi = win["high"].to_numpy(); win_lo = win["low"].to_numpy()

    def fill(direction, L):
        """direction: 1 多 / -1 空; 多單掛 L 以下等跌到, 空單掛 L 以上等漲到"""
        if direction == 1:
            if o10 <= L:
                return o10
            h = np.nonzero(win_lo <= L)[0]
            return L if len(h) else None
        else:
            if o10 >= L:
                return o10
            h = np.nonzero(win_hi >= L)[0]
            return L if len(h) else None

    mid = (a + b) / 2
    recs.append(dict(
        date=date, a=a, b=b, c=a - b, vol=vol, o10=o10, o0845=o0845,
        c1344=c1344,
        long_E0=o10, long_mid=fill(1, mid), long_ext=fill(1, b + 20),
        short_E0=o10, short_mid=fill(-1, mid), short_ext=fill(-1, a - 20)))
E = pd.DataFrame(recs)
E["rv"] = E.vol / E.vol.rolling(20).median().shift(1)
E["rc"] = E.c / E.c.rolling(20).median().shift(1)
E = E.merge(D[["date", "yr", "sox"]], on="date").dropna(subset=["rv", "sox"])

BUCKETS = [("大跌<-2%", E.sox < -0.02, -1),
           ("跌-2~-1%", (E.sox >= -0.02) & (E.sox < -0.01), -1),
           ("小跌-1~0", (E.sox >= -0.01) & (E.sox < 0), 1),
           ("漲1~2%", (E.sox >= 0.01) & (E.sox < 0.02), 1),
           ("大漲>2%", E.sox >= 0.02, -1)]
ENTRIES = [("v1_0845", "o0845"), ("E0_10市價", None),
           ("E_mid中點", "mid"), ("E_ext貼邊", "ext")]

def pnl_series(sub, d, entry):
    if entry == "o0845":
        e = sub.o0845
    elif entry is None:
        e = sub.o10
    else:
        col = ("long_" if d == 1 else "short_") + ("mid" if entry == "mid" else "ext")
        e = sub[col]
    return (sub.c1344 - e) * d - COST

def yearly_line(name, sub, p):
    parts = []
    tots = {}
    for y in ["2023", "2024", "2025", "2026"]:
        v = p[sub.yr == y].dropna()
        if len(v) < 8:
            parts.append(f"{y}:--"); continue
        tots[y] = v.sum()
        parts.append(f"{y}:{v.sum():+.0f}")
    v = p.dropna()
    pos = sum(1 for x in tots.values() if x > 0)
    fillrate = len(v) / len(sub) if len(sub) else 0
    print(f"    {name:<12s} " + " ".join(f"{x:>10s}" for x in parts) +
          f"  全期avg={v.mean():+6.1f} n={len(v)} 成交率={fillrate:.0%} 正年={pos}/{len(tots)}")
    return v

print("===== 各桶 x 進場方式: 逐年總損益 (出場13:44, 未含過濾) =====")
best = {}
for bname, bm, d in BUCKETS:
    sub = E[bm]
    print(f"  {bname} ({'空' if d==-1 else '多'}, n={len(sub)})")
    for ename, entry in ENTRIES:
        p = pnl_series(sub, d, entry)
        yearly_line(ename, sub, p)
    print()

print("===== 量能/區間過濾 (以 E_mid 中點掛單為基準) =====")
for bname, bm, d in BUCKETS:
    sub = E[bm]
    p = pnl_series(sub, d, "mid")
    print(f"  {bname}:")
    for fname, fm in [("高量 rv>=1", sub.rv >= 1), ("低量 rv<1", sub.rv < 1),
                      ("寬區間 rc>=1", sub.rc >= 1), ("窄區間 rc<1", sub.rc < 1)]:
        v = p[fm].dropna()
        ys = p[fm & (sub.yr != "2026")].dropna()
        if len(v) < 25:
            continue
        print(f"    {fname:<12s} n={len(v)} avg={v.mean():+6.1f} "
              f"total={v.sum():+7.0f} | 排除2026: avg={ys.mean():+6.1f}")
    print()
E.to_csv("output/v12_entry_obs.csv", index=False)
