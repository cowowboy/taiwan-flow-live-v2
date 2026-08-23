#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
v11: 費半桶 x 時段 的逐年一致性 -> 組每日型 playbook
  對每個 SOX 桶、三個時段 (早盤0845-10 / 盤中10-1344 / 全日0845-1344)
  列出四年逐年平均, 只把「≥3/4 年同號且全期 |t|>=1.5」的格子納入 playbook,
  然後回測 playbook 合併淨值 (成本 2 點/趟)
"""
import numpy as np
import pandas as pd

COST = 2.0
D = pd.read_csv("output/v10_days.csv", dtype={"yr": str})

BUCKETS = [("大跌<-2%", D.sox < -0.02),
           ("跌-2~-1%", (D.sox >= -0.02) & (D.sox < -0.01)),
           ("小跌-1~0", (D.sox >= -0.01) & (D.sox < 0)),
           ("小漲0~1%", (D.sox >= 0) & (D.sox < 0.01)),
           ("漲1~2%", (D.sox >= 0.01) & (D.sox < 0.02)),
           ("大漲>2%", D.sox >= 0.02)]
SEGS = [("早盤0845-10", "m_drift"), ("盤中10-1344", "pm_drift"),
        ("全日0845-1344", "full")]

print("===== 各桶 x 時段: 逐年平均漂移 (點, 未扣成本) =====")
print(f"{'桶':<10s}{'時段':<14s}" + "".join(f"{y:>10s}" for y in
      ["2023", "2024", "2025", "2026"]) + f"{'全期':>10s}{'t':>7s}{'同號年':>6s}")
playbook = []
for bname, bm in BUCKETS:
    for sname, col in SEGS:
        cells = []
        for y in ["2023", "2024", "2025", "2026"]:
            v = D.loc[bm & (D.yr == y), col]
            cells.append(v.mean() if len(v) >= 10 else np.nan)
        allv = D.loc[bm, col].dropna()
        t = allv.mean() / (allv.std() / np.sqrt(len(allv)))
        valid = [c for c in cells if not np.isnan(c)]
        same = max(sum(1 for c in valid if c > 0), sum(1 for c in valid if c < 0))
        print(f"{bname:<10s}{sname:<14s}" +
              "".join(f"{c:>10.1f}" if not np.isnan(c) else f"{'--':>10s}"
                      for c in cells) +
              f"{allv.mean():>10.1f}{t:>7.2f}{same:>5d}/{len(valid)}")
        if same >= 3 and same == len(valid) or (same >= 3 and abs(t) >= 1.5):
            direction = 1 if allv.mean() > 0 else -1
            if abs(allv.mean()) > COST * 2:  # 期望至少蓋過兩倍成本才收
                playbook.append((bname, sname, col, direction, bm))

print("\n===== Playbook (入選格) =====")
for bname, sname, col, d, _ in playbook:
    print(f"  {bname} -> {sname} {'做多' if d==1 else '做空'}")

pnl = pd.Series(np.nan, index=D.index)
for bname, sname, col, d, bm in playbook:
    pnl[bm] = D.loc[bm, col] * d - COST
res = pd.DataFrame({"date": D.date, "yr": D.yr, "pnl": pnl}).dropna()
print(f"\n===== Playbook 合併回測: 出勤 {len(res)}/{len(D)} 天 =====")
for y, g in res.groupby("yr"):
    p = g.pnl
    print(f"  {y}: n={len(p)} 勝率={(p>0).mean():.0%} avg={p.mean():+.1f} "
          f"total={p.sum():+.0f} max_loss={p.min():.0f}")
p = res.pnl
t = p.mean() / (p.std() / np.sqrt(len(p)))
wk = res.assign(w=pd.to_datetime(res.date).dt.strftime("%G-W%V")).groupby("w").pnl.sum()
print(f"  全期: n={len(p)} 勝率={(p>0).mean():.0%} avg={p.mean():+.1f} "
      f"total={p.sum():+.0f} t={t:.2f}")
allw = pd.to_datetime(D.date).dt.strftime("%G-W%V").unique()
wkf = wk.reindex(allw, fill_value=0)
eq = res.pnl.cumsum()
mdd = (eq - eq.cummax()).min()
print(f"  週平均 {wkf.mean():+.1f} 點 (={wkf.mean()*50:+,.0f} 元/口) 週勝率 {(wkf>0).mean():.0%} "
      f"最差週 {wkf.min():.0f} 最好週 {wkf.max():.0f} 日結MDD {mdd:.0f} 點")
res.to_csv("output/v11_playbook_trades.csv", index=False)
