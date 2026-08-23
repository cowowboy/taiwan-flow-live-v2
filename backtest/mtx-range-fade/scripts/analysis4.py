#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
策略 v3: c>400 過濾 + 停利250 + 不停損 + 13:20 平倉
  a,b = 08:45-10:00 高低點, c = a-b
  10:00-12:00 掛單 空@a-x / 多@b+y (觸價成交, o1000 已越過則視同市價)
  進場後: 獲利達 +250 即平倉(掃到 13:19), 否則 13:20 該分 K 開盤價平倉
"""
import numpy as np
import pandas as pd
from pathlib import Path

COST = 2.0
TARGET = 250.0
GRID = list(range(10, 151, 10))
OUT = Path("output")

bars = pd.read_parquet("data/all_bars.parquet")

trades = []
for date, g in bars.groupby("date"):
    g = g.sort_index()
    pre = g.between_time("08:45", "09:59")
    win = g.between_time("10:00", "11:59")
    if len(pre) < 30 or len(win) < 50:
        continue
    a = float(pre["high"].max()); b = float(pre["low"].min())
    c = a - b
    o_w = float(win.iloc[0]["open"])
    scan = g.between_time("10:00", "13:19")      # 停利偵測範圍
    ebar = g.between_time("13:20", "13:44")      # 平倉價: 13:20 起第一根開盤
    if not len(ebar):
        continue
    close_px = float(ebar.iloc[0]["open"])
    hi = scan["high"].to_numpy(); lo = scan["low"].to_numpy()
    win_hi = win["high"].to_numpy(); win_lo = win["low"].to_numpy()

    def run_exit(side, entry, ei):
        for j in range(ei, len(hi)):
            if side == -1 and entry - lo[j] >= TARGET:
                return TARGET - COST, "target"
            if side == +1 and hi[j] - entry >= TARGET:
                return TARGET - COST, "target"
        return (close_px - entry) * side - COST, "close1320"

    for k in GRID:
        L = a - k                                 # 空單
        if o_w >= L:
            e, ei = o_w, 0
        else:
            hits = np.nonzero(win_hi >= L)[0]
            e, ei = (L, int(hits[0])) if len(hits) else (None, None)
        if e is not None:
            pnl, how = run_exit(-1, e, ei)
            trades.append(dict(date=date, side="short", k=k, c=c,
                               pnl=pnl, how=how))
        L = b + k                                 # 多單
        if o_w <= L:
            e, ei = o_w, 0
        else:
            hits = np.nonzero(win_lo <= L)[0]
            e, ei = (L, int(hits[0])) if len(hits) else (None, None)
        if e is not None:
            pnl, how = run_exit(+1, e, ei)
            trades.append(dict(date=date, side="long", k=k, c=c,
                               pnl=pnl, how=how))

tr = pd.DataFrame(trades)
tr.to_csv(OUT / "v3_trades.csv", index=False)
assert tr[tr.how == "target"].pnl.eq(TARGET - COST).all()

days = tr.groupby("date").c.first()
n400 = (days > 400).sum()
print(f"有效交易日 {len(days)}, c>400 共 {n400} 天 ({n400/len(days):.1%}); 逐年:")
d400 = days[days > 400]
print(d400.groupby(d400.index.str[:4]).size().to_string())

sub = tr[tr.c > 400]
rows = []
for (side, k), gg in sub.groupby(["side", "k"]):
    p = gg.pnl
    w, l = p[p > 0], p[p <= 0]
    t = p.mean() / (p.std() / np.sqrt(len(p))) if len(p) > 1 else np.nan
    rows.append(dict(side=side, k=k, n=len(gg),
                     winrate=round((p > 0).mean(), 3),
                     avg=round(p.mean(), 2), total=round(p.sum(), 0),
                     med=round(p.median(), 1), max_loss=round(p.min(), 0),
                     pf=round(w.sum() / abs(l.sum()), 2)
                     if l.sum() != 0 else np.inf,
                     t=round(t, 2),
                     tgt=round((gg.how == "target").mean(), 3)))
res = pd.DataFrame(rows)
res.to_csv(OUT / "v3_results.csv", index=False)
for side in ["short", "long"]:
    r = res[res.side == side].sort_values("k")
    print(f"\n===== {side} (c>400, 停利250/不停損/13:20平倉) =====")
    print(r.to_string(index=False))
