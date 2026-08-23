#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
策略 v9: 前一晚美股 (費半 ^SOX / 台積電 ADR TSM) 對策略的影響
  對齊: 美股 T 日收盤 -> 台股下一個交易日 (取 < D 的最近美股日)
  基底交易: v6 基線凍結參數 (台積電早盤定向, 突破 m=20, 停利150, 13:44平倉, 不停損)
  檢驗: 各美股條件下的分年績效 (2023-2025 樣本外 vs 2026 樣本內)
  另測: 以 TSM 隔夜方向取代/疊加台積電早盤訊號
"""
import numpy as np
import pandas as pd
from pathlib import Path

COST = 2.0
M, TGT = 20, 150

def us_feats(path, prefix):
    df = pd.read_parquet(path).sort_values("date").reset_index(drop=True)
    f = pd.DataFrame({
        "us_date": df.date,
        f"{prefix}_ret": df.Close.pct_change(),
        f"{prefix}_intra": df.Close / df.Open - 1,
        f"{prefix}_gap": df.Open / df.Close.shift(1) - 1,
        f"{prefix}_rng": (df.High - df.Low) / df.Close.shift(1),
    })
    return f.dropna().reset_index(drop=True)

sox = us_feats("data/us_sox.parquet", "sox")
tsm = us_feats("data/us_tsm.parquet", "tsm")
us = sox.merge(tsm, on="us_date", how="inner")
us_dates = us.us_date.to_numpy()

sig = {}
for p in sorted(Path("data/tsmc").glob("*.parquet")):
    if p.stat().st_size == 0:
        continue
    df = pd.read_parquet(p)
    if df.empty:
        continue
    o = float(df.iloc[0]["open"]); c = float(df.iloc[-1]["close"])
    if c != o:
        sig[p.stem] = 1 if c > o else -1

bars = pd.read_parquet("data/all_bars.parquet")

rows = []
for date, g in bars.groupby("date"):
    if date not in sig:
        continue
    s = sig[date]
    g = g.sort_index()
    pre = g.between_time("08:45", "09:59")
    win = g.between_time("10:00", "11:59")
    rest = g.between_time("10:00", "13:44")
    if len(pre) < 30 or len(win) < 50 or len(rest) < 60:
        continue
    a = float(pre["high"].max()); b = float(pre["low"].min())
    o_w = float(win.iloc[0]["open"])
    hi = rest["high"].to_numpy(); lo = rest["low"].to_numpy()
    cl = rest["close"].to_numpy()
    win_hi = win["high"].to_numpy(); win_lo = win["low"].to_numpy()
    L = a + M if s == 1 else b - M
    if (s == 1 and o_w >= L) or (s == -1 and o_w <= L):
        e, ei = o_w, 0
    else:
        h = (np.nonzero(win_hi >= L)[0] if s == 1
             else np.nonzero(win_lo <= L)[0])
        if not len(h):
            continue
        e, ei = L, int(h[0])
    fav = (hi - e) if s == 1 else (e - lo)
    fav[:ei] = -1e9
    ok = np.nonzero(fav >= TGT)[0]
    pnl = (TGT - COST) if len(ok) else (float(cl[-1]) - e) * s - COST
    # 對齊: 取 < date 的最近美股日
    ix = np.searchsorted(us_dates, date) - 1
    if ix < 0:
        continue
    feats = us.iloc[ix].to_dict()
    stale = (pd.Timestamp(date) - pd.Timestamp(feats["us_date"])).days
    rows.append(dict(date=date, dir=s, pnl=pnl, stale=stale, **feats))

tr = pd.DataFrame(rows)
tr["yr"] = tr.date.str[:4]
tr["oos"] = np.where(tr.yr == "2026", "2026樣本內", "2023-25樣本外")
tr.to_csv("output/v9_us_trades.csv", index=False)
print(f"交易 {len(tr)} 筆; 美股資料落後 >4 天的 {int((tr.stale>4).sum())} 筆; "
      f"對齊抽查: 台股週一應對美股週五")
chk = tr[pd.to_datetime(tr.date).dt.dayofweek == 0].head(3)
for _, r in chk.iterrows():
    print(f"  台 {r.date}(週一) <- 美 {r.us_date}"
          f"({pd.Timestamp(r.us_date).day_name()})")

def seg(name, mask):
    out = []
    for grp in ["2023-25樣本外", "2026樣本內"]:
        p = tr[mask & (tr.oos == grp)].pnl
        if len(p) < 10:
            out.append(f"{grp}: n<10")
            continue
        out.append(f"{grp}: n={len(p)} 勝率={(p>0).mean():.0%} "
                   f"avg={p.mean():+.1f} total={p.sum():+.0f}")
    print(f"{name:<28s} | " + " | ".join(out))

print("\n===== 基線全樣本 =====")
seg("全部", pd.Series(True, index=tr.index))
print("\n===== 前晚費半 (^SOX) =====")
seg("費半漲", tr.sox_ret > 0)
seg("費半跌", tr.sox_ret < 0)
seg("費半大漲>1%", tr.sox_ret > 0.01)
seg("費半大跌<-1%", tr.sox_ret < -0.01)
seg("費半震盪大 rng>2%", tr.sox_rng > 0.02)
seg("費半與訊號同向", np.sign(tr.sox_ret) == tr["dir"])
seg("費半與訊號反向", np.sign(tr.sox_ret) == -tr["dir"])
print("\n===== 前晚台積電 ADR (TSM) =====")
seg("ADR漲", tr.tsm_ret > 0)
seg("ADR跌", tr.tsm_ret < 0)
seg("ADR大漲>1%", tr.tsm_ret > 0.01)
seg("ADR大跌<-1%", tr.tsm_ret < -0.01)
seg("ADR與訊號同向", np.sign(tr.tsm_ret) == tr["dir"])
seg("ADR與訊號反向", np.sign(tr.tsm_ret) == -tr["dir"])
seg("ADR盤中走強 intra>0", tr.tsm_intra > 0)
seg("ADR盤中走弱 intra<0", tr.tsm_intra < 0)
print("\n===== 疊加條件 =====")
seg("ADR同向 且 費半同向",
    (np.sign(tr.tsm_ret) == tr["dir"]) & (np.sign(tr.sox_ret) == tr["dir"]))
seg("ADR同向 且 |ADR|>1%",
    (np.sign(tr.tsm_ret) == tr["dir"]) & (tr.tsm_ret.abs() > 0.01))
