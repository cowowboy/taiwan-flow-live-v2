#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
v10: 前晚費半漲跌 -> 台指期隔日行為的關係圖 + 每日型策略
  Part1 關係表: 依前晚 SOX 報酬分桶, 看隔日 缺口/早盤漂移/盤中漂移/跟隨機率
  Part2 每日策略 (時間進出, 無停利停損, 成本2點):
    D1 順美開盤:   08:45 依 SOX 方向進場, 持有到 13:44 (含 10:00/13:30 出場變體)
    D2 順美10點:   10:00 依 SOX 方向進場 -> 13:44
    D3 逆美10點:   10:00 依 SOX 反向進場 -> 13:44
    D4 順美早盤段: 08:45 -> 10:00
  全部輸出逐年 (2023-2026) 一致性
"""
import numpy as np
import pandas as pd
from pathlib import Path

COST = 2.0

us = pd.read_parquet("data/us_sox.parquet").sort_values("date").reset_index(drop=True)
us["sox_ret"] = us.Close.pct_change()
us = us.dropna()[["date", "sox_ret"]].rename(columns={"date": "us_date"})
us_dates = us.us_date.to_numpy()
us_ret = us.sox_ret.to_numpy()

bars = pd.read_parquet("data/all_bars.parquet")
days = []
prev_close = None
for date, g in bars.groupby("date"):
    g = g.sort_index()
    pre = g.between_time("08:45", "09:59")
    if len(pre) < 30 or len(g.between_time("13:00", "13:44")) < 15:
        prev_close = float(g.iloc[-1]["close"]) if len(g) else prev_close
        continue
    o = float(pre.iloc[0]["open"])
    p10bar = g.between_time("10:00", "10:00")
    p10 = float(p10bar.iloc[0]["open"]) if len(p10bar) else float(pre.iloc[-1]["close"])
    c1330bar = g.between_time("13:30", "13:44")
    c1330 = float(c1330bar.iloc[0]["open"]) if len(c1330bar) else np.nan
    c1344 = float(g.iloc[-1]["close"])
    ix = np.searchsorted(us_dates, date) - 1
    if ix < 0 or prev_close is None:
        prev_close = c1344
        continue
    days.append(dict(date=date, yr=date[:4], sox=us_ret[ix],
                     gap=o - prev_close, m_drift=p10 - o,
                     pm_drift=c1344 - p10, pm_drift1330=c1330 - p10,
                     full=c1344 - o, day_dir=np.sign(c1344 - prev_close)))
    prev_close = c1344
D = pd.DataFrame(days)
D.to_csv("output/v10_days.csv", index=False)
print(f"樣本 {len(D)} 天 (2023-08 ~ 2026-08)\n")

# ---- Part 1 關係表 ----
buckets = [("大跌<-2%", D.sox < -0.02), ("跌-2~-1%", (D.sox >= -0.02) & (D.sox < -0.01)),
           ("小跌-1~0", (D.sox >= -0.01) & (D.sox < 0)),
           ("小漲0~1%", (D.sox >= 0) & (D.sox < 0.01)),
           ("漲1~2%", (D.sox >= 0.01) & (D.sox < 0.02)), ("大漲>2%", D.sox >= 0.02)]
print("===== Part1: 前晚費半 -> 台指期隔日行為 (點, 平均) =====")
rows = []
for name, m in buckets:
    d = D[m]
    rows.append(dict(費半前晚=name, n=len(d),
                     開盤缺口=round(d.gap.mean(), 1),
                     早盤漂移_0845到10=round(d.m_drift.mean(), 1),
                     盤中漂移_10到1344=round(d.pm_drift.mean(), 1),
                     全日_開盤到收盤=round(d.full.mean(), 1),
                     隔日同向收率=round((d.day_dir == np.sign(d.sox)).mean(), 2)))
print(pd.DataFrame(rows).to_string(index=False))

# ---- Part 2 每日策略 ----
def yearly(name, pnl_by_day):
    s = pd.Series(pnl_by_day, index=D.date)
    s = s.dropna()
    out = [name]
    tot = {}
    for yr in ["2023", "2024", "2025", "2026"]:
        p = s[s.index.str[:4] == yr]
        if len(p) < 20:
            out.append(f"{yr}: n<20"); continue
        tot[yr] = p.sum()
        out.append(f"{yr}: n={len(p)} 勝率={(p>0).mean():.0%} "
                   f"avg={p.mean():+.1f} total={p.sum():+.0f}")
    allp = s
    t = allp.mean() / (allp.std() / np.sqrt(len(allp)))
    pos_years = sum(1 for v in tot.values() if v > 0)
    out.append(f"全期: avg={allp.mean():+.1f} t={t:.2f} 正年數={pos_years}/{len(tot)}")
    print("\n".join("  " + o if i else o for i, o in enumerate(out)))
    print()

sgn = np.sign(D.sox).replace(0, np.nan)
print("\n===== Part2: 每日型策略 (成本2點已扣, 訊號=前晚費半方向) =====\n")
yearly("D1 順美 08:45進 -> 13:44出", (D.full * sgn - COST))
yearly("D1b 順美 08:45進 -> 10:00出", (D.m_drift * sgn - COST))
yearly("D2 順美 10:00進 -> 13:44出", (D.pm_drift * sgn - COST))
yearly("D3 逆美 10:00進 -> 13:44出", (-D.pm_drift * sgn - COST))
yearly("D2b 順美 10:00進 -> 13:30出", (D.pm_drift1330 * sgn - COST))
# 幅度加權變體: 只在 |sox|>=0.5% 的日子 (仍屬高頻, 約半數日子)
big = D.sox.abs() >= 0.005
yearly("D1-0.5%門檻 順美 08:45->13:44", np.where(big, D.full * sgn - COST, np.nan))
yearly("D1b-0.5%門檻 順美 08:45->10:00", np.where(big, D.m_drift * sgn - COST, np.nan))
yearly("D2-0.5%門檻 順美 10:00->13:44", np.where(big, D.pm_drift * sgn - COST, np.nan))
