#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
策略 v7: S2 順勢突破優化 (2026)
  基線: 台積電早盤定向 -> 突破 a+m / b-m 同向進場, 停利, 13:xx 平倉, 不停損
  新槓桿:
    strength: 台積電早盤漲跌幅絕對值門檻 {0, 0.2%, 0.5%}
    agree:    小台早盤趨勢與訊號同向才進場 {False, True}
    B(保本):  浮盈達 B 點後回到進場價即保本出場 {None, 50, 80, 120}
  掃描: m {10..50} x tgt {150,200,250,300} x exit {13:20,13:30,13:44}
"""
import numpy as np
import pandas as pd
from pathlib import Path

COST = 2.0
M_GRID = [10, 20, 30, 40, 50]
TARGETS = [150, 200, 250, 300]
EXITS = ["13:20", "13:30", "13:44"]
BE_GRID = [None, 50, 80, 120]
OUT = Path("output")

bars = pd.read_parquet("data/all_bars.parquet")
bars = bars[bars["date"] >= "2026-01-01"]

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
    o_pre = float(pre.iloc[0]["open"]); c_pre = float(pre.iloc[-1]["close"])
    mtx_dir = 1 if c_pre > o_pre else (-1 if c_pre < o_pre else 0)
    o_w = float(win.iloc[0]["open"])
    hi = rest["high"].to_numpy(); lo = rest["low"].to_numpy()
    op = rest["open"].to_numpy(); cl = rest["close"].to_numpy()
    tstr = rest.index.strftime("%H:%M")
    exit_ix, exit_px = {}, {}
    for t in EXITS:
        pos = int(np.searchsorted(tstr, t))
        if t == "13:44" or pos >= len(op):
            exit_ix[t], exit_px[t] = len(op), float(cl[-1])
        else:
            exit_ix[t], exit_px[t] = pos, float(op[pos])
    win_hi = win["high"].to_numpy(); win_lo = win["low"].to_numpy()

    for m in M_GRID:
        L = a + m if s == 1 else b - m
        if (s == 1 and o_w >= L) or (s == -1 and o_w <= L):
            e, ei = o_w, 0
        else:
            h = (np.nonzero(win_hi >= L)[0] if s == 1
                 else np.nonzero(win_lo <= L)[0])
            if not len(h):
                continue
            e, ei = L, int(h[0])
        for B in BE_GRID:
            for tgt in TARGETS:
                # 逐 K 走: 停利 > (已武裝)保本 > 到時平倉
                res = {}
                armed = False
                done = None  # (bar_idx, pnl, how)
                for j in range(ei, len(op)):
                    fav = (hi[j] - e) if s == 1 else (e - lo[j])
                    adv_hit = (lo[j] <= e) if s == 1 else (hi[j] >= e)
                    if fav >= tgt:
                        done = (j, tgt - COST, "target"); break
                    if B is not None and armed and adv_hit and j > ei:
                        done = (j, -COST, "breakeven"); break
                    if B is not None and not armed and fav >= B:
                        armed = True
                    # 同一根先武裝不觸發保本(保守: 下一根才生效)
                for t in EXITS:
                    if exit_ix[t] <= ei:
                        continue
                    if done is not None and done[0] < exit_ix[t]:
                        pnl, how = done[1], done[2]
                    else:
                        pnl = (exit_px[t] - e) * s - COST
                        how = "close"
                    rows.append(dict(date=date, m=m, tgt=tgt, ex=t,
                                     B=(B if B is not None else 0),
                                     strength=strength,
                                     agree=(mtx_dir == s),
                                     pnl=pnl, how=how))

tr = pd.DataFrame(rows)
tr.to_parquet(OUT / "v7_trades_2026.parquet")

agg = []
for str_min in [0.0, 0.002, 0.005]:
    for need_agree in [False, True]:
        base = tr[tr.strength >= str_min]
        if need_agree:
            base = base[base.agree]
        for (m, tgt, ex, B), gg in base.groupby(["m", "tgt", "ex", "B"]):
            p = gg.pnl
            if len(p) < 30:
                continue
            w, l = p[p > 0], p[p <= 0]
            agg.append(dict(
                str_min=str_min, agree=need_agree, m=m, tgt=tgt, ex=ex, B=B,
                n=len(p), n_loss=int((p < 0).sum()),
                n_loss100=int((p < -100).sum()),
                winrate=round((p > 0).mean(), 3),
                avg=round(p.mean(), 2), total=round(p.sum(), 0),
                max_loss=round(p.min(), 0),
                pf=round(w.sum() / abs(l.sum()), 2)
                if l.sum() != 0 else np.inf,
                be=round((gg.how == "breakeven").mean(), 3)))
res = pd.DataFrame(agg)
res.to_csv(OUT / "v7_results.csv", index=False)

base = res[(res.str_min == 0) & (~res.agree) & (res.B == 0)]
bl = base[(base.m == 20) & (base.tgt == 150) & (base.ex == "13:44")]
print("基線 (v6 最佳: m20/tgt150/13:44):")
print(bl.to_string(index=False))

print("\n===== 總獲利 top 10 =====")
print(res.nlargest(10, "total").to_string(index=False))

q = res[res.total >= 4000]
print("\n===== 總獲利>=4000 中, 虧損筆數最少 top 10 =====")
print(q.nsmallest(10, "n_loss").to_string(index=False))

q2 = res[(res.winrate >= 0.8)]
print("\n===== 勝率>=80% 中, 總獲利 top 10 =====")
print(q2.nlargest(10, "total").to_string(index=False))
