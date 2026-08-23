#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
策略 v2: 區間過濾 + 停利/停損
  a,b = 08:45-10:00 高低點, c = a-b
  僅 c > d 的日子交易; 10:00-12:00 掛單 空@a-x / 多@b+y
  進場後: 獲利達 +400 平倉 / 虧損達 -100 停損 / 皆未觸 -> 13:44 收盤平倉
  同一根 K 若同時觸利觸損, 保守假設先停損
  掃 d x (x|y), 輸出勝率/平均/出場型態
"""
import numpy as np
import pandas as pd
from pathlib import Path

COST = 2.0
TARGET, STOP = 400.0, 100.0
GRID = list(range(10, 151, 10))
D_GRID = [0, 50, 100, 150, 200, 250]
OUT = Path("output")

bars = pd.read_parquet("data/all_bars.parquet")

trades = []
for date, g in bars.groupby("date"):
    g = g.sort_index()
    pre = g.between_time("08:45", "09:59")
    win = g.between_time("10:00", "11:59")
    aft = g.between_time("13:00", "13:44")
    if len(pre) < 30 or len(win) < 50 or len(aft) < 15:
        continue
    a = float(pre["high"].max()); b = float(pre["low"].min())
    c = a - b
    o_w = float(win.iloc[0]["open"])
    # 進場後可掃描的 K (從進場那根到 13:44)
    day_rest = g.between_time("10:00", "13:44")
    hi = day_rest["high"].to_numpy(); lo = day_rest["low"].to_numpy()
    cl = day_rest["close"].to_numpy(); idx = day_rest.index

    def run_exit(side, entry, ei):
        """side=+1 多 / -1 空; ei = 進場 K 在 day_rest 的位置"""
        for j in range(ei, len(hi)):
            if side == -1:
                if hi[j] - entry >= STOP:          # 先看停損(保守)
                    return -STOP - COST, "stop"
                if entry - lo[j] >= TARGET:
                    return TARGET - COST, "target"
            else:
                if entry - lo[j] >= STOP:
                    return -STOP - COST, "stop"
                if hi[j] - entry >= TARGET:
                    return TARGET - COST, "target"
        pnl = (cl[-1] - entry) * side - COST
        return pnl, "close"

    win_hi = win["high"].to_numpy(); win_lo = win["low"].to_numpy()
    n_win = len(win)
    for k in GRID:
        # 空單 @ a-k
        L = a - k
        if o_w >= L:
            e, ei = o_w, 0
        else:
            hits = np.nonzero(win_hi >= L)[0]
            e, ei = (L, int(hits[0])) if len(hits) else (None, None)
        if e is not None:
            pnl, how = run_exit(-1, e, ei)
            trades.append(dict(date=date, side="short", k=k, c=c,
                               pnl=pnl, how=how))
        # 多單 @ b+k
        L = b + k
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
tr.to_csv(OUT / "v2_trades.csv", index=False)

# sanity: PnL 邊界
assert tr[tr.how == "target"].pnl.eq(TARGET - COST).all()
assert tr[tr.how == "stop"].pnl.eq(-STOP - COST).all()
mx = tr[tr.how == "close"].pnl.abs().max()
print(f"[sanity] target/stop PnL 恆為 +{TARGET-COST}/-{STOP+COST}; "
      f"close-out |PnL| max={mx:.0f}")
print(f"[資訊] c 分布: 10%={tr.groupby('date').c.first().quantile(.1):.0f} "
      f"50%={tr.groupby('date').c.first().median():.0f} "
      f"90%={tr.groupby('date').c.first().quantile(.9):.0f}")

rows = []
for d in D_GRID:
    sub = tr[tr.c > d]
    for (side, k), gg in sub.groupby(["side", "k"]):
        pnl = gg.pnl
        wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
        rows.append(dict(
            d=d, side=side, k=k, n=len(gg),
            winrate=round((pnl > 0).mean(), 4),
            avg=round(pnl.mean(), 2),
            pf=round(wins.sum() / abs(losses.sum()), 2)
            if losses.sum() != 0 else np.inf,
            pct_target=round((gg.how == "target").mean(), 3),
            pct_stop=round((gg.how == "stop").mean(), 3),
            pct_close=round((gg.how == "close").mean(), 3),
        ))
res = pd.DataFrame(rows)
res.to_csv(OUT / "v2_results.csv", index=False)

for side in ["short", "long"]:
    r = res[res.side == side]
    print(f"\n===== {side} 平均損益矩陣 (d x {'x' if side=='short' else 'y'}) =====")
    print(r.pivot(index="k", columns="d", values="avg").to_string())
    print(f"\n===== {side} 勝率矩陣 =====")
    print(r.pivot(index="k", columns="d", values="winrate")
          .to_string(float_format=lambda v: f"{v:.1%}"))
    q = r[r.n >= 100]
    print(f"\n===== {side} 最佳組合 (n>=100, 依 avg 前 8) =====")
    print(q.nlargest(8, "avg").to_string(index=False))
