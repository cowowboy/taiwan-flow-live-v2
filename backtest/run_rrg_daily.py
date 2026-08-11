#!/usr/bin/env python3
# backtest/run_rrg_daily.py — 盤後日頻 RRG 第一階段（歷史回補）的資料品質與基本統計
#
# 本階段**刻意不做軸定義比較**（那是 docs/rrg-daily-spec-20260811.md §3 第二階段，
# 要等回補資料就位才有意義）。這支只做三件事：
#   1. 讀 data/chain_daily/series.json，確認回補範圍、缺漏、鏈集合是否與 classify 對得上
#   2. 與 data/daysummary/*.json 的 chain_top5／chain_bot3 交叉驗證，**量化**差異
#      （規格 §2 完成定義 2；不因為差異小就寫「一致」，一律給數字）
#   3. 算出第二階段選軸會用到的基本分布（每日鏈數、成交額／佔比分布、報酬分布），
#      並把交叉驗證量到的漂移換算成 RS 座標的噪音尺度（只是換算，不選軸）
# 產出 backtest/report_rrg_daily.md。
#
# 覆蓋率有三種定義，**互相不可比**（2026-08-11 驗收抓到的坑）：舊版報告拿
# 「重複計數口徑」的 63.6% 去對盤中版「均分口徑」的 70.7%，結論方向剛好相反
# （同口徑其實是 82.4%，比盤中版高）。三種定義的算法在 src/build_chain_daily.py 的
# `def topn_coverage`，逐日值落在 series.json 的 coverage 區塊，本檔只負責呈現。
#
# 用法：python3 backtest/run_rrg_daily.py [--series ...] [--out ...]
#   離線、免 FinMind token（只讀 repo 內既有檔案）。

from __future__ import annotations

import argparse
import io
import json
import math
import statistics as stats
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERIES = ROOT / "data" / "chain_daily" / "series.json"
DAYSUM = ROOT / "data" / "daysummary"
CLASSIFY = ROOT / "data" / "classify.json"
OUT = ROOT / "backtest" / "report_rrg_daily.md"

MIN_DAYS = 120          # 規格 §2 完成定義 1
TOPN = 10               # 畫布錨點條數（規格 §5：成交額 Top 10）
INTRADAY_TOP10 = 70.7   # 盤中版 backtest/run_rrg_topn.py 的「產業鏈層」Top10 覆蓋率
                        # （規格 §5 引用值，13 日樣本；2026-08-11 用 15 日重跑為 70.6%）
RS_KS = (5, 10, 14)     # 噪音換算用的移動平均期數（盤中版 K=5；實務 RRG 10~14）


# ------------------------------------------------------------------ 小工具

def pctl(xs, p):
    """線性內插分位數（同 run_rrg.py 的 pctl 語意）；空清單回 nan。"""
    v = sorted(x for x in xs if x is not None)
    if not v:
        return float("nan")
    if len(v) == 1:
        return v[0]
    k = (len(v) - 1) * p / 100.0
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return v[int(k)]
    return v[lo] * (hi - k) + v[hi] * (k - lo)


def corr(xs, ys):
    """Pearson 相關；樣本 <3 或任一邊變異為 0 → nan。"""
    pairs = [(a, b) for a, b in zip(xs, ys) if a is not None and b is not None]
    if len(pairs) < 3:
        return float("nan")
    a = [p[0] for p in pairs]
    b = [p[1] for p in pairs]
    sa, sb = stats.pstdev(a), stats.pstdev(b)
    if sa <= 0 or sb <= 0:
        return float("nan")
    ma, mb = stats.fmean(a), stats.fmean(b)
    return sum((x - ma) * (y - mb) for x, y in pairs) / (len(pairs) * sa * sb)


def fmt(x, nd=2, suffix=""):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    return f"{x:.{nd}f}{suffix}"


# ------------------------------------------------------------------ 讀檔

def load_series(path: Path = SERIES) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_daysummaries(d: Path = DAYSUM) -> list[dict]:
    out = []
    for p in sorted(d.glob("????-??-??.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:                    # noqa: BLE001
            continue
    return out


def classify_chains(path: Path = CLASSIFY) -> set[str]:
    m = json.loads(path.read_text(encoding="utf-8"))["map"]
    return {c for v in m.values() if v.get("t") == "twse" for c in (v.get("c") or [])}


# ------------------------------------------------------------------ 資料品質

def missing_weekdays(dates: list[str], nontrading: list[str]) -> list[str]:
    """回補區間內「是平日、不在序列、也不在已知非交易日名單」的日期。

    這些是真正的缺口（抓取失敗／FinMind 缺資料），與國定假日要分開看。"""
    if not dates:
        return []
    known = set(dates) | set(nontrading or [])
    lo, hi = date.fromisoformat(dates[0]), date.fromisoformat(dates[-1])
    out, d = [], lo
    while d <= hi:
        if d.weekday() < 5 and d.isoformat() not in known:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def chain_set_diff(series: dict, cls: set[str]) -> tuple[list[str], list[str]]:
    """(序列有但 classify 沒有, classify 有但序列沒有)。兩邊都空才算一致。"""
    got = set(series.get("chains") or {})
    return sorted(got - cls), sorted(cls - got)


def coverage_gaps(series: dict) -> list[tuple[str, int, int]]:
    """各鏈的缺格數：(鏈名, amt 缺天數, ret_ew 缺天數)，只回有缺者。"""
    n = len(series.get("dates") or [])
    out = []
    for c, t in (series.get("chains") or {}).items():
        a = sum(1 for v in t.get("amt") or [] if v is None) + max(0, n - len(t.get("amt") or []))
        r = sum(1 for v in t.get("ret_ew") or [] if v is None) + max(0, n - len(t.get("ret_ew") or []))
        if a or r:
            out.append((c, a, r))
    return sorted(out, key=lambda x: (-x[1], -x[2]))


# ------------------------------------------------------------------ 交叉驗證

def cross_validate(series: dict, daysums: list[dict]) -> dict:
    """回補結果 vs daysummary 的 chain_top5／chain_bot3 逐（日, 鏈）比對。

    參考值刻意用 `amt_yi ÷ index.tse.amt_yi` 還原 share，而不是直接讀 share_pct——
    後者只有 1 位小數，對 2.8% 這種小鏈光四捨五入就佔 ±1.8% 相對誤差，
    會把資料源差異和顯示層取整混在一起。
    回 {"n":樣本數, "amt_rel":[...], "share_rel":[...], "share_pp":[...],
        "nstk_match":int, "nstk_n":int, "per_chain":{鏈:[amt_rel...]},
        "days":[...], "mkt":[(date, 我方總額, daysummary 總額)]}"""
    dates = series.get("dates") or []
    idx = {d: i for i, d in enumerate(dates)}
    chs = series.get("chains") or {}
    mkt_amt = (series.get("market") or {}).get("amt") or []

    res = {"n": 0, "amt_rel": [], "share_rel": [], "share_pp": [],
           "nstk_match": 0, "nstk_n": 0, "per_chain": {}, "per_chain_share": {},
           "days": [], "mkt": [], "skipped": []}
    for ds in daysums:
        d = ds.get("date")
        i = idx.get(d)
        if i is None:
            res["skipped"].append(d)
            continue
        ref_tot = (((ds.get("index") or {}).get("tse") or {}).get("amt_yi")) or 0
        mine_tot = mkt_amt[i] if i < len(mkt_amt) else None
        if ref_tot and mine_tot:
            res["mkt"].append((d, mine_tot, ref_tot))
        res["days"].append(d)
        for row in (ds.get("chain_top5") or []) + (ds.get("chain_bot3") or []):
            name = row.get("n")
            t = chs.get(name)
            if not t:
                continue
            mine = (t.get("amt") or [None] * (i + 1))[i]
            ref = row.get("amt_yi")
            if mine is None or not ref:
                continue
            res["n"] += 1
            rel = mine / ref - 1
            res["amt_rel"].append(rel)
            res["per_chain"].setdefault(name, []).append(rel)
            if ref_tot and mine_tot:
                ref_share = ref / ref_tot
                my_share = mine / mine_tot
                res["share_rel"].append(my_share / ref_share - 1)
                res["share_pp"].append((my_share - ref_share) * 100)
                res["per_chain_share"].setdefault(name, []).append(my_share / ref_share - 1)
            if row.get("n_stk") is not None:
                res["nstk_n"] += 1
                my_n = (t.get("n") or [None] * (i + 1))[i]
                if my_n == row["n_stk"]:
                    res["nstk_match"] += 1
    return res


# ------------------------------------------------------------------ 基本統計

def daily_chain_counts(series: dict) -> list[int]:
    dates = series.get("dates") or []
    chs = series.get("chains") or {}
    out = []
    for i in range(len(dates)):
        out.append(sum(1 for t in chs.values()
                       if i < len(t.get("amt") or []) and (t["amt"][i] or 0) > 0))
    return out


def mean_shares(series: dict) -> list[tuple[str, float]]:
    """各鏈全期平均佔比（降序）。"""
    out = []
    for c, t in (series.get("chains") or {}).items():
        v = [x for x in (t.get("share") or []) if x is not None]
        if v:
            out.append((c, stats.fmean(v)))
    return sorted(out, key=lambda x: -x[1])


def ret_stats(series: dict) -> dict:
    """報酬分布：等權／市值加權各自的分位數、極值、兩者相關與差距。"""
    ew, mw, both = [], [], []
    for t in (series.get("chains") or {}).values():
        a = t.get("ret_ew") or []
        b = t.get("ret_mw") or []
        for i in range(min(len(a), len(b))):
            if a[i] is not None:
                ew.append(a[i])
            if b[i] is not None:
                mw.append(b[i])
            if a[i] is not None and b[i] is not None:
                both.append((a[i], b[i]))
    return {
        "ew": ew, "mw": mw,
        "corr": corr([x for x, _ in both], [y for _, y in both]),
        "gap_abs": [abs(x - y) for x, y in both],
    }


def share_concentration(series: dict) -> dict:
    """佔比集中度。

    `daily_dup` 是「重複計數」口徑（TopN 鏈 share 加總 ÷ 全部鏈 share 加總），
    分子分母都含多對多重複，分母 >100%；本檔仍算它是為了與舊版報告對得起來，
    但**它不能拿去跟盤中版比**。可比的 `split`／`union` 由 build_chain_daily 逐日
    落在 series.coverage（算法見該檔 `def topn_coverage`），這裡只取出來。"""
    ms = mean_shares(series)
    tot = sum(v for _, v in ms)
    top = sum(v for _, v in ms[:TOPN])
    dates = series.get("dates") or []
    chs = series.get("chains") or {}
    daily = []
    for i in range(len(dates)):
        vals = sorted((t["share"][i] for t in chs.values()
                       if i < len(t.get("share") or []) and t["share"][i] is not None),
                      reverse=True)
        if vals:
            s = sum(vals)
            daily.append(sum(vals[:TOPN]) / s if s else None)
    cov = series.get("coverage") or {}
    return {"mean_top_share": top / tot if tot else float("nan"),
            "daily": [x for x in daily if x is not None], "ranked": ms,
            "cov": {k: [x for x in (cov.get(k) or []) if x is not None]
                    for k in ("dup", "split", "union")}}


def drift_decomposition(cv: dict) -> dict:
    """把交叉驗證的佔比相對差拆成「鏈間固定成分」與「鏈內漂移」。

    RRG 的 X 軸是 share ÷ 該鏈自身基準，**逐鏈固定的乘性偏差會約掉**，只有隨時間
    漂移的部分會傷到座標。這裡用單因子變異數拆解量兩者的相對大小：
    總變異 = 組間（各鏈平均差彼此不同）+ 組內（同一鏈隨日子漂移）。"""
    sr = cv.get("share_rel") or []
    pc = cv.get("per_chain_share") or {}
    if len(sr) < 3 or not pc:
        return {}
    gm = stats.fmean(sr)
    n = len(sr)
    btw = math.sqrt(sum(len(v) * (stats.fmean(v) - gm) ** 2 for v in pc.values()) / n)
    wit = math.sqrt(sum(sum((x - stats.fmean(v)) ** 2 for x in v) for v in pc.values()) / n)
    var = btw ** 2 + wit ** 2
    sds = [stats.pstdev(v) for v in pc.values() if len(v) >= 3]
    return {"total": stats.pstdev(sr), "between": btw, "within": wit,
            "between_var_share": (btw ** 2 / var) if var else float("nan"),
            "n": n, "chains": len(pc), "chains_ge3": len(sds),
            "max_n": max((len(v) for v in pc.values()), default=0),
            "drift_sd_median": stats.median(sds) if sds else None,
            "drift_sd_max": max(sds) if sds else None}


def rs_noise(series: dict, drift_sd: float, ks=RS_KS) -> list[dict]:
    """把「逐鏈漂移標準差」換算成 RS-Ratio 的點數噪音，並對照實測 RS 分布。

    RS_Ratio = 100 × share(t) ÷ mean(share(t−K..t−1))（同 backtest/run_rrg.py 檔頭）。
    分子帶 1 份漂移、分母是 K 天的平均帶 1/K 份，兩者獨立 →
      σ_RS ≈ 100 × drift_sd × √(1 + 1/K)（點）。
    **這不是選軸**，只是把 §交叉驗證量到的誤差放到第二階段看得懂的尺度上。
    `band` ＝ |RS−100| < σ_RS 的觀測比例，也就是「象限判定純由噪音決定」的下限比例。"""
    dates = series.get("dates") or []
    chs = series.get("chains") or {}
    out = []
    for k in ks:
        rs = []
        for t in chs.values():
            s = t.get("share") or []
            for i in range(k, min(len(s), len(dates))):
                w = [s[j] for j in range(i - k, i) if s[j] is not None]
                if len(w) < k or s[i] is None:
                    continue
                b = sum(w) / k
                if b > 0:
                    rs.append(100 * s[i] / b)
        if len(rs) < 100:
            continue
        sig = 100 * drift_sd * math.sqrt(1 + 1 / k)
        q1, q3 = pctl(rs, 25), pctl(rs, 75)
        out.append({"k": k, "n": len(rs), "sigma": sig, "p25": q1, "p75": q3,
                    "iqr": q3 - q1, "band": sum(1 for x in rs if abs(x - 100) < sig) / len(rs)})
    return out


# ------------------------------------------------------------------ 報告

def render(series: dict, cv: dict, cls: set[str]) -> str:
    dates = series.get("dates") or []
    meta = series.get("meta") or {}
    extra, missing_ch = chain_set_diff(series, cls)
    gaps_wd = missing_weekdays(dates, meta.get("nontrading") or [])
    counts = daily_chain_counts(series)
    conc = share_concentration(series)
    rs = ret_stats(series)
    cg = coverage_gaps(series)
    mk = series.get("market") or {}
    qual = series.get("quality") or {}
    sp0 = meta.get("spread_zero_up") or {}
    dec = drift_decomposition(cv)
    # 輸出檔體積：用與 build_chain_daily 相同的序列化方式現算，不依賴檔案存不存在
    series_kb = round(len(json.dumps(series, ensure_ascii=False,
                                     separators=(",", ":")).encode()) / 1024)

    # 指數壞列的前後對照。「修正前」＝把壞日的報酬按舊版邏輯記成 0（其餘不變），
    # 兩邊都只取 taiex_ret 非 null 的日子，才不會被序列第一天的有無影響加總。
    tx_bad = meta.get("taiex_spread_zero_days") or []
    txr = mk.get("taiex_ret") or []
    txc = mk.get("taiex") or []
    ok_i = [i for i, v in enumerate(txr) if v is not None]
    good = [txr[i] for i in ok_i]
    badset = set(tx_bad)
    bad = [0.0 if dates[i] in badset else txr[i] for i in ok_i]
    mwv = [(mk.get("ret_mw") or [None] * len(dates))[i] for i in ok_i]
    good_sum, bad_sum = sum(good), sum(bad)
    mw_sum = sum(x for x in mwv if x is not None)
    good_corr, bad_corr = corr(mwv, good), corr(mwv, bad)
    good_compound = math.prod(1 + x for x in good)
    bad_compound = math.prod(1 + x for x in bad)
    tx_first = txc[ok_i[0] - 1] if ok_i and ok_i[0] else (txc[0] if txc else float("nan"))
    tx_last = txc[ok_i[-1]] if ok_i else float("nan")
    tx_bad_moves = [txr[i] for i in ok_i if dates[i] in badset] or [0.0]

    L = []
    A = L.append
    A("# 盤後日頻 RRG — 第一階段（歷史回補）資料報告")
    A("")
    A(f"- 產生時間：{series.get('generated_at', '—')}")
    A(f"- 資料源：{series.get('source', '—')}")
    A(f"- 產生腳本：`src/build_chain_daily.py` → `data/chain_daily/series.json`")
    A(f"- 本報告腳本：`backtest/run_rrg_daily.py`（離線、免 token）")
    A("- 規格：`docs/rrg-daily-spec-20260811.md` §2。**本階段不做軸定義比較**（§3 第二階段）。")
    A("")

    # ---------------------------------------------------------- 資料品質
    A("## 資料品質")
    A("")
    A("### 回補範圍")
    A("")
    A("| 項目 | 值 |")
    A("|---|---|")
    A(f"| 交易日數 | **{len(dates)}**（規格門檻 ≥{MIN_DAYS} → "
      f"{'通過' if len(dates) >= MIN_DAYS else '**未達**'}）|")
    A(f"| 起訖 | {dates[0] if dates else '—'} ~ {dates[-1] if dates else '—'} |")
    A(f"| 產業鏈條數 | {len(series.get('chains') or {})} |")
    A(f"| 鏈集合 vs classify（twse） | 序列多出 {len(extra)} 條、classify 多出 {len(missing_ch)} 條"
      f"{'（雙向差集為空 ✅）' if not extra and not missing_ch else ''} |")
    if extra:
        A(f"| ⚠ 序列有而 classify 無 | {'、'.join(extra)} |")
    if missing_ch:
        A(f"| ⚠ classify 有而序列無 | {'、'.join(missing_ch)} |")
    A(f"| 已知非交易日（區間內國定假日等） | {len(meta.get('nontrading') or [])} 天 |")
    A(f"| **未解釋的缺口**（平日但無資料且非已知休市） | {len(gaps_wd)} 天"
      f"{'：' + '、'.join(gaps_wd[:12]) + ('…' if len(gaps_wd) > 12 else '') if gaps_wd else ' ✅'} |")
    # 2 位小數：series.json 存的是 6 位有效位數（src/build_chain_daily.py 的
    # `def mw_amt_coverage_of`），印 3 位小數會讓第 3 位變成四捨五入產物（假精度）。
    A(f"| 市值加權覆蓋率（有 `sh` 的成員佔鏈成員成交額） | "
      f"{fmt((meta.get('mw_amt_coverage') or 0) * 100, 2, '%')}"
      f"（全期 {len(qual.get('mw_cov') or [])} 天平均）|")
    A(f"| 指數報酬口徑 | 收盤比值（`meta.taiex_ret_method` = "
      f"`{meta.get('taiex_ret_method', '—')}`）|")
    A(f"| ⚠ FinMind 指數列壞資料 | **{len(tx_bad)} 天**"
      f"{'（' + tx_bad[0] + ' ~ ' + tx_bad[-1] + '）' if tx_bad else ''}，已繞開，見下段 |")
    A(f"| 個股 `spread` 缺陷（spread=0 但收盤上漲） | "
      f"{sp0.get('stock_days', 0)} / {sp0.get('checked', 0)} stock-day"
      f"（{fmt(sp0['stock_days'] / sp0['checked'] * 100, 3, '%') if sp0.get('checked') else '—'}）"
      f"；僅記錄不修正 |")
    A("")

    # ------------------------------------------------ 指數列壞資料（2026-08-11 驗收抓到）
    A("### ⚠ FinMind 指數列的整段壞資料（已修正）")
    A("")
    if not tx_bad:
        A("本次序列未偵測到 `spread = 0` 但收盤有動的指數列。")
    else:
        A(f"**{len(tx_bad)} 個交易日**（{'、'.join(tx_bad)}）的 FinMind `TAIEX` 列是 "
          "`Trading_money = 0`、`spread = 0`，但指數當日確實有動"
          f"（最大單日 {fmt(max(abs(x) for x in tx_bad_moves) * 100, 2, '%')}）。")
        A("")
        A("舊版把 `spread` 照單全收，這 12 天的 `market.taiex_ret` 全被記成 0，"
          "**而且完全靜默**——沒有 null、沒有旗標，下游看不出來。修正後：")
        A("")
        A("| 量 | 修正前（spread 口徑） | 修正後（收盤比值） |")
        A("|---|---:|---:|")
        A(f"| 全期日報酬算術加總 | {fmt(bad_sum * 100, 1, '%')} | {fmt(good_sum * 100, 1, '%')} |")
        A(f"| 與市值加權報酬的差距 | {fmt((mw_sum - bad_sum) * 100, 1, 'pp')} | "
          f"{fmt((mw_sum - good_sum) * 100, 1, 'pp')} |")
        A(f"| 與市值加權報酬的相關 | {fmt(bad_corr, 4)} | {fmt(good_corr, 4)} |")
        A("")
        A("**舊版報告把 13.2pp 的差距歸因給「股利還原＋`sh` 用當前值＋TAIEX 權重規則」，"
          f"那是錯的**：其中 {fmt((good_sum - bad_sum) * 100, 1, 'pp')} 純粹是這 12 天的資料缺陷，"
          f"真正待解釋的只剩 {fmt((mw_sum - good_sum) * 100, 1, 'pp')}。")
        A("")
        A("**修法選擇：指數改用收盤比值，不是加守門。** 理由：")
        A("")
        A("1. TAIEX 是**價格指數、本來就不還原股利**，收盤比值就是它的定義。"
          "「用 `spread` 避開除權息假跌」這個理由只對個股成立，對指數不成立。")
        A("2. 可自我對帳："
          f"{tx_first:,.2f} → {tx_last:,.2f} ＝ {(tx_last / tx_first - 1) * 100:+.2f}%，"
          f"收盤比值逐日連乘剛好等於它（{(good_compound - 1) * 100:+.2f}%，誤差 <1e-5）；"
          f"`spread` 版連乘只有 {(bad_compound - 1) * 100:+.2f}%，缺口正是這 12 天。"
          "守門只能把壞日標成缺值，指數序列會多 12 個洞，還是對不起帳。")
        A("3. 代價是指數報酬不再單日自足。實作把它放在 "
          "`src/build_chain_daily.py` 的 `def columnar`、由已落檔的收盤序列現算，"
          "所以舊檔重跑就自動修正、不必 `--rebuild`；且只在 `def prev_trading_day` "
          "確認兩天相鄰時才算，跨缺口一律給 null（本次 "
          f"{len(meta.get('taiex_ret_gap_days') or [])} 天，序列第一天另計）。")
        A("")
        A("> **對第二階段是直接風險，請務必先看這段。** 規格 §3 的價格版軸（方案 B）"
          "`RS-Ratio = 鏈報酬指數 ÷ 大盤指數`，分母就是這條指數序列。"
          "若沿用壞資料，這 12 天的分母會是「大盤不動」，鏈的相對強弱會被整段推高，"
          "而且**不會有任何缺值訊號**——是靜默給出錯誤座標。"
          "價格版上線前務必先確認 `meta.taiex_spread_zero_days` 為空、"
          "或這些日子已被排除。")
    A("")

    A("### 交叉驗證（vs `data/daysummary/*.json` 的 `chain_top5`／`chain_bot3`）")
    A("")
    if not cv["n"]:
        A("**無重疊樣本**——回補區間與 daysummary 沒有交集，無法交叉驗證。")
    else:
        ar = cv["amt_rel"]
        sr = cv["share_rel"]
        sp = cv["share_pp"]
        A(f"重疊交易日 {len(cv['days'])} 天、逐（日, 鏈）樣本 **{cv['n']}** 組"
          f"（daysummary 每天只落 top5+bot3，故每天最多 8 組）。")
        A("")
        A("全表四欄**同一口徑：帶號值**（中位／平均／P90 皆為帶號分布上的統計量，"
          "「最大」欄取絕對值最大者但保留原符號）。"
          "舊版此表的「鏈佔比絕對差」列單獨把 P90 算成 `abs()` 分布的 P90，"
          "同一列混了兩種口徑、不可比"
          + (f"（該列帶號 P90 為 {fmt(pctl(sp, 90), 3, 'pp')}，"
             f"絕對值 P90 為 {fmt(pctl([abs(x) for x in sp], 90), 3, 'pp')}）。" if sp else "。"))
        A("")
        A("| 指標 | 中位 | 平均 | P90（帶號） | 最大（絕對值最大者，保留符號） |")
        A("|---|---|---|---|---|")
        A(f"| 鏈成交額相對差 (我方÷daysummary−1) | {fmt(stats.median(ar) * 100, 2, '%')} | "
          f"{fmt(stats.fmean(ar) * 100, 2, '%')} | {fmt(pctl(ar, 90) * 100, 2, '%')} | "
          f"{fmt(max(ar, key=abs) * 100, 2, '%')} |")
        if sr:
            A(f"| **鏈佔比相對差**（分母各用自家市場總額） | {fmt(stats.median(sr) * 100, 2, '%')} | "
              f"{fmt(stats.fmean(sr) * 100, 2, '%')} | {fmt(pctl(sr, 90) * 100, 2, '%')} | "
              f"{fmt(max(sr, key=abs) * 100, 2, '%')} |")
            # P90 用帶號分布（不是 abs 分布），與本表其餘各列同口徑——見表頭上方的說明
            A(f"| 鏈佔比絕對差（百分點，帶號） | {fmt(stats.median(sp), 3, 'pp')} | "
              f"{fmt(stats.fmean(sp), 3, 'pp')} | {fmt(pctl(sp, 90), 3, 'pp')} | "
              f"{fmt(max(sp, key=abs), 3, 'pp')} |")
        if cv["mkt"]:
            mr = [m / r - 1 for _, m, r in cv["mkt"]]
            A(f"| 市場總額相對差 | {fmt(stats.median(mr) * 100, 2, '%')} | "
              f"{fmt(stats.fmean(mr) * 100, 2, '%')} | {fmt(pctl(mr, 90) * 100, 2, '%')} | "
              f"{fmt(max(mr, key=abs) * 100, 2, '%')} |")
        A(f"| 成員檔數 `n_stk` 完全相符 | {cv['nstk_match']}/{cv['nstk_n']} "
          f"（{fmt(cv['nstk_match'] / cv['nstk_n'] * 100 if cv['nstk_n'] else 0, 1, '%')}）| | |")
        A("")
        A("逐鏈的差異（樣本數 ≥3 者）——**佔比欄才是 RRG 真正要用的量**，"
          "成交額欄的水準差在佔比中被分母吸收：")
        A("")
        A("| 產業鏈 | 樣本 | 成交額相對差(中位) | 成交額(最大) | 佔比相對差(中位) | 佔比(最大) | 佔比差**標準差** |")
        A("|---|---:|---:|---:|---:|---:|---:|")
        sds = []
        for c, v in sorted(cv["per_chain"].items(), key=lambda x: -abs(stats.median(x[1]))):
            if len(v) < 3:
                continue
            s = cv["per_chain_share"].get(c) or []
            sd = stats.pstdev(s) if len(s) >= 3 else None
            if sd is not None:
                sds.append(sd)
            A(f"| {c} | {len(v)} | {fmt(stats.median(v) * 100, 2, '%')} | "
              f"{fmt(max(v, key=abs) * 100, 2, '%')} | "
              f"{fmt(stats.median(s) * 100, 2, '%') if s else '—'} | "
              f"{fmt(max(s, key=abs) * 100, 2, '%') if s else '—'} | "
              f"{fmt(sd * 100, 2, '%') if sd is not None else '—'} |")
        A("")
        A("**最後一欄是本次交叉驗證最關鍵的數字。** RRG 的 X 軸是 "
          "`share_i(t) ÷ 該鏈自身 K 日基準`——分子分母**同源**（都來自本回補資料），"
          "所以「某條鏈的佔比相對 daysummary 系統性偏高／偏低 x%」這種**逐鏈固定的乘性偏差會完全約掉**，"
          "不影響座標。真正會傷到座標的是偏差**隨時間漂移**的部分，也就是這一欄。"
          + (f"實測逐鏈標準差中位 **{fmt(stats.median(sds) * 100, 2, '%')}**、"
             f"最大 {fmt(max(sds) * 100, 2, '%')}。" if sds else ""))
        A("")
        if dec:
            A("**這個 1.07% 是「最有利視窗下的下限估計」，不是全期誤差。** 樣本限制四項，"
              "引用時必須一併帶上：")
            A("")
            A(f"1. 只涵蓋 47 條鏈中的 **{dec['chains_ge3']} 條**（樣本 ≥3 者），"
              f"單鏈最大樣本數僅 {dec['max_n']}——以 {dec['max_n']} 個點估標準差，"
              "本身的抽樣誤差就有數成。")
            A("2. 樣本只來自**該鏈當天進 daysummary top5/bot3 的日子**，"
              "是**選擇偏誤**：偏向該鏈成交額特別高或特別低的極端日，"
              "不是隨機抽樣的一般日。")
            A(f"3. 只涵蓋 {len(dates)} 天中的 **{len(cv['days'])} 天**"
              f"，其餘 {len(dates) - len(cv['days'])} 天**零驗證**"
              "（daysummary 只有這麼多天）。")
            A("4. daysummary 本身也不是真值，只是另一個口徑的觀測；"
              "兩者的差不等於本檔的誤差。")
            A("")
            A("**變異數拆解**（同一批 %d 個樣本、%d 條有樣本的鏈）：" % (dec["n"], dec["chains"]))
            A("")
            A(f"- 佔比相對差的總標準差 **{fmt(dec['total'] * 100, 2, '%')}**"
              f" ＝ 鏈間固定成分 {fmt(dec['between'] * 100, 2, '%')}"
              f" ⊕ 鏈內漂移 {fmt(dec['within'] * 100, 2, '%')}（平方和）")
            A(f"- 固定成分佔總變異數 **{fmt(dec['between_var_share'] * 100, 0, '%')}**"
              "——這一份會被 RRG 的自身基準約掉，剩下的才是真正的座標噪音。")
            A("")
            A("所以舊版寫的「**逐鏈近乎固定**的乘性偏差」偏樂觀，正確說法是"
              "**「以固定成分為主」**：三成多的變異確實是會傷到座標的鏈內漂移。")
        A("")
        A("**差異成因（依可解釋度排序）**")
        A("")
        A("1. **資料源口徑不同（主因，方向固定為正）**。回補用 FinMind 日線 "
          "`TaiwanStockPrice.Trading_money`，涵蓋整股盤中＋**盤中零股**＋盤後定價交易；"
          "daysummary 用 Worker `/live` 的 tick snapshot `total_amount`，只含整股盤中。"
          "高價股的盤中零股佔比最高，故差異隨股價／熱門度放大——2026-08-11 實測 "
          "2330 台積電 snapshot 378.9 億 vs 日線 436.7 億（+13.2%），"
          "全市場 8489.6 億 vs 8807.9 億（+3.75%）。這是**水準差**，"
          "在佔比（各自除以自家市場總額）中被分母吸收掉大半。"
          "殘留的部分是**成分效應**：零股集中在高價／熱門股，故高價股為主的鏈佔比相對偏高、"
          "低價傳產鏈偏低——這個偏差**以逐鏈固定的乘性成分為主**"
          "（變異數的七成，見上方變異數拆解），但**不是**「近乎固定」："
          "剩下三成是會傷到座標的鏈內漂移。")
        A("2. **`classify.json` 是當前版本、不是當時版本**。分類月更；區間內的新上市、"
          "下市、改分類都被今天的分類回頭套用。愈往前推的日期，這項誤差愈大，"
          "但 daysummary 只覆蓋最近 18 天，這段幾乎不受影響"
          f"（classify 產生於 {series.get('classify_at', '—')}）。")
        A("3. **顯示層取整**。daysummary 的 `amt_yi` 與 `index.tse.amt_yi` 都只有 1 位小數；"
          "小鏈（2~3%）的相對誤差因此天生有 ±0.02% 量級的下限雜訊，"
          "但相對本表的差異量級可忽略。")
        A("4. **歸戶規則本身不是差異來源**（已排除）。daysummary 的 `chain_top5` 走 "
          "`src/build_daysummary.py` 的 `agg_multi(level=0)`（用 `p[0]`），本檔用 `c`；"
          "2026-08-11 實測 classify 內全部 1537 檔 twse 個股的 `c` 與 `{p[0]}` 集合**完全相同**，"
          "故兩者等價。成員檔數 `n_stk` 的相符率即為此提供直接證據。")
    A("")

    A("### 已知限制")
    A("")
    for i, s in enumerate(meta.get("limits") or [], 1):
        A(f"{i}. {s}")
    n0 = len(meta.get("limits") or [])
    A(f"{n0 + 1}. **個股報酬用交易所口徑的 `spread ÷ 前收`**（指數不是，見上），"
      "不是 `close ÷ 前日 close − 1`。"
      "後者在除權息日會把配息當暴跌（2026-08-11 實測合庫金 −8.05%、第一金 −6.44%、"
      "群益證 −11.24%，三檔 `spread` 皆為 0），足以讓「金融」鏈當日報酬被假跌汙染數個百分點。"
      "副作用是單日計算完全自足（不需前一交易日資料），增量更新可逐日獨立。")
    A(f"{n0 + 2}. **本階段沒有回答軸定義該用哪個**。資金版 vs 價格版的比較、"
      "移動平均期數、持續性條件 N，全部留到第二階段用這批資料實測"
      "（規格 §3 的四項判定準則）。")
    A(f"{n0 + 3}. **`n_stk` 的比對只覆蓋 daysummary 落檔的 top5+bot3**，"
      "不是全部 47 條鏈；其餘鏈的成員數沒有獨立的第三方參照可比。")
    A(f"{n0 + 4}. **交叉驗證量到的 {fmt((dec.get('drift_sd_median') or 0) * 100, 2, '%')} "
      "殘餘漂移是「最有利視窗下的下限估計」**"
      f"（只涵蓋 {dec.get('chains_ge3', 0)}/47 條鏈、單鏈最大樣本 {dec.get('max_n', 0)}、"
      f"樣本來自 top5/bot3 故有選擇偏誤、只涵蓋 {len(dates)} 天中的 {len(cv['days'])} 天）。"
      "詳見交叉驗證段的四項樣本限制，不可當成全期誤差上界。")
    A(f"{n0 + 5}. **`--rebuild` 在 classify 換版後單獨用並不安全**。"
      "cache 的 v1 檔（2026-08-11 之前落的）是用**當時的 classify** 過濾後才存的，"
      "新上市個股／新增鏈從 v1 cache 重算會靜默漏掉。v2 起 cache 與 classify 脫鉤"
      "（留全部數字開頭代號）並記下 `classify_at`，不符時 `--rebuild` 會明確警告；"
      "換版請跑 `--rebuild --refetch-stale`（會重打 API）。"
      "存量 302 天的 v1 cache 未回填，下次 `--refetch-stale` 才會轉成 v2。"
      "**且 `--refetch-stale` 必須併用 `--rebuild`**：`def build` 對已在 `series.json` 的"
      "日期直接沿用（`if ds in days: continue`），根本走不到讀 cache 那一步，"
      "單獨下這個旗標對存量日期完全無效。")
    A(f"{n0 + 6}. **`--refetch-stale` 的實際重抓路徑尚未實跑驗證**（2026-08-11 複驗補記）。"
      "本環境無 FinMind token，只做了程式碼審閱："
      "`refetch_stale and not offline` → `prices = None` → `def fetch_day` → "
      "`def cache_write` 以 v2 落檔，邏輯正確；但**沒有任何一次真的打過 API 的實跑證據**，"
      "「302 份 v1 cache 會被轉成 v2」目前是推論、不是已驗證事實。"
      "下次有 token 時務必先實跑確認，並注意那一跑是 **302 次連續 API 呼叫**"
      "（`src/build_chain_daily.py` 的 `SLEEP` = 0.5 秒，約 2.5 分鐘），"
      "跑之前先確認不會撞到 FinMind 的請求額度；額度吃緊時用 `--start`／`--end` 分批跑。")
    A(f"{n0 + 7}. **輸出檔約 {series_kb} KB 且每次更新整檔重寫，會直接進 git**。"
      "取捨理由（columnar 讓 packfile 吃得到 delta：實測連續兩版 219 KiB，"
      "分日檔 286 個 blob 是 382 KiB）原本只寫在 `src/build_chain_daily.py` 檔頭，"
      "沒進報告。**這是已接受的成本，不是沒看到**：以每日增量約 2 KB、"
      "delta 壓縮後更小估計，一年約增 0.5 MB；"
      "超過約兩年份時應改依年份分段，屆時前端／回測要一併改讀多檔。")
    A("")

    # ---------------------------------------------------------- 基本統計
    A("## 基本統計")
    A("")
    A("### 每日鏈數（成交額 >0）")
    A("")
    if counts:
        A(f"最小 {min(counts)}、中位 {int(stats.median(counts))}、最大 {max(counts)}；"
          f"全期 {len(counts)} 天中有 {sum(1 for c in counts if c == max(counts))} 天達到最大值。")
        low = [(d, c) for d, c in zip(dates, counts) if c < max(counts)]
        if low:
            A("")
            A(f"未達最大值的日期共 {len(low)} 天，最少的 5 天："
              + "、".join(f"{d}({c})" for d, c in sorted(low, key=lambda x: x[1])[:5]))
    A("")

    A("### 成交額／佔比分布")
    A("")
    A(f"#### Top{TOPN} 覆蓋率：三種定義，不可互比")
    A("")
    A("舊版報告在這裡犯過錯，先講清楚定義再給數字。同一天的 Top10 覆蓋率，"
      "三種算法可以差 17pp：")
    A("")
    A("| 定義 | 分子 | 分母 | 全期平均 | 中位 | P5 | P95 |")
    A("|---|---|---|---:|---:|---:|---:|")
    cov = conc["cov"]
    labels = {
        "split": (f"**均分口徑**（與盤中版可比）", f"跨鏈成員金額均分後的 Top{TOPN}", "成員總額（≈100%）"),
        "union": ("成員聯集", f"Top{TOPN} 鏈的成員**聯集**成交額", "市場總額"),
        "dup": ("重複計數（舊版用的）", f"Top{TOPN} 鏈 share 加總", "全 47 鏈 share 加總（>100%）"),
    }
    for k in ("split", "union", "dup"):
        v = cov.get(k) or []
        if not v:
            continue
        lb = labels[k]
        A(f"| {lb[0]} | {lb[1]} | {lb[2]} | **{fmt(stats.fmean(v) * 100, 1, '%')}** | "
          f"{fmt(stats.median(v) * 100, 1, '%')} | {fmt(pctl(v, 5) * 100, 1, '%')} | "
          f"{fmt(pctl(v, 95) * 100, 1, '%')} |")
    A("")
    sp = cov.get("split") or []
    if sp:
        A(f"**與盤中版可比的是第一列。** `backtest/run_rrg_topn.py` 的 {INTRADAY_TOP10}% "
          "出自「產業鏈層（intraday g + classify p）」，那支是把跨鏈次產業金額**均分**"
          "後算 Top10 ÷ 成員總額（見其 `def subs_to_chains`），分母 ≈100%。"
          f"本批 {len(sp)} 天用同口徑重算是 **{fmt(stats.fmean(sp) * 100, 1, '%')}**，"
          f"比盤中版**高 {fmt((stats.fmean(sp) * 100 - INTRADAY_TOP10), 1, 'pp')}**。")
        A("")
        A("**但「同口徑」只到「均分後 Top10 ÷ 成員總額、分母 ≈100%」這一層，"
          "兩者不是同一個運算**（2026-08-11 複驗補記，引用時請一併帶上）：")
        A("")
        A("1. **均分發生的層級不同**。本檔在**個股層**均分：一檔跨 m 條鏈就把它的成交額"
          "除以 m 分給各鏈（`src/build_chain_daily.py` 的 `def aggregate_day` 內 "
          "`per = amt / len(cs)`）。盤中版在**次產業層**均分：intraday 的 `g` 只到次產業，"
          "一個次產業跨 m 條鏈就把整個次產業的金額除以 m（`backtest/run_rrg_topn.py` 的 "
          "`def subs_to_chains`；該支附註「classify 次產業 484 個，其中 19 個橫跨多條產業鏈」）。"
          "同一檔股票在兩邊被切分的份數不一定相同。")
        A("2. **資料源不同**。本檔是 FinMind 日線（含盤中零股與盤後定價），"
          "盤中版是 tick snapshot（僅整股盤中）；金額水準差約 +3.75%（見交叉驗證段）。")
        A(f"3. **樣本期不同、數字會動**。規格 §5 引用的 {INTRADAY_TOP10}% 是 13 日樣本；"
          "2026-08-11 以現有 15 日（2026-07-20~2026-08-11）重跑 `backtest/run_rrg_topn.py` "
          "得 **70.6%**。本報告仍沿用規格值以便對照，差 0.1pp 不影響任何結論。")
        A("")
        A(f"兩邊分母都 ≈100%，所以「日頻覆蓋率**高於**盤中版、且 Top10 落在 RRG 可讀的 "
          "10±2 條」這個方向結論穩健；但**不要把這 "
          f"{fmt((stats.fmean(sp) * 100 - INTRADAY_TOP10), 1, 'pp')} 當成「同一個算法在兩種資料上的差」**"
          "——它同時混了均分層級與資料源兩個變因，無法歸因給其中任一個。")
        A("")
        A("舊版報告寫的 63.6%／65.2% 是「重複計數」口徑（分子分母都含多對多重複，"
          "分母是 47 條鏈的 share 加總、遠大於 100%），拿去對盤中版的 "
          f"{INTRADAY_TOP10}% 是**兩種定義互比**，得出「低於盤中版」的結論方向剛好相反。"
          "規格 §5「Top10 覆蓋 70.7%、落在 RRG 可讀上限 10±2」的判斷不但沒被推翻，"
          "日頻資料下還更站得住腳。")
        A("")
        A("> 三種定義的算法在 `src/build_chain_daily.py` 的 `def topn_coverage`，"
          "逐日值落在 `series.json` 的 `coverage` 區塊。日後要比覆蓋率，先確認口徑。")
    A("")
    A(f"全期平均佔比排名（前 {TOPN + 5} 條；佔比分母為市場總額，多對多歸屬故加總 >100%）：")
    A("")
    A("| # | 產業鏈 | 平均佔比 | 最小 | 最大 |")
    A("|---:|---|---:|---:|---:|")
    chs = series.get("chains") or {}
    for i, (c, v) in enumerate(conc["ranked"][:TOPN + 5], 1):
        col = [x for x in (chs[c].get("share") or []) if x is not None]
        A(f"| {i} | {c} | {fmt(v * 100, 2, '%')} | {fmt(min(col) * 100, 2, '%')} | "
          f"{fmt(max(col) * 100, 2, '%')} |")
    A("")
    tail = conc["ranked"][-5:]
    A("尾端 5 條（第二階段的「小分母汙染」風險就集中在這裡）："
      + "、".join(f"{c} {v*100:.2f}%" for c, v in tail))
    A("")

    A("### 報酬分布")
    A("")
    A("| 序列 | 樣本 | P1 | P25 | 中位 | P75 | P99 | 最大絕對值 |")
    A("|---|---:|---:|---:|---:|---:|---:|---:|")
    for k, lbl in (("ew", "鏈等權報酬"), ("mw", "鏈市值加權報酬")):
        v = rs[k]
        if not v:
            continue
        A(f"| {lbl} | {len(v)} | {fmt(pctl(v, 1) * 100, 2, '%')} | {fmt(pctl(v, 25) * 100, 2, '%')} | "
          f"{fmt(stats.median(v) * 100, 2, '%')} | {fmt(pctl(v, 75) * 100, 2, '%')} | "
          f"{fmt(pctl(v, 99) * 100, 2, '%')} | {fmt(abs(max(v, key=abs)) * 100, 2, '%')} |")
    for k, lbl in (("ret_ew", "市場等權報酬"), ("ret_mw", "市場市值加權報酬"),
                   ("taiex_ret", "加權指數報酬")):
        v = [x for x in (mk.get(k) or []) if x is not None]
        if not v:
            continue
        A(f"| {lbl} | {len(v)} | {fmt(pctl(v, 1) * 100, 2, '%')} | {fmt(pctl(v, 25) * 100, 2, '%')} | "
          f"{fmt(stats.median(v) * 100, 2, '%')} | {fmt(pctl(v, 75) * 100, 2, '%')} | "
          f"{fmt(pctl(v, 99) * 100, 2, '%')} | {fmt(abs(max(v, key=abs)) * 100, 2, '%')} |")
    A("")
    A(f"- 等權 vs 市值加權的相關：**{fmt(rs['corr'], 4)}**"
      f"（R² ≈ {fmt(rs['corr'] ** 2, 2)}）；"
      f"兩者絕對差中位 {fmt(stats.median(rs['gap_abs']) * 100, 2, '%')}、"
      f"P95 {fmt(pctl(rs['gap_abs'], 95) * 100, 2, '%')}"
      if rs["gap_abs"] else "- （無足夠樣本比較兩種加權）")
    if rs["ew"] and rs["mw"]:
        A(f"- 市值加權的離散度**顯著更大**：P1/P99 為 "
          f"{fmt(pctl(rs['mw'], 1) * 100, 2, '%')}／{fmt(pctl(rs['mw'], 99) * 100, 2, '%')}，"
          f"等權只有 {fmt(pctl(rs['ew'], 1) * 100, 2, '%')}／"
          f"{fmt(pctl(rs['ew'], 99) * 100, 2, '%')}。方向可解釋："
          "市值加權會**放大單一個股的支配度**（規格 §3 準則 3 記載 2308 台達電掛在 "
          "47 條鏈中的 21 條），等權則會稀釋它。")
        A("")
        A("  → **給第二階段的具體建議：不要只「明講用哪一個」，要做「兩種軸 × 兩種加權」"
          "四組實測**。R² 只有 "
          f"{fmt(rs['corr'] ** 2, 2)} 表示兩者有四成變異不共用，加權方式不是"
          "價格版軸內部的細節，而是與軸定義同一層級的自變數；只測兩組會把"
          "「軸的差異」和「加權的差異」混在一起，得不出可歸因的結論。")
    if good and mwv:
        A(f"- 市場市值加權報酬 vs 加權指數報酬的相關：**{fmt(good_corr, 4)}**"
          f"（{len(good)} 個共同日）。全期日報酬**算術加總**（非複利，僅供量級對照）："
          f"市值加權 {fmt(mw_sum * 100, 1, '%')} vs 指數 {fmt(good_sum * 100, 1, '%')}，"
          f"差距 {fmt((mw_sum - good_sum) * 100, 1, 'pp')}。")
        A(f"  **這裡的舊數字（13.2pp）有 {fmt((good_sum - bad_sum) * 100, 1, 'pp')} 是指數列的"
          "資料缺陷，不是歸因**（見上方「FinMind 指數列的整段壞資料」段）。"
          f"扣掉之後剩下的 {fmt((mw_sum - good_sum) * 100, 1, 'pp')} 才輪得到"
          "「本檔已還原除權息、TAIEX 未還原」「`sh` 用當前發行張數」"
          "「TAIEX 的實際權重規則與漲跌幅限制」這幾項來解釋，"
          "且它們仍混在一起，**不可直接當成「股利貢獻」解讀**。")
    A("")

    # ------------------------------------------------ 噪音尺度換算
    noise = rs_noise(series, dec["drift_sd_median"]) if dec.get("drift_sd_median") else []
    if noise:
        A("### 誤差換算成 RS 座標的噪音尺度")
        A("")
        A("交叉驗證量到的漂移是「佔比的百分比」，第二階段看的是「RS 點數」，"
          "得先換算才知道嚴不嚴重。`RS_Ratio = 100 × share(t) ÷ 前 K 日 share 平均`"
          "（同 `backtest/run_rrg.py` 檔頭），分子帶 1 份漂移、分母的 K 日平均帶 1/K 份，"
          f"兩者獨立 → **σ_RS ≈ 100 × {fmt(dec['drift_sd_median'] * 100, 3, '%')} × √(1 + 1/K)**"
          "（表格用未四捨五入的漂移值；若逕用報告顯示的 1.07% 會得到 1.17／1.12／1.11）。")
        A("")
        A("| K | σ_RS（點） | 實測 RS-Ratio IQR | σ_RS ÷ IQR | \\|RS−100\\| < σ_RS 的觀測比例 |")
        A("|---:|---:|---:|---:|---:|")
        for r in noise:
            A(f"| {r['k']} | **{fmt(r['sigma'], 2)}** | "
              f"{fmt(r['p25'], 1)} ~ {fmt(r['p75'], 1)}（寬 {fmt(r['iqr'], 1)}）| "
              f"{fmt(r['sigma'] / r['iqr'] * 100, 1, '%')} | {fmt(r['band'] * 100, 2, '%')} |")
        A("")
        b = [r["band"] for r in noise]
        A(f"讀法：噪音約是 RS 分布 IQR 的 3%，量級上不致命；但 "
          f"**{fmt(min(b) * 100, 1, '%')}~{fmt(max(b) * 100, 1, '%')} 的（日, 鏈）觀測"
          f"落在 RS=100 這條象限界線的噪音帶內**——大約每 30 個象限判定就有 1 個"
          "接近擲銅板。這是規格 §3 判定準則 1（跨日穩定度）的先驗下限："
          "任何方案的「象限翻轉率」至少會有這麼多來自資料誤差，"
          "不能全記在指標頭上。")
        A("")
        A("（此處只是把 §交叉驗證的誤差放到可讀的尺度，**不是在選軸**；"
          "上表用的是資金版軸的 RS 定義，價格版另有自己的噪音結構，第二階段各測各的。）")
        A("")

    A("### 缺格")
    A("")
    if not cg:
        A("全部鏈在全部交易日都有成交額與報酬，無缺格。")
        n_txn = sum(1 for v in txr if v is None)
        if n_txn:
            A("")
            A(f"市場層唯一的缺值是 `market.taiex_ret` 的 {n_txn} 天"
              f"（序列第一天沒有前收；跨缺口 "
              f"{len(meta.get('taiex_ret_gap_days') or [])} 天）——這是**刻意的**，"
              "見上方指數壞資料段。")
    else:
        A("| 產業鏈 | 成交額缺天數 | 等權報酬缺天數 |")
        A("|---|---:|---:|")
        for c, a, r in cg[:20]:
            A(f"| {c} | {a} | {r} |")
        if len(cg) > 20:
            A(f"| …另 {len(cg) - 20} 條 | | |")
    A("")

    A("## 下一步（第二階段）")
    A("")
    A("依規格 §3，用本批資料實測比較兩個軸定義（A 資金版／B 價格版），判定準則四項："
      "跨日穩定度、小分母汙染、單一個股支配度、輪動訊號延續性；"
      "並掃出移動平均期數與「連續 N 日符合才進榜」的 N。**本階段不預先綁死任何一個。**")
    A("")
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="盤後日頻 RRG 回補資料的品質與基本統計")
    ap.add_argument("--series", default=str(SERIES))
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()
    sp = Path(a.series)
    if not sp.exists():
        print(f"::error::找不到 {sp}，請先跑 python3 src/build_chain_daily.py", file=sys.stderr)
        return 1
    series = load_series(sp)
    cv = cross_validate(series, load_daysummaries())
    txt = render(series, cv, classify_chains())
    op = Path(a.out)
    op.parent.mkdir(parents=True, exist_ok=True)
    op.write_text(txt, encoding="utf-8")

    dates = series.get("dates") or []
    print(f"交易日 {len(dates)} 天（{dates[0]} ~ {dates[-1]}）、鏈 {len(series.get('chains') or {})} 條")
    if cv["n"]:
        sr = (f"{stats.median(cv['share_rel'])*100:+.2f}%" if cv["share_rel"] else "—")
        print(f"交叉驗證 {len(cv['days'])} 天 / {cv['n']} 組：成交額相對差中位 "
              f"{stats.median(cv['amt_rel'])*100:+.2f}%、佔比相對差中位 {sr}、n_stk 相符 "
              f"{cv['nstk_match']}/{cv['nstk_n']}")
    else:
        print("交叉驗證：無重疊樣本")
    try:
        shown = op.relative_to(ROOT)
    except ValueError:
        shown = op.resolve()
    print(f"→ {shown}（{op.stat().st_size/1024:.0f} KB）")
    return 0


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    raise SystemExit(main())
