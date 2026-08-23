#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
v13 (最終): Playbook v2 — 費半桶 x 早盤觀察 的每日型策略
  前置: analysis11.py 產 v10_days.csv, analysis13.py 產 v12_entry_obs.csv
  規則 (方向=前晚費半; rv=早盤量/近20日中位數):
    大跌<-2%   : 08:45 多 -> 10:00 平
    跌-2~-1%   : 08:45 空 -> 13:44 平
    小跌-1~0   : 08:45 多 -> 13:44 平
    小漲0~1%   : 空手
    漲1~2%     : rv>=1 才 08:45 多 -> 13:44 平
    大漲>2%    : 10:00 空 -> 13:44 平
  成本 2 點, 不停損
"""
import numpy as np
import pandas as pd

COST = 2.0
E = pd.read_csv("output/v12_entry_obs.csv", dtype={"yr": str})
D = pd.read_csv("output/v10_days.csv", dtype={"yr": str})
E = E.merge(D[["date", "m_drift"]], on="date")

pnl = pd.Series(np.nan, index=E.index)
m = E.sox < -0.02
pnl[m] = E.m_drift[m] * 1 - COST
m = (E.sox >= -0.02) & (E.sox < -0.01)
pnl[m] = (E.c1344 - E.o0845) * -1 - COST
m = (E.sox >= -0.01) & (E.sox < 0)
pnl[m] = (E.c1344 - E.o0845) * 1 - COST
m = (E.sox >= 0.01) & (E.sox < 0.02) & (E.rv >= 1)
pnl[m] = (E.c1344 - E.o0845) * 1 - COST
m = E.sox >= 0.02
pnl[m] = (E.c1344 - E.o10) * -1 - COST

res = pd.DataFrame({"date": E.date, "yr": E.yr, "pnl": pnl}).dropna()
print(f"Playbook v2: 出勤 {len(res)}/{len(E)} 天")
for y, g in res.groupby("yr"):
    p = g.pnl
    print(f"  {y}: n={len(p)} 勝率={(p>0).mean():.0%} avg={p.mean():+.1f} "
          f"total={p.sum():+.0f} max_loss={p.min():.0f}")
p = res.pnl
t = p.mean() / (p.std() / np.sqrt(len(p)))
res["w"] = pd.to_datetime(res.date).dt.strftime("%G-W%V")
allw = pd.to_datetime(E.date).dt.strftime("%G-W%V").unique()
wk = res.groupby("w").pnl.sum().reindex(allw, fill_value=0)
eq = res.pnl.cumsum()
mdd = (eq - eq.cummax()).min()
print(f"  全期: avg={p.mean():+.1f} total={p.sum():+.0f} t={t:.2f}")
print(f"  週平均={wk.mean():+.1f}點 週勝率={(wk>0).mean():.0%} "
      f"最差週={wk.min():.0f} MDD={mdd:.0f}點")
res.to_csv("output/v12_playbook2_trades.csv", index=False)
