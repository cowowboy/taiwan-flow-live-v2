#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
策略 v8: 2025 樣本外驗證 (參數凍結, 全部沿用 2026 調參結果)
  四組合:
    基線   m=20 tgt=150 ex=13:44 B=None str=0
    方案A  m=40 tgt=150 ex=13:44 B=120  str=0
    方案B  m=20 tgt=150 ex=13:44 B=None str=0.5%
    方案C  m=30 tgt=150 ex=13:44 B=120  str=0.5%
  逐年 (2025 vs 2026) 輸出同一組指標
"""
import numpy as np
import pandas as pd
from pathlib import Path

COST = 2.0
CONFIGS = [
    ("基線",  20, 150, "13:44", None, 0.0),
    ("方案A", 40, 150, "13:44", 120,  0.0),
    ("方案B", 20, 150, "13:44", None, 0.005),
    ("方案C", 30, 150, "13:44", 120,  0.005),
]

bars = pd.read_parquet("data/all_bars.parquet")
bars = bars[bars["date"] >= "2023-01-01"]

sig = {}
for p in sorted(Path("data/tsmc").glob("*.parquet")):
    if p.stat().st_size == 0:
        continue
    df = pd.read_parquet(p)
    if df.empty:
        continue
    o = float(df.iloc[0]["open"]); c = float(df.iloc[-1]["close"])
    if c != o:
        sig[p.stem] = ((1 if c > o else -1), abs(c / o - 1))
cov = pd.Series({k: 1 for k in sig})
print("台積電訊號覆蓋:",
      {yr: int(n) for yr, n in cov.groupby(cov.index.str[:4]).sum().items()})

rows = []
for date, g in bars.groupby("date"):
    if date not in sig:
        continue
    s, strength = sig[date]
    g = g.sort_index()
    pre = g.between_time("08:45", "09:59")
    win = g.between_time("10:00", "11:59")
    rest = g.between_time("10:00", "13:44")
    if len(pre) < 30 or len(win) < 50 or len(rest) < 60:
        continue
    a = float(pre["high"].max()); b = float(pre["low"].min())
    o_w = float(win.iloc[0]["open"])
    hi = rest["high"].to_numpy(); lo = rest["low"].to_numpy()
    op = rest["open"].to_numpy(); cl = rest["close"].to_numpy()
    win_hi = win["high"].to_numpy(); win_lo = win["low"].to_numpy()

    for name, m, tgt, ex, B, str_min in CONFIGS:
        if strength < str_min:
            continue
        L = a + m if s == 1 else b - m
        if (s == 1 and o_w >= L) or (s == -1 and o_w <= L):
            e, ei = o_w, 0
        else:
            h = (np.nonzero(win_hi >= L)[0] if s == 1
                 else np.nonzero(win_lo <= L)[0])
            if not len(h):
                continue
            e, ei = L, int(h[0])
        armed, done = False, None
        for j in range(ei, len(op)):
            fav = (hi[j] - e) if s == 1 else (e - lo[j])
            adv = (lo[j] <= e) if s == 1 else (hi[j] >= e)
            if fav >= tgt:
                done = (j, tgt - COST, "target"); break
            if B is not None and armed and adv and j > ei:
                done = (j, -COST, "breakeven"); break
            if B is not None and not armed and fav >= B:
                armed = True
        if done is not None:
            pnl, how = done[1], done[2]
        else:
            pnl, how = (float(cl[-1]) - e) * s - COST, "close"
        rows.append(dict(date=date, cfg=name, pnl=pnl, how=how))

tr = pd.DataFrame(rows)
tr.to_csv("output/v8_oos_trades.csv", index=False)
tr["yr"] = tr.date.str[:4]
for name, *_ in CONFIGS:
    print(f"\n===== {name} =====")
    for yr, gg in tr[tr.cfg == name].groupby("yr"):
        p = gg.pnl
        w, l = p[p > 0], p[p <= 0]
        wk = p.groupby(pd.to_datetime(gg.date).dt.strftime("%G-W%V")).sum()
        tv = p.mean() / (p.std() / np.sqrt(len(p)))
        tag = "(樣本外)" if yr == "2025" else "(樣本內)"
        print(f" {yr}{tag}: n={len(p)} 勝率={(p>0).mean():.1%} "
              f"avg={p.mean():+.1f} total={p.sum():+.0f} "
              f"虧損筆數={(p<0).sum()} (>100點: {(p<-100).sum()}) "
              f"max_loss={p.min():.0f} "
              f"pf={w.sum()/abs(l.sum()) if l.sum() else float('inf'):.2f} "
              f"t={tv:.2f} 週平均={p.sum()/max(1,wk.index.nunique()):+.1f}點")
