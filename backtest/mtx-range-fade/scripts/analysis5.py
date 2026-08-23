#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
策略 v4: 為「多空都提高勝率」掃參數
  固定: 10:00-12:00 掛單(空@a-x/多@b+y, 觸價成交), 不停損, 13:20 平倉, 成本2點
  掃描: 停利 {100,150,200,250} x k {10..150} x c門檻 {0,200,400}
        x 前日方向過濾 {無, 空單只做前日漲/多單只做前日跌}
  輸出: 各側 n>=50 且 avg>0 中勝率最高的組合
"""
import numpy as np
import pandas as pd
from pathlib import Path

COST = 2.0
TARGETS = [100.0, 150.0, 200.0, 250.0]
GRID = list(range(10, 151, 10))
C_MIN = [0, 200, 400]
OUT = Path("output")

bars = pd.read_parquet("data/all_bars.parquet")

trades = []
prev_close = None
prev_ret = 0.0
for date, g in bars.groupby("date"):
    g = g.sort_index()
    pre = g.between_time("08:45", "09:59")
    win = g.between_time("10:00", "11:59")
    ebar = g.between_time("13:20", "13:44")
    if len(pre) < 30 or len(win) < 50 or not len(ebar):
        prev_close = float(g.iloc[-1]["close"]) if len(g) else prev_close
        continue
    a = float(pre["high"].max()); b = float(pre["low"].min())
    c = a - b
    o_w = float(win.iloc[0]["open"])
    close_px = float(ebar.iloc[0]["open"])
    scan = g.between_time("10:00", "13:19")
    hi = scan["high"].to_numpy(); lo = scan["low"].to_numpy()
    win_hi = win["high"].to_numpy(); win_lo = win["low"].to_numpy()

    for k in GRID:
        for side, L_s, L_l in [("short", a - k, None), ("long", None, b + k)]:
            if side == "short":
                L = L_s
                if o_w >= L:
                    e, ei = o_w, 0
                else:
                    hits = np.nonzero(win_hi >= L)[0]
                    e, ei = (L, int(hits[0])) if len(hits) else (None, None)
            else:
                L = L_l
                if o_w <= L:
                    e, ei = o_w, 0
                else:
                    hits = np.nonzero(win_lo <= L)[0]
                    e, ei = (L, int(hits[0])) if len(hits) else (None, None)
            if e is None:
                continue
            sgn = -1 if side == "short" else 1
            # 各停利水準: 找第一根達標的 K
            for tgt in TARGETS:
                pnl, how = None, "close1320"
                for j in range(ei, len(hi)):
                    fav = (e - lo[j]) if sgn == -1 else (hi[j] - e)
                    if fav >= tgt:
                        pnl, how = tgt - COST, "target"
                        break
                if pnl is None:
                    pnl = (close_px - e) * sgn - COST
                trades.append(dict(date=date, side=side, k=k, tgt=int(tgt),
                                   c=c, prev_ret=prev_ret, pnl=pnl, how=how))
    last = float(g.iloc[-1]["close"])
    if prev_close:
        prev_ret = last - prev_close
    prev_close = last

tr = pd.DataFrame(trades)
tr.to_parquet(OUT / "v4_trades.parquet")

rows = []
for side in ["short", "long"]:
    base = tr[tr.side == side]
    for cmin in C_MIN:
        s1 = base[base.c > cmin]
        for filt, mask in [("無", np.ones(len(s1), bool)),
                           ("前日反向", (s1.prev_ret > 0).to_numpy()
                            if side == "short"
                            else (s1.prev_ret < 0).to_numpy())]:
            s2 = s1[mask]
            for (k, tgt), gg in s2.groupby(["k", "tgt"]):
                p = gg.pnl
                if len(p) < 50:
                    continue
                w, l = p[p > 0], p[p <= 0]
                rows.append(dict(
                    side=side, c_min=cmin, filt=filt, k=k, tgt=tgt,
                    n=len(p), winrate=round((p > 0).mean(), 3),
                    avg=round(p.mean(), 2), total=round(p.sum(), 0),
                    max_loss=round(p.min(), 0),
                    pf=round(w.sum() / abs(l.sum()), 2)
                    if l.sum() != 0 else np.inf,
                    t=round(p.mean() / (p.std() / np.sqrt(len(p))), 2),
                    tgt_hit=round((gg.how == "target").mean(), 3)))
res = pd.DataFrame(rows)
res.to_csv(OUT / "v4_results.csv", index=False)

for side in ["short", "long"]:
    r = res[(res.side == side) & (res.avg > 0)]
    print(f"\n===== {side}: avg>0 且 n>=50, 勝率前 12 =====")
    if r.empty:
        print("(無正期望組合)")
        continue
    print(r.nlargest(12, "winrate").to_string(index=False))
