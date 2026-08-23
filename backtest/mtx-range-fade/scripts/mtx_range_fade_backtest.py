#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小台(MTX)日內區間逆勢回測
=========================
策略:
  a = 08:45-10:00 最高點, b = 08:45-10:00 最低點
  10:00-12:00 掛限價單: 空單 @ a-x / 多單 @ b+y (觸價視為成交)
  出場: 13:00-13:30 每 5 分鐘掃描 + 13:44 對照組
  掃描 x, y = 10~150 (間距10), 輸出勝率/平均損益/期望值矩陣
  另做多空判準分析(缺口/早盤趨勢/區間寬度/前日漲跌 vs 勝率)
"""
import os
import sys
import time
import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd
import requests

# ---------------- 參數 ----------------
API = "https://api.finmindtrade.com/api/v4/data"
TOKEN = os.environ.get("FINMIND_TOKEN", "")
YEARS = 3
COST_PTS = 2.0                      # 來回手續費+期交稅(點)
X_GRID = list(range(10, 151, 10))   # 空單: a - x
Y_GRID = list(range(10, 151, 10))   # 多單: b + y
EXIT_TIMES = ["13:00", "13:05", "13:10", "13:15",
              "13:20", "13:25", "13:30", "13:44"]  # 13:44=收盤前對照
MIN_TRADES = 100                    # 樣本數低於此不列入結論
WIN_TARGET = 0.80

DATA_DIR = Path("data"); BAR_DIR = DATA_DIR / "bars"
OUT_DIR = Path("output")
BAR_DIR.mkdir(parents=True, exist_ok=True)
OUT_DIR.mkdir(exist_ok=True)

END = dt.date.today()
START = END - dt.timedelta(days=YEARS * 365 + 7)


# ---------------- FinMind ----------------
def api_get(params: dict, retries: int = 6):
    p = dict(params)
    if TOKEN:
        p["token"] = TOKEN
    for i in range(retries):
        try:
            r = requests.get(API, params=p, timeout=90)
            j = r.json()
        except Exception as e:
            print(f"  [warn] {e}, retry...")
            time.sleep(min(60, 3 * 2 ** i))
            continue
        if j.get("status") == 200:
            return j.get("data", [])
        msg = str(j.get("msg", ""))
        if "level" in msg.lower():
            sys.exit(f"API 權限不足(需 Sponsor 才能抓期貨 tick): {msg}")
        print(f"  [warn] API msg: {msg}, retry...")
        time.sleep(min(120, 5 * 2 ** i))
    raise RuntimeError(f"API 失敗: {params}")


def trading_dates() -> list:
    rows = api_get({"dataset": "TaiwanFuturesDaily", "data_id": "MTX",
                    "start_date": str(START), "end_date": str(END)})
    d = sorted({r["date"] for r in rows})
    print(f"交易日: {len(d)} 天 ({d[0]} ~ {d[-1]})")
    return d


def norm_time(s) -> str:
    s = str(s)
    if ":" in s:
        return s[:8]
    s = s.split(".")[0].zfill(6)
    return f"{s[:2]}:{s[2:4]}:{s[4:6]}"


def fetch_day_bars(date: str):
    """抓一日 tick -> 近月(當日量最大、排除價差單) -> 日盤 1 分 K"""
    cache = BAR_DIR / f"{date}.parquet"
    if cache.exists():
        return pd.read_parquet(cache) if cache.stat().st_size > 0 else None
    rows = api_get({"dataset": "TaiwanFuturesTick", "data_id": "MTX",
                    "start_date": date})
    if not rows:
        cache.touch()  # 空檔案標記無資料
        return None
    df = pd.DataFrame(rows)
    if "quantity" in df.columns and "volume" not in df.columns:
        df = df.rename(columns={"quantity": "volume"})
    if "time" not in df.columns:
        # FinMind tick 無獨立 time 欄, 時間在 date 欄 ("YYYY-MM-DD HH:MM:SS")
        df["time"] = df["date"].astype(str).str.slice(11, 19)
    df["contract_date"] = df["contract_date"].astype(str)
    df = df[~df["contract_date"].str.contains("/")]
    if df.empty:
        cache.touch()
        return None
    near = df.groupby("contract_date")["volume"].sum().idxmax()
    df = df[df["contract_date"] == near].copy()
    df["t"] = df["time"].map(norm_time)
    df = df[(df["t"] >= "08:45:00") & (df["t"] <= "13:45:00")]
    if df.empty:
        cache.touch()
        return None
    df["ts"] = pd.to_datetime(date + " " + df["t"])
    df = df.sort_values("ts").set_index("ts")
    bars = df["price"].astype(float).resample("1min").ohlc()
    bars["volume"] = df["volume"].astype(float).resample("1min").sum()
    bars = bars.dropna(subset=["open"])
    bars["date"] = date
    bars.to_parquet(cache)
    return bars


def build_bars() -> pd.DataFrame:
    if (DATA_DIR / "all_bars.parquet").exists():
        return pd.read_parquet(DATA_DIR / "all_bars.parquet")
    dates = trading_dates()
    out = []
    for i, d in enumerate(dates, 1):
        cache = BAR_DIR / f"{d}.parquet"
        cached = cache.exists()
        b = fetch_day_bars(d)
        if b is not None and len(b):
            out.append(b)
        if i % 20 == 0:
            print(f"  進度 {i}/{len(dates)}")
        if not cached:
            time.sleep(0.3)  # 禮貌限速
    allb = pd.concat(out)
    allb.to_parquet(DATA_DIR / "all_bars.parquet")
    return allb


# ---------------- 回測 ----------------
def day_slices(g: pd.DataFrame):
    pre = g.between_time("08:45", "09:59")
    win = g.between_time("10:00", "11:59")
    aft = g.between_time("13:00", "13:44")
    if len(pre) < 30 or len(win) < 50 or len(aft) < 15:
        return None
    return pre, win, aft


def exit_prices(g: pd.DataFrame, aft: pd.DataFrame) -> dict:
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


def simulate(bars: pd.DataFrame):
    trades_s, trades_l, feats = [], [], []
    prev_close = None
    prev_ret = 0.0
    for date, g in bars.groupby("date"):
        g = g.sort_index()
        sl = day_slices(g)
        if sl is None:
            prev_close = float(g.iloc[-1]["close"]) if len(g) else prev_close
            continue
        pre, win, aft = sl
        a = float(pre["high"].max())
        b = float(pre["low"].min())
        o0845 = float(pre.iloc[0]["open"])
        c0959 = float(pre.iloc[-1]["close"])
        o1000 = float(win.iloc[0]["open"])
        last = float(aft.iloc[-1]["close"])
        ex = exit_prices(g, aft)

        feats.append(dict(
            date=date,
            gap=(o0845 - prev_close) if prev_close else np.nan,
            m_trend=c0959 - o0845,
            rng=a - b,
            pos10=(o1000 - b) / (a - b) if a > b else np.nan,
            prev_ret=prev_ret,
        ))

        for x in X_GRID:  # 空單 @ a-x
            L = a - x
            if o1000 >= L:
                entry, ftype = o1000, "immediate"
            else:
                hit = win[win["high"] >= L]
                if hit.empty:
                    continue
                entry, ftype = L, "touch"
            rec = dict(date=date, x=x, entry=entry, fill=ftype)
            for t, xp in ex.items():
                rec[t] = entry - xp - COST_PTS
            trades_s.append(rec)

        for y in Y_GRID:  # 多單 @ b+y
            L = b + y
            if o1000 <= L:
                entry, ftype = o1000, "immediate"
            else:
                hit = win[win["low"] <= L]
                if hit.empty:
                    continue
                entry, ftype = L, "touch"
            rec = dict(date=date, y=y, entry=entry, fill=ftype)
            for t, xp in ex.items():
                rec[t] = xp - entry - COST_PTS
            trades_l.append(rec)

        if prev_close:
            prev_ret = last - prev_close
        prev_close = last
    return (pd.DataFrame(trades_s), pd.DataFrame(trades_l),
            pd.DataFrame(feats).set_index("date"))


def aggregate(trades: pd.DataFrame, key: str) -> pd.DataFrame:
    rows = []
    for v, gg in trades.groupby(key):
        for t in EXIT_TIMES:
            pnl = gg[t].dropna()
            if pnl.empty:
                continue
            wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
            rows.append(dict(
                **{key: v}, exit=t, n=len(pnl),
                winrate=round((pnl > 0).mean(), 4),
                avg=round(pnl.mean(), 2),
                med=round(pnl.median(), 2),
                max_loss=round(pnl.min(), 1),
                pf=round(wins.sum() / abs(losses.sum()), 2)
                if losses.sum() != 0 else np.inf,
            ))
    return pd.DataFrame(rows)


def report(res: pd.DataFrame, key: str, name: str):
    print(f"\n===== {name} 勝率矩陣 ({key} x 出場時點) =====")
    print(res.pivot(index=key, columns="exit", values="winrate")
          .to_string(float_format=lambda v: f"{v:.1%}"))
    print(f"\n===== {name} 平均損益(扣成本 {COST_PTS} 點) =====")
    print(res.pivot(index=key, columns="exit", values="avg").to_string())
    q = res[(res["winrate"] >= WIN_TARGET) & (res["n"] >= MIN_TRADES)]
    print(f"\n===== {name} 勝率>={WIN_TARGET:.0%} 且 n>={MIN_TRADES} =====")
    if q.empty:
        print("(無 -> 列出樣本足夠中勝率最高前五)")
        q = res[res["n"] >= MIN_TRADES].nlargest(5, "winrate")
    print(q.sort_values("avg", ascending=False).to_string(index=False))
    return q


def best_combo(res: pd.DataFrame, key: str):
    q = res[(res["winrate"] >= WIN_TARGET) & (res["n"] >= MIN_TRADES)]
    if q.empty:
        q = res[res["n"] >= MIN_TRADES]
    r = q.sort_values(["winrate", "avg"], ascending=False).iloc[0]
    return r[key], r["exit"]


def direction_analysis(trades: pd.DataFrame, feats: pd.DataFrame,
                       key: str, val, exit_t: str, name: str):
    tt = trades[trades[key] == val].set_index("date").join(feats)
    tt = tt.dropna(subset=[exit_t])
    print(f"\n===== {name} 多空判準分析 ({key}={val}, 出場 {exit_t}, "
          f"n={len(tt)}) =====")
    out = []
    conds = {
        "開高(gap>0)": tt["gap"] > 0, "開低(gap<0)": tt["gap"] < 0,
        "早盤漲(m_trend>0)": tt["m_trend"] > 0,
        "早盤跌(m_trend<0)": tt["m_trend"] < 0,
        "區間寬(rng>中位)": tt["rng"] > tt["rng"].median(),
        "區間窄(rng<中位)": tt["rng"] < tt["rng"].median(),
        "10點價位偏高(pos10>0.6)": tt["pos10"] > 0.6,
        "10點價位偏低(pos10<0.4)": tt["pos10"] < 0.4,
        "前日漲": tt["prev_ret"] > 0, "前日跌": tt["prev_ret"] < 0,
    }
    for label, m in conds.items():
        pnl = tt.loc[m, exit_t]
        if len(pnl) < 20:
            continue
        out.append(dict(條件=label, n=len(pnl),
                        勝率=round((pnl > 0).mean(), 3),
                        平均=round(pnl.mean(), 2)))
    df = pd.DataFrame(out)
    print(df.to_string(index=False))
    df.to_csv(OUT_DIR / f"direction_{name}.csv", index=False)


def main():
    if not TOKEN:
        sys.exit("請先 export FINMIND_TOKEN=<sponsor token>")
    print("== 下載/讀取 1 分 K ==")
    bars = build_bars()
    print(f"共 {bars['date'].nunique()} 個交易日")
    print("== 回測 ==")
    ts, tl, feats = simulate(bars)
    ts.to_csv(OUT_DIR / "trades_short.csv", index=False)
    tl.to_csv(OUT_DIR / "trades_long.csv", index=False)
    rs = aggregate(ts, "x"); rl = aggregate(tl, "y")
    rs.to_csv(OUT_DIR / "results_short.csv", index=False)
    rl.to_csv(OUT_DIR / "results_long.csv", index=False)
    report(rs, "x", "空單(a-x)")
    report(rl, "y", "多單(b+y)")
    bx, bex = best_combo(rs, "x")
    by, bey = best_combo(rl, "y")
    direction_analysis(ts, feats, "x", bx, bex, "short")
    direction_analysis(tl, feats, "y", by, bey, "long")
    print("\n完成。明細與矩陣在 output/。")
    print("提醒: 觸價即成交為理想化假設; 高勝率大 x/y 組合請同時檢視 "
          "max_loss 與 pf, 避免統計幻覺。")


if __name__ == "__main__":
    main()
