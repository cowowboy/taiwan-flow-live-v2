#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓 2330 台積電 1 分 K (TaiwanStockKBar), 只留 09:00-09:59, 逐日快取"""
import os
import sys
import time
from pathlib import Path

import pandas as pd
import requests

API = "https://api.finmindtrade.com/api/v4/data"
TOKEN = os.environ.get("FINMIND_TOKEN", "")
if not TOKEN:
    sys.exit("需要 FINMIND_TOKEN")

BAR_DIR = Path("data/bars")
TS_DIR = Path("data/tsmc")
TS_DIR.mkdir(parents=True, exist_ok=True)

dates = sorted(p.stem for p in BAR_DIR.glob("202[3-6]-*.parquet")
               if p.stat().st_size > 0)
print(f"目標 {len(dates)} 天")
for i, d in enumerate(dates, 1):
    cache = TS_DIR / f"{d}.parquet"
    if cache.exists():
        continue
    for att in range(5):
        try:
            r = requests.get(API, params=dict(
                dataset="TaiwanStockKBar", data_id="2330",
                start_date=d, token=TOKEN), timeout=90)
            j = r.json()
        except Exception as e:
            print(f"  [warn] {d} {e}"); time.sleep(5 * 2 ** att); continue
        if j.get("status") == 200:
            df = pd.DataFrame(j.get("data", []))
            if df.empty:
                cache.touch()
            else:
                df = df[(df["minute"] >= "09:00:00")
                        & (df["minute"] <= "09:59:00")]
                df.to_parquet(cache)
            break
        print(f"  [warn] {d} msg={j.get('msg')}"); time.sleep(10 * 2 ** att)
    else:
        print(f"  [fail] {d}")
    if i % 20 == 0:
        print(f"  進度 {i}/{len(dates)}")
    time.sleep(0.3)
print("完成")
