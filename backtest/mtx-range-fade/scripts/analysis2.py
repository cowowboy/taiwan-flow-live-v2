#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
後續分析:
1) 基準線: 無條件 08:45/09:00/10:00 進場 x 各出場時點的日內漂移(未扣成本)
2) 均值回歸過濾(空單只做前日漲/多單只做前日跌) + 樣本內外切分
   變體A: 區間 08:45-10:00, 掛單 10:00-12:00 (原始)
   變體B: 區間 08:45-09:00, 掛單 09:00-12:00 (9點版)
"""
import numpy as np
import pandas as pd
from pathlib import Path

COST = 2.0
EXIT_TIMES = ["13:00", "13:05", "13:10", "13:15",
              "13:20", "13:25", "13:30", "13:44"]
GRID = list(range(10, 151, 10))
SPLIT = "2025-08-14"          # 之前=train(約2年), 含當日之後=test(約1年)
OUT = Path("output")

bars = pd.read_parquet("data/all_bars.parquet")


def exit_prices(g, aft):
    ex = {}
    for t in EXIT_TIMES:
        if t == "13:44":
            ex[t] = float(aft.iloc[-1]["close"])
            continue
        bar = g.between_time(t, t)
        if len(bar):
            ex[t] = float(bar.iloc[0]["open"])
        else:
            seg = g.between_time(t, "13:44")
            ex[t] = float(seg.iloc[0]["open"]) if len(seg) else np.nan
    return ex


# ---------- 1) 基準線 ----------
rows = []
for date, g in bars.groupby("date"):
    g = g.sort_index()
    aft = g.between_time("13:00", "13:44")
    if len(aft) < 15:
        continue
    ex = exit_prices(g, aft)
    for tt in ["08:45", "09:00", "10:00"]:
        bar = g.between_time(tt, tt)
        if not len(bar):
            continue
        e = float(bar.iloc[0]["open"])
        r = dict(date=date, entry=tt)
        for t, xp in ex.items():
            r[t] = xp - e          # 多方向, 未扣成本; 空方向=反號
        rows.append(r)
bl = pd.DataFrame(rows)
bl.to_csv(OUT / "baseline_drift.csv", index=False)

print("===== 1) 基準線: 無條件進場的日內漂移 (多方向, 點, 未扣成本; 空方向=反號) =====")
for stat, fmt in [("mean", "{:.2f}"), ]:
    piv = bl.groupby("entry")[EXIT_TIMES].mean().round(2)
    print("\n平均漂移 (n={} 天):".format(bl.date.nunique()))
    print(piv.to_string())
tt10 = bl[bl.entry == "10:00"]
tstat = {t: tt10[t].mean() / (tt10[t].std() / np.sqrt(tt10[t].notna().sum()))
         for t in EXIT_TIMES}
print("\n10:00 進場各出場點 t 值:",
      {k: round(v, 2) for k, v in tstat.items()})
tt10 = tt10.copy(); tt10["yr"] = tt10.date.str[:4]
print("\n10:00 進場 -> 13:30 出場 逐年平均漂移:")
print(tt10.groupby("yr")["13:30"].agg(n="count", avg="mean").round(2).to_string())


# ---------- 2) fade 模擬 (參數化窗) ----------
def simulate(range_end, win_start, min_pre, min_win):
    trades_s, trades_l = [], []
    prev_close, prev_ret = None, 0.0
    for date, g in bars.groupby("date"):
        g = g.sort_index()
        pre = g.between_time("08:45", range_end)
        win = g.between_time(win_start, "11:59")
        aft = g.between_time("13:00", "13:44")
        if len(pre) < min_pre or len(win) < min_win or len(aft) < 15:
            prev_close = float(g.iloc[-1]["close"]) if len(g) else prev_close
            continue
        a = float(pre["high"].max()); b = float(pre["low"].min())
        o_w = float(win.iloc[0]["open"])
        last = float(aft.iloc[-1]["close"])
        ex = exit_prices(g, aft)
        for x in GRID:
            L = a - x
            if o_w >= L:
                entry, ftype = o_w, "immediate"
            else:
                hit = win[win["high"] >= L]
                if hit.empty:
                    continue
                entry, ftype = L, "touch"
            rec = dict(date=date, k=x, fill=ftype, prev_ret=prev_ret)
            for t, xp in ex.items():
                rec[t] = entry - xp - COST
            trades_s.append(rec)
        for y in GRID:
            L = b + y
            if o_w <= L:
                entry, ftype = o_w, "immediate"
            else:
                hit = win[win["low"] <= L]
                if hit.empty:
                    continue
                entry, ftype = L, "touch"
            rec = dict(date=date, k=y, fill=ftype, prev_ret=prev_ret)
            for t, xp in ex.items():
                rec[t] = xp - entry - COST
            trades_l.append(rec)
        if prev_close:
            prev_ret = last - prev_close
        prev_close = last
    return pd.DataFrame(trades_s), pd.DataFrame(trades_l)


def matrices(tr, label):
    win = tr.pivot_table(index="k", columns=None, values=EXIT_TIMES,
                         aggfunc=lambda s: (s.dropna() > 0).mean())[EXIT_TIMES]
    avg = tr.pivot_table(index="k", values=EXIT_TIMES, aggfunc="mean")[EXIT_TIMES]
    n = tr.groupby("k")["13:44"].count()
    print(f"\n--- {label} 勝率矩陣 (n: {n.min()}~{n.max()}) ---")
    print(win.to_string(float_format=lambda v: f"{v:.1%}"))
    print(f"\n--- {label} 平均損益 (扣成本 {COST} 點) ---")
    print(avg.round(2).to_string())
    return avg


def oos(tr, label):
    """train 選前3名(avg, n>=60), 到 test 驗證"""
    tra, tes = tr[tr.date < SPLIT], tr[tr.date >= SPLIT]
    rows = []
    for k, gg in tra.groupby("k"):
        for t in EXIT_TIMES:
            p = gg[t].dropna()
            if len(p) >= 60:
                rows.append(dict(k=k, exit=t, n_tr=len(p),
                                 avg_tr=p.mean(), win_tr=(p > 0).mean()))
    top = pd.DataFrame(rows).nlargest(3, "avg_tr")
    res = []
    for _, r in top.iterrows():
        p = tes[tes.k == r.k][r.exit].dropna()
        res.append(dict(k=int(r.k), exit=r.exit,
                        n_train=int(r.n_tr), avg_train=round(r.avg_tr, 2),
                        win_train=round(r.win_tr, 3),
                        n_test=len(p), avg_test=round(p.mean(), 2),
                        win_test=round((p > 0).mean(), 3)))
    print(f"\n--- {label} train前3名 -> test 驗證 (split {SPLIT}) ---")
    print(pd.DataFrame(res).to_string(index=False))


for tag, r_end, w_start, mp, mw in [
        ("變體A(區間~10:00, 掛單10:00起)", "09:59", "10:00", 30, 50),
        ("變體B(區間~09:00, 掛單09:00起)", "08:59", "09:00", 10, 100)]:
    ts, tl = simulate(r_end, w_start, mp, mw)
    fs = ts[ts.prev_ret > 0]   # 空單只做前日漲
    fl = tl[tl.prev_ret < 0]   # 多單只做前日跌
    print(f"\n\n===== 2) {tag} + 均值回歸過濾 =====")
    matrices(fs, "空單@前日漲")
    oos(fs, "空單@前日漲")
    matrices(fl, "多單@前日跌")
    oos(fl, "多單@前日跌")
    v = "A" if "A" in tag else "B"
    fs.to_csv(OUT / f"mr_short_{v}.csv", index=False)
    fl.to_csv(OUT / f"mr_long_{v}.csv", index=False)

print("\n完成: baseline_drift.csv / mr_short_A.csv / mr_long_A.csv / "
      "mr_short_B.csv / mr_long_B.csv 已寫入 output/")
