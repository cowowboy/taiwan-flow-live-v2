#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
策略 v6 (2026): 台積電 09:00-10:00 方向訊號, 僅做單邊
  訊號: 2330 09:59 收盤 vs 09:00 開盤 -> 漲=只做多 / 跌=只做空 / 平=不做
  S1 逆勢: 多->限價 b+k 接低點 / 空->限價 a-k 接高點 (10:00-12:00 觸價)
  S2 順勢: 多->突破 a+m 追進   / 空->跌破 b-m 追進   (10:00-12:00 觸價)
  出場: 停利 {100,150,200,250,300}; 未觸利 -> {13:00,13:10,13:20,13:30,13:44}
  不停損, 成本 2 點; 另附 c>400 過濾對照
"""
import numpy as np
import pandas as pd
from pathlib import Path

COST = 2.0
K_GRID = list(range(10, 151, 10))          # S1 距離
M_GRID = [0, 10, 20, 30, 50, 70, 100]      # S2 突破幅度
TARGETS = [100, 150, 200, 250, 300]
EXITS = ["13:00", "13:10", "13:20", "13:30", "13:44"]
OUT = Path("output")

bars = pd.read_parquet("data/all_bars.parquet")
bars = bars[bars["date"] >= "2026-01-01"]

# --- 台積電訊號 ---
sig = {}
for p in sorted(Path("data/tsmc").glob("*.parquet")):
    if p.stat().st_size == 0:
        continue
    df = pd.read_parquet(p)
    if df.empty:
        continue
    o = float(df.iloc[0]["open"]); c = float(df.iloc[-1]["close"])
    sig[p.stem] = 1 if c > o else (-1 if c < o else 0)
print(f"台積電訊號覆蓋 {len(sig)} 天 "
      f"(多 {sum(1 for v in sig.values() if v==1)} / "
      f"空 {sum(1 for v in sig.values() if v==-1)} / "
      f"平 {sum(1 for v in sig.values() if v==0)})")

rows = []
for date, g in bars.groupby("date"):
    s = sig.get(date, 0)
    if s == 0:
        continue
    g = g.sort_index()
    pre = g.between_time("08:45", "09:59")
    win = g.between_time("10:00", "11:59")
    rest = g.between_time("10:00", "13:44")
    if len(pre) < 30 or len(win) < 50 or len(rest) < 60:
        continue
    a = float(pre["high"].max()); b = float(pre["low"].min())
    c_rng = a - b
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

    def emit(strat, dist, e, ei, sgn):
        fav = (e - lo) if sgn == -1 else (hi - e)
        fav = fav.copy(); fav[:ei] = -1e9
        for tgt in TARGETS:
            ok = np.nonzero(fav >= tgt)[0]
            first = int(ok[0]) if len(ok) else len(op) + 1
            for t in EXITS:
                if exit_ix[t] <= ei:
                    continue
                if first < exit_ix[t]:
                    pnl, how = tgt - COST, "target"
                else:
                    pnl = (exit_px[t] - e) * sgn - COST
                    how = "close"
                rows.append(dict(date=date, strat=strat, dir=sgn, dist=dist,
                                 tgt=tgt, ex=t, c=c_rng, pnl=pnl, how=how))

    # S1 逆勢: 多接低 b+k / 空接高 a-k
    for k in K_GRID:
        if s == 1:
            L = b + k
            if o_w <= L:
                emit("S1逆勢", k, o_w, 0, 1)
            else:
                h = np.nonzero(win_lo <= L)[0]
                if len(h):
                    emit("S1逆勢", k, L, int(h[0]), 1)
        else:
            L = a - k
            if o_w >= L:
                emit("S1逆勢", k, o_w, 0, -1)
            else:
                h = np.nonzero(win_hi >= L)[0]
                if len(h):
                    emit("S1逆勢", k, L, int(h[0]), -1)
    # S2 順勢: 多破高 a+m / 空破低 b-m
    for m in M_GRID:
        if s == 1:
            L = a + m
            if o_w >= L:
                emit("S2順勢", m, o_w, 0, 1)
            else:
                h = np.nonzero(win_hi >= L)[0]
                if len(h):
                    emit("S2順勢", m, L, int(h[0]), 1)
        else:
            L = b - m
            if o_w <= L:
                emit("S2順勢", m, o_w, 0, -1)
            else:
                h = np.nonzero(win_lo <= L)[0]
                if len(h):
                    emit("S2順勢", m, L, int(h[0]), -1)

tr = pd.DataFrame(rows)
tr.to_parquet(OUT / "v6_trades_2026.parquet")

def agg(df, label):
    out = []
    for (strat, dist, tgt, ex), gg in df.groupby(["strat", "dist", "tgt", "ex"]):
        p = gg.pnl
        if len(p) < 30:
            continue
        w, l = p[p > 0], p[p <= 0]
        out.append(dict(strat=strat, dist=dist, tgt=tgt, ex=ex, n=len(p),
                        winrate=round((p > 0).mean(), 3),
                        avg=round(p.mean(), 2), total=round(p.sum(), 0),
                        max_loss=round(p.min(), 0),
                        pf=round(w.sum() / abs(l.sum()), 2)
                        if l.sum() != 0 else np.inf,
                        tgt_hit=round((gg.how == "target").mean(), 3)))
    r = pd.DataFrame(out)
    if r.empty:
        print(f"\n===== {label}: 無足量組合 =====")
        return r
    for strat in r.strat.unique():
        q = r[(r.strat == strat) & (r.avg > 0)]
        print(f"\n===== {label} / {strat}: avg>0, 依 avg 前 8 =====")
        print(q.nlargest(8, "avg").to_string(index=False)
              if not q.empty else "(無正期望組合)")
        # 200 點停利的最佳出場點
        q200 = r[(r.strat == strat) & (r.tgt == 200)]
        print(f"--- {strat} 停利200 各出場點 (dist 取 avg 最高者) ---")
        if not q200.empty:
            best_d = q200.groupby("dist").avg.mean().idxmax()
            print(q200[q200.dist == best_d].sort_values("ex").to_string(index=False))
    return r

r_all = agg(tr, "全部日")
r_c = agg(tr[tr.c > 400], "c>400 日")
if r_all is not None and not r_all.empty:
    r_all.to_csv(OUT / "v6_results_all.csv", index=False)
if r_c is not None and not r_c.empty:
    r_c.to_csv(OUT / "v6_results_c400.csv", index=False)
