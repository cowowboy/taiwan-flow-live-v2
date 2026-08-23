#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
策略 v5: 2026 年單年調參
  固定: 10:00-12:00 掛單(空@a-x/多@b+y), 不停損, 成本2點
  掃描: k{10..150} x 停利{50,75,100,150,200,250} x 平倉{12:00..13:44}
        x c門檻{100,200,300,400} x 前日方向過濾{無,反向}
  穩定性: 2026-01~04 vs 2026-05~08 兩半各自的 avg
"""
import numpy as np
import pandas as pd
from pathlib import Path

COST = 2.0
GRID = list(range(10, 151, 10))
TARGETS = [50, 75, 100, 150, 200, 250]
EXITS = ["12:00", "12:30", "13:00", "13:10", "13:20", "13:30", "13:44"]
C_MIN = [100, 200, 300, 400]
OUT = Path("output")

bars = pd.read_parquet("data/all_bars.parquet")
bars = bars[bars["date"] >= "2026-01-01"]

rows = []
prev_close, prev_ret = None, 0.0
for date, g in bars.groupby("date"):
    g = g.sort_index()
    pre = g.between_time("08:45", "09:59")
    win = g.between_time("10:00", "11:59")
    rest = g.between_time("10:00", "13:44")
    if len(pre) < 30 or len(win) < 50 or len(rest) < 60:
        prev_close = float(g.iloc[-1]["close"]) if len(g) else prev_close
        continue
    a = float(pre["high"].max()); b = float(pre["low"].min())
    c = a - b
    o_w = float(win.iloc[0]["open"])
    hi = rest["high"].to_numpy(); lo = rest["low"].to_numpy()
    op = rest["open"].to_numpy(); cl = rest["close"].to_numpy()
    tstr = rest.index.strftime("%H:%M")
    # 各平倉時點在 rest 的位置與平倉價
    exit_ix, exit_px = {}, {}
    for t in EXITS:
        pos = int(np.searchsorted(tstr, t))
        if t == "13:44" or pos >= len(op):
            exit_ix[t], exit_px[t] = len(op), float(cl[-1])
        else:
            exit_ix[t], exit_px[t] = pos, float(op[pos])
    win_hi = win["high"].to_numpy(); win_lo = win["low"].to_numpy()

    for k in GRID:
        for side, sgn in [("short", -1), ("long", 1)]:
            L = a - k if side == "short" else b + k
            if (sgn == -1 and o_w >= L) or (sgn == 1 and o_w <= L):
                e, ei = o_w, 0
            else:
                hits = (np.nonzero(win_hi >= L)[0] if sgn == -1
                        else np.nonzero(win_lo <= L)[0])
                if not len(hits):
                    continue
                e, ei = L, int(hits[0])
            fav = (e - lo) if sgn == -1 else (hi - e)
            fav[:ei] = -1e9
            for tgt in TARGETS:
                ok = np.nonzero(fav >= tgt)[0]
                first = int(ok[0]) if len(ok) else len(op) + 1
                for t in EXITS:
                    if exit_ix[t] <= ei:      # 進場晚於平倉時點 -> 無此交易
                        continue
                    if first < exit_ix[t]:
                        pnl, how = tgt - COST, "target"
                    else:
                        pnl = (exit_px[t] - e) * sgn - COST
                        how = "close"
                    rows.append(dict(date=date, side=side, k=k, tgt=tgt,
                                     ex=t, c=c, prev_ret=prev_ret,
                                     pnl=pnl, how=how))
    last = float(g.iloc[-1]["close"])
    if prev_close:
        prev_ret = last - prev_close
    prev_close = last

tr = pd.DataFrame(rows)
tr.to_parquet(OUT / "v5_trades_2026.parquet")
print(f"2026 有效交易日 {tr.date.nunique()} 天, 逐筆組合 {len(tr)} 列")

agg = []
H2 = "2026-05-01"
for side in ["short", "long"]:
    base = tr[tr.side == side]
    for cmin in C_MIN:
        s1 = base[base.c > cmin]
        for filt in ["無", "前日反向"]:
            s2 = (s1 if filt == "無" else
                  s1[s1.prev_ret > 0] if side == "short" else
                  s1[s1.prev_ret < 0]) if filt == "前日反向" else s1
            for (k, tgt, ex), gg in s2.groupby(["k", "tgt", "ex"]):
                p = gg.pnl
                if len(p) < 40:
                    continue
                w, l = p[p > 0], p[p <= 0]
                h1, h2 = gg[gg.date < H2].pnl, gg[gg.date >= H2].pnl
                agg.append(dict(
                    side=side, c_min=cmin, filt=filt, k=k, tgt=tgt, ex=ex,
                    n=len(p), winrate=round((p > 0).mean(), 3),
                    avg=round(p.mean(), 2), total=round(p.sum(), 0),
                    max_loss=round(p.min(), 0),
                    pf=round(w.sum() / abs(l.sum()), 2)
                    if l.sum() != 0 else np.inf,
                    avg_h1=round(h1.mean(), 1) if len(h1) >= 10 else np.nan,
                    avg_h2=round(h2.mean(), 1) if len(h2) >= 10 else np.nan))
res = pd.DataFrame(agg)
res.to_csv(OUT / "v5_results_2026.csv", index=False)

for side in ["short", "long"]:
    r = res[(res.side == side) & (res.avg > 0)]
    both = r[(r.avg_h1 > 0) & (r.avg_h2 > 0)]
    print(f"\n===== {side}: avg>0, n>=40, 兩半年皆正, 勝率前 10 =====")
    print(both.nlargest(10, "winrate").to_string(index=False)
          if not both.empty else "(無)")
    print(f"\n===== {side}: 總損益前 5 (不要求兩半皆正) =====")
    print(r.nlargest(5, "total").to_string(index=False))
