#!/usr/bin/env python3
# backtest/test_rrg_daily_smoke.py — 盤後日頻 RRG 回補管線的離線煙霧/回歸測試
#
# 為什麼需要這支：src/build_chain_daily.py 要打 FinMind 才跑得動、跑一輪回補要數十分鐘；
# 這支改用合成樣本直接呼叫核心計算函式，驗「口徑對不對、降級分支走不走得通、
# 報告必要段落在不在」——**不驗真實資料的數字**（那要跑 build_chain_daily / run_rrg_daily 本人）。
#
# 守的具體坑（2026-08-11 建檔時逐一實測確認）：
#   1. **報酬不能用 close ÷ 前日 close − 1**。除權息日交易所以除息參考價為基準，
#      2026-08-11 實測合庫金 close 比值 −8.05%、第一金 −6.44%、群益證 −11.24%，
#      三檔 `spread` 皆為 0（當日平盤）。用 close 比值會讓「金融」鏈當日報酬被假跌汙染。
#      見 test_ret_of_uses_spread_not_close_ratio。
#   2. **佔比分母必須是市場總額，不能是鏈加總**。classify 的鏈是多對多，
#      一檔會同時計入多條鏈，鏈 share 加總必然 >1。見 test_share_denominator_is_market_total。
#   3. **classify 裡混著 FinMind 的指數偽代號**（TAIEX／Semiconductor／FinancialInsurance…，
#      t 也是 twse），它們在 TaiwanStockPrice 會帶著整個市場的成交金額出現，
#      不濾掉市場總額會被灌爆。見 test_keep_codes_drops_index_pseudocodes。
#   4. **ETF 計入市場總額分母但不計報酬**（與 worker/src/index.js `aggregate` 的
#      `const etf = code.startsWith("00")` 一致）。見 test_etf_counts_in_denominator_only。
#   5. **同一檔在同一條鏈只能算一次**（classify.c 可能重複列出）。
#      見 test_chain_dedup。
#   6. **指數報酬不能用 spread**（2026-08-11 驗收抓到）。FinMind 的 TAIEX 列有整段
#      `Trading_money=0, spread=0` 但收盤確實有動的壞資料（實測 12 天），照收會讓
#      taiex_ret 靜默變 0。指數是價格指數、不還原股利，收盤比值才是定義。
#      見 test_taiex_ret_uses_close_ratio、test_taiex_ret_none_across_gap。
#   7. **要對全期平均的量必須逐日落檔**（2026-08-11 驗收抓到）。舊版 mw_amt_coverage
#      只平均「本次新算的日子」，隔日增量會被單日值蓋掉（0.9992 → 1.0）。
#      見 test_incremental_does_not_clobber_coverage。
#   8. **覆蓋率三種定義不可互比**。見 test_topn_coverage_three_definitions。
#   9. **cache 不可綁死在當時的 classify**。見 test_cache_roundtrip_and_staleness。
#  10. **報告頭條數字要有回歸保護**（2026-08-11 複驗補）。原本 drift_decomposition
#      與 rs_noise 零測試——把鏈內漂移 wit 歸零、或拿掉 σ_RS 的 √(1+1/K) 因子，
#      全部測試仍綠。見 test_drift_decomposition_math、test_rs_noise_sigma_and_band。
#  11. **「要對全期平均的量」的守門必須打在邏輯上，不是打在產物上**（同上批複驗）。
#      原本只有「對已落檔的 series.json 斷言」，把 build() 的彙總改回舊 bug
#      （只取最後一天）測試仍綠，要等產物重建才會紅。彙總邏輯已抽成
#      src/build_chain_daily.py 的 `def mw_amt_coverage_of`，
#      見 test_mw_amt_coverage_is_full_period_mean。
#
# ── 這支測試的「強度」要誠實看 ─────────────────────────────────────────
# 本檔目前 165 項斷言（2026-08-11 實跑計數），**其中 25 項是「文件／報告字串存在性」檢查**
# （test_committed_report_sections 的 21 項＋test_render_runs_on_synthetic 的 4 項，
# 例如「資料品質段量化了差異」＝ `"%" in q`）。這類斷言近乎恆真、守門力弱，
# 只擋得住「整段被刪掉」，擋不住「數字算錯」。
# **引用斷言總數當成測試強度的證據時，請先扣掉這 25 項。**
# 真正守數值邏輯的是 build_chain_daily 那批純函式測試，以及上面第 10、11 條補的三支。
# ────────────────────────────────────────────────────────────────────
#
# 用法：python3 backtest/test_rrg_daily_smoke.py（免 token、免網路、免快取）

from __future__ import annotations

import json
import math
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_chain_daily as bc   # noqa: E402
import run_rrg_daily as rd       # noqa: E402

FAILED = []


def check(label, cond):
    ok = bool(cond)
    print(f"{'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        FAILED.append(label)


def close(a, b, tol=1e-9):
    if a is None or b is None:
        return False
    return abs(a - b) <= tol


# ---------------------------------------------------------------- 合成樣本

def sample_classify() -> dict:
    """5 檔股票 ＋ 1 個指數偽代號，覆蓋所有口徑分支。"""
    return {
        "1001": {"n": "多鏈股", "t": "twse", "c": ["X", "Y"], "p": [["X", "a"], ["Y", "b"]], "sh": 1000},
        "1002": {"n": "單鏈股", "t": "twse", "c": ["X"], "p": [["X", "a"]], "sh": 2000},
        "2002": {"n": "上櫃股", "t": "tpex", "c": ["X"], "p": [["X", "a"]], "sh": 5000},
        "0050": {"n": "ETF", "t": "twse", "c": [], "p": [], "sh": 9999},
        "9001": {"n": "無股本股", "t": "twse", "c": ["Y", "Y"], "p": [["Y", "b"]], "sh": 0},
        "TAIEX": {"n": "加權指數", "t": "twse", "c": [], "p": [], "sh": 0},
        "Semiconductor": {"n": "半導體類指數", "t": "twse", "c": [], "p": [], "sh": 0},
    }


def sample_prices() -> dict:
    """{code: [amt(元), close, spread]}。amt 單位是元，1 億 = 1e8。"""
    return {
        "1001": [100e8, 110.0, 10.0],    # 前收 100 → ret +10%
        "1002": [50e8, 99.0, -1.0],      # 前收 100 → ret −1%
        "0050": [20e8, 50.0, 0.0],       # ETF：只進分母
        "9001": [30e8, 20.0, 0.0],       # 無 sh：進等權不進市值加權
        "2002": [999e8, 10.0, 0.0],      # tpex：整檔不計
        "_TAIEX": [900e10, 20000.0, 100.0],
    }


# ---------------------------------------------------------------- build_chain_daily

def test_ret_of_uses_spread_not_close_ratio():
    # 除息日：close 34.95→32.70（−6.44%）但 spread=0（平盤）
    check("除息日 spread=0 → 報酬 0（不是 −6.44%）", close(bc.ret_of(32.70, 0.0), 0.0))
    check("一般日 spread 正確換算", close(bc.ret_of(110.0, 10.0), 0.1))
    check("負 spread", close(bc.ret_of(99.0, -1.0), -0.01))


def test_ret_of_guards():
    check("close 為 None → None", bc.ret_of(None, 1.0) is None)
    check("spread 為 None → None", bc.ret_of(10.0, None) is None)
    check("前收 ≤0（close==spread）→ None", bc.ret_of(5.0, 5.0) is None)
    check("前收為負 → None", bc.ret_of(5.0, 6.0) is None)
    check("prev_close_of 與 ret_of 同守門", bc.prev_close_of(5.0, 5.0) is None
          and close(bc.prev_close_of(110.0, 10.0), 100.0))


def test_keep_codes_drops_index_pseudocodes():
    keep = bc.keep_codes(sample_classify())
    check("keep 收上市＋上櫃個股", keep == {"1001", "1002", "2002", "0050", "9001"})
    check("keep 排除 TAIEX 偽代號", "TAIEX" not in keep)
    check("keep 排除 Semiconductor 類指數偽代號", "Semiconductor" not in keep)


def test_rows_to_prices():
    rows = [
        {"stock_id": "TAIEX", "Trading_money": 9e12, "close": 20000.0, "spread": 100.0},
        {"stock_id": "Semiconductor", "Trading_money": 5e12, "close": 900.0, "spread": 1.0},
        {"stock_id": "1001", "Trading_money": 1e10, "close": 110.0, "spread": 10.0},
        {"stock_id": "030123", "Trading_money": 1e6, "close": 1.0, "spread": 0.0},  # 權證
        {"stock_id": "8888", "Trading_money": 2e8, "close": 10.0, "spread": 0.0},   # 未進 classify
    ]
    p = bc.rows_to_prices(rows, bc.keep_codes(sample_classify()))
    check("TAIEX 另存為 _TAIEX", p.get(bc.TAIEX) == [9e12, 20000.0, 100.0])
    check("keep 內個股留下", p.get("1001") == [1e10, 110.0, 10.0])
    check("權證等非 keep 代號被丟掉", "030123" not in p)
    # v2：cache 不再吃 classify 過濾器，否則 classify 換版後 --rebuild 會靜默漏掉新上市
    p2 = bc.rows_to_prices(rows)
    check("keep=None 時仍擋掉非數字開頭的類指數偽代號", "Semiconductor" not in p2)
    check("keep=None 時保留還沒進 classify 的新代號", p2.get("8888") == [2e8, 10.0, 0.0])
    check("keep=None 時仍收 _TAIEX", bc.TAIEX in p2)


def test_market_row():
    a = bc.aggregate_day(sample_prices(), sample_classify())["market"]
    check("市場總額含 ETF、不含 tpex（200 億）", close(a["amt"] / 1e8, 200.0, 1e-6))
    check("市場檔數 = 4（tpex 不計）", a["n"] == 4)
    # 等權：1001 +10%、1002 −1%、9001 0%（ETF 不計）
    check("市場等權報酬 = 3%", close(a["ret_ew"], 0.03))
    # 市值加權：w(1001)=1000×100、w(1002)=2000×100；9001 無 sh、0050 是 ETF
    check("市場市值加權報酬 = 8000/300000", close(a["ret_mw"], 8000 / 300000))
    check("TAIEX 收盤落下", close(a["taiex"], 20000.0))
    check("aggregate_day 不再自己算 taiex_ret（改由 columnar 用收盤比值算）",
          "taiex_ret" not in a)
    check("指數 spread=0 的旗標（本樣本 spread=100 → 0）", a["_tx_spread0"] == 0)
    check("市值加權成交額覆蓋率 = 150/180", close(a["_mw_cov"], 150 / 180))


def test_chain_dedup():
    ch = bc.aggregate_day(sample_prices(), sample_classify())["chains"]
    # 9001 的 c 是 ["Y","Y"]，重複列出只能算一次
    check("Y 鏈成員數 = 2（1001 + 9001，不因 c 重複而變 3）", ch["Y"]["n"] == 2)
    check("Y 鏈成交額 = 130 億（9001 只加一次）", close(ch["Y"]["amt"] / 1e8, 130.0, 1e-6))


def test_twse_only():
    ch = bc.aggregate_day(sample_prices(), sample_classify())["chains"]
    check("X 鏈不含 tpex 的 2002（150 億而非 1149 億）",
          close(ch["X"]["amt"] / 1e8, 150.0, 1e-6) and ch["X"]["n"] == 2)


def test_etf_counts_in_denominator_only():
    ch = bc.aggregate_day(sample_prices(), sample_classify())["chains"]
    check("ETF 不出現在任何鏈", all("0050" not in str(v) for v in ch.values()))
    check("鏈只有 X / Y 兩條", set(ch) == {"X", "Y"})


def test_share_denominator_is_market_total():
    ch = bc.aggregate_day(sample_prices(), sample_classify())["chains"]
    check("X share = 150/200", close(ch["X"]["share"], 0.75))
    check("Y share = 130/200", close(ch["Y"]["share"], 0.65))
    check("多對多 → 鏈 share 加總 >1（證明分母不是鏈加總）",
          ch["X"]["share"] + ch["Y"]["share"] > 1.0)


def test_chain_returns():
    ch = bc.aggregate_day(sample_prices(), sample_classify())["chains"]
    check("X 等權 = (10% + −1%)/2 = 4.5%", close(ch["X"]["ret_ew"], 0.045))
    check("X 市值加權 = 8000/300000", close(ch["X"]["ret_mw"], 8000 / 300000))
    check("Y 等權 = (10% + 0%)/2 = 5%", close(ch["Y"]["ret_ew"], 0.05))
    check("Y 市值加權 = 10%（9001 無 sh 被排除）", close(ch["Y"]["ret_mw"], 0.1))


def test_empty_day_is_degraded_not_crash():
    a = bc.aggregate_day({}, sample_classify())
    check("空日不炸、市場總額 0", a["market"]["amt"] == 0 and a["chains"] == {})
    check("空日報酬為 None", a["market"]["ret_ew"] is None and a["market"]["ret_mw"] is None)
    only_etf = bc.aggregate_day({"0050": [1e8, 50.0, 0.0]}, sample_classify())
    check("只有 ETF 的日子：有總額但無報酬、無鏈",
          only_etf["market"]["amt"] == 1e8 and only_etf["market"]["ret_ew"] is None
          and only_etf["chains"] == {})


def test_wmean_and_mean_guards():
    check("wmean 權重總和 0 → None", bc.wmean([(1.0, 0.0)]) is None)
    check("wmean 正常", close(bc.wmean([(1.0, 1.0), (3.0, 3.0)]), 2.5))
    check("mean 空 → None", bc.mean([]) is None)
    check("sig(None) → None", bc.sig(None) is None)
    check("sig 保留有效位數", close(bc.sig(0.4383371234, 6), 0.438337, 1e-12))


def test_weekdays():
    d = bc.weekdays(__import__("datetime").date(2026, 8, 3), __import__("datetime").date(2026, 8, 9))
    check("weekdays 只給週一~五", d == ["2026-08-03", "2026-08-04", "2026-08-05",
                                        "2026-08-06", "2026-08-07"])


def test_columnar_fills_missing_chain_day():
    days = {
        "2026-08-10": bc.aggregate_day(sample_prices(), sample_classify()),
        "2026-08-11": bc.aggregate_day({"1002": [50e8, 99.0, -1.0]}, sample_classify()),
    }
    dates, market, chains, _cov, _qual = bc.columnar(days, ["X", "Y", "Z"])
    check("dates 由舊到新排序", dates == ["2026-08-10", "2026-08-11"])
    check("第二天沒有 Y 鏈 → 補 None", chains["Y"]["amt"][1] is None)
    check("classify 有但全期無資料的 Z 鏈欄位全 None", chains["Z"]["amt"] == [None, None])
    check("market 陣列與 dates 等長", all(len(v) == 2 for v in market.values()))
    check("chains 每欄與 dates 等長",
          all(len(v) == 2 for t in chains.values() for v in t.values()))


def two_day_days() -> dict:
    """兩天的合成聚合；第二天的指數 spread=0 但收盤有動（＝ FinMind 那 12 天的形狀）。"""
    d1 = bc.aggregate_day(sample_prices(), sample_classify())
    p2 = dict(sample_prices())
    p2["_TAIEX"] = [0, 20400.0, 0.0]           # Trading_money=0、spread=0，但收盤 +2%
    d2 = bc.aggregate_day(p2, sample_classify())
    return {"2026-08-10": d1, "2026-08-11": d2}


def test_taiex_ret_uses_close_ratio():
    """守的坑 6：spread=0 的壞列不能讓 taiex_ret 變成 0。"""
    _d, market, _c, _cov, qual = bc.columnar(two_day_days(), ["X", "Y"])
    check("序列第一天沒有前收 → taiex_ret 為 None", market["taiex_ret"][0] is None)
    check("壞列日的報酬由收盤比值算出 +2%（不是 spread 給的 0）",
          close(market["taiex_ret"][1], 0.02, 1e-9))
    check("壞列被標記在 quality.tx_spread0", qual["tx_spread0"] == [0, 1])


def test_taiex_ret_none_across_gap():
    """相鄰兩筆若不是相鄰交易日（序列有洞），寧可給 None 也不給橫跨缺口的假報酬。"""
    days = two_day_days()
    days["2026-08-13"] = days.pop("2026-08-11")      # 中間的 08-11、08-12 憑空消失
    _d, market, _c, _cov, _q = bc.columnar(days, ["X", "Y"])
    check("跨未解釋缺口 → taiex_ret 為 None", market["taiex_ret"][1] is None)
    _d2, m2, _c2, _cov2, _q2 = bc.columnar(days, ["X", "Y"],
                                           nontrading=["2026-08-11", "2026-08-12"])
    check("缺口若是已知休市 → 照常給報酬", close(m2["taiex_ret"][1], 0.02, 1e-9))
    check("prev_trading_day 跳過週末",
          bc.prev_trading_day("2026-08-10", []) == "2026-08-07")
    check("prev_trading_day 跳過已知非交易日",
          bc.prev_trading_day("2026-08-10", ["2026-08-07"]) == "2026-08-06")


def test_topn_coverage_three_definitions():
    """守的坑 8：三種覆蓋率定義的分母不同，數字不可互比。"""
    cov = bc.aggregate_day(sample_prices(), sample_classify())["cov"]
    # 合成樣本只有 X、Y 兩條鏈，TOPN=10 → 三種定義都會收下全部
    check("均分口徑分母 ≈ 成員總額 → 全收時為 1", close(cov["split"], 1.0, 1e-9))
    check("重複計數口徑分母是鏈加總 → 全收時也是 1", close(cov["dup"], 1.0, 1e-9))
    # 成員聯集 = 1001+1002+9001 = 180 億；市場總額含 ETF = 200 億
    check("成員聯集口徑分母是市場總額（含 ETF）→ 180/200", close(cov["union"], 0.9, 1e-9))


def test_spread_zero_up_detection():
    """守的坑：spread=0 同時是「平盤」與「沒資料」，只在收盤上漲時才判定為缺陷。"""
    prev = {"1001": [1e8, 100.0, 0.0], "1002": [1e8, 100.0, 0.0], "9001": [1e8, 100.0, 0.0]}
    now = {
        "1001": [1e8, 105.0, 0.0],    # 漲了卻 spread=0 → 缺陷
        "1002": [1e8, 92.0, 0.0],     # 跌了且 spread=0 → 多半是除權息，不算
        "9001": [1e8, 100.0, 0.0],    # 真平盤
        "0050": [1e8, 120.0, 0.0],    # ETF 不算
    }
    hit, chk = bc.spread_zero_up(now, prev, sample_classify())
    check("只抓上漲方向的 spread=0", hit == 1)
    check("檢查數排除 ETF 與缺前收者", chk == 3)
    check("沒有前一日資料時回 (0, 0)", bc.spread_zero_up(now, {}, sample_classify()) == (0, 0))


def test_cache_roundtrip_and_staleness():
    """守的坑 9：v1 cache 是被當時的 classify 過濾過的，換版重算會靜默漏檔。"""
    with tempfile.TemporaryDirectory() as td:
        old = bc.CACHE
        bc.CACHE = Path(td)
        try:
            bc.cache_write("2026-08-10", {"1001": [1, 2, 3]}, "2026-08-01T00:00:00+08:00")
            p, meta = bc.cache_read("2026-08-10")
            check("v2 cache 讀得回價格", p == {"1001": [1, 2, 3]})
            check("v2 cache 記下 classify_at", meta["classify_at"] == "2026-08-01T00:00:00+08:00")
            check("classify 沒換版 → 不算過期",
                  bc.cache_stale(meta, "2026-08-01T00:00:00+08:00") is False)
            check("classify 換版 → 判定過期",
                  bc.cache_stale(meta, "2026-09-01T00:00:00+08:00") is True)
            # v1（沒有版本欄的裸 dict）也要讀得回來，且一律判定過期
            bc.cache_path("2026-08-11").write_bytes(
                __import__("gzip").compress(json.dumps({"1002": [4, 5, 6]}).encode()))
            p1, m1 = bc.cache_read("2026-08-11")
            check("v1 cache 仍讀得回來", p1 == {"1002": [4, 5, 6]})
            check("v1 cache 一律判定過期", bc.cache_stale(m1, "任何值") is True)
            check("沒有 cache 時回 (None, {})", bc.cache_read("1999-01-01") == (None, {}))
        finally:
            bc.CACHE = old


def test_load_existing_roundtrip():
    days = {"2026-08-10": bc.aggregate_day(sample_prices(), sample_classify())}
    days["2026-08-10"]["qual"] = {"sp0_up": 2, "sp0_n": 100}
    dates, market, chains, cov, qual = bc.columnar(days, ["X", "Y"])
    obj = {"dates": dates, "market": market, "chains": chains,
           "coverage": cov, "quality": qual,
           "meta": {"nontrading": ["2026-08-12"]}}
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "series.json"
        p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        back, nt, _meta = bc.load_existing(p)
    check("round-trip 日期還原", list(back) == ["2026-08-10"])
    check("round-trip 非交易日還原", nt == {"2026-08-12"})
    check("round-trip 金額還原成元（150 億）",
          close(back["2026-08-10"]["chains"]["X"]["amt"] / 1e8, 150.0, 1e-3))
    check("round-trip 佔比不重算、原樣沿用",
          close(back["2026-08-10"]["chains"]["X"]["share"], 0.75, 1e-6))
    again = bc.columnar(back, ["X", "Y"])
    check("round-trip 後再 columnar 一次是冪等的", again[1]["amt"] == market["amt"])
    # 逐日診斷欄位若沒還原，增量更新就會把全期彙總抹成「當天那一格」（驗收抓到的坑 7）
    check("round-trip 還原逐日覆蓋率", again[3] == cov)
    check("round-trip 還原逐日品質欄位", again[4] == qual)


def test_incremental_does_not_clobber_coverage():
    """守的坑 7：只重算 1 天的增量，不能讓全期平均變成那 1 天的值。"""
    days = two_day_days()
    dates, market, chains, cov, qual = bc.columnar(days, ["X", "Y"])
    full = bc.mean([v for v in qual["mw_cov"] if v is not None])
    obj = {"dates": dates, "market": market, "chains": chains,
           "coverage": cov, "quality": qual, "meta": {"nontrading": []}}
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "series.json"
        p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        back, _nt, _m = bc.load_existing(p)
    # 模擬隔日增量：舊的兩天沿用（不重算），只有第三天是新算的
    back["2026-08-12"] = bc.aggregate_day({"1002": [50e8, 99.0, -1.0]}, sample_classify())
    _d, _mk, _ch, _cv, q2 = bc.columnar(back, ["X", "Y"])
    got = bc.mean([v for v in q2["mw_cov"] if v is not None])
    check("沿用日的 mw_cov 有被還原（不是 None）",
          q2["mw_cov"][0] is not None and q2["mw_cov"][1] is not None)
    check("增量後的全期平均仍含舊日子（不等於單日值）",
          got is not None and abs(got - q2["mw_cov"][-1]) > 1e-6
          and abs(got - full) < abs(q2["mw_cov"][-1] - full))


def test_mw_amt_coverage_is_full_period_mean():
    """守的坑 7 的**邏輯層**斷言（2026-08-11 複驗補）。

    上面那支 test_incremental_does_not_clobber_coverage 驗的是 columnar/load_existing
    的還原，`test_series_file_if_present` 驗的是已落檔的產物——兩者都抓不到
    「build() 裡的彙總改回只取最後一天」這個突變（產物沒重建就不會紅）。
    彙總邏輯已抽成 bc.mw_amt_coverage_of，這裡直接對它下斷言：
    輸入是刻意讓「全期平均」與「最後一天」差很遠的序列。"""
    qual = {"mw_cov": [0.90, 0.92, None, 1.00]}     # 平均 0.94、最後一天 1.00
    got = bc.mw_amt_coverage_of(qual)
    check("mw_amt_coverage = 有值日的全期平均（0.94）", close(got, 0.94, 1e-9))
    check("mw_amt_coverage ≠ 最後一天（1.00）", not close(got, 1.00, 1e-6))
    check("mw_amt_coverage ≠ 第一天（0.90）", not close(got, 0.90, 1e-6))
    check("mw_amt_coverage 跳過 None（不是拿 4 個值去除）",
          not close(got, (0.90 + 0.92 + 1.00) / 4, 1e-9))
    # 假精度守門（複驗第 3 項）：實值 0.9991686 若只存 4 位有效位數會變 0.9992，
    # 下游印 3 位小數就會給出「99.920%」這個第 3 位是四捨五入產物的假精度。
    real = [0.9991686254] * 10
    got2 = bc.mw_amt_coverage_of({"mw_cov": real})
    check("落檔精度足以正確印到小數 2 位（0.999169，非 0.9992）",
          got2 is not None and f"{got2 * 100:.2f}" == "99.92" and abs(got2 - 0.9992) > 1e-7)
    check("空序列回 None 不炸", bc.mw_amt_coverage_of({"mw_cov": [None, None]}) is None
          and bc.mw_amt_coverage_of({}) is None)


# ---------------------------------------------------------------- run_rrg_daily

def mk_series() -> dict:
    return {
        "generated_at": "2026-08-11T20:00:00+08:00",
        "source": "test",
        "classify_at": "2026-08-01T10:36:04+08:00",
        "dates": ["2026-08-10", "2026-08-11"],
        "market": {"amt": [200.0, 100.0], "n": [4, 3],
                   "ret_ew": [0.03, -0.01], "ret_mw": [0.02, -0.02],
                   "taiex": [20000.0, 19900.0], "taiex_ret": [None, -0.005]},
        "coverage": {"topn": 10, "dup": [1.0, 1.0], "split": [1.0, 1.0],
                     "union": [0.9, 0.85]},
        "quality": {"mw_cov": [0.8333, 0.9], "sp0_up": [0, 1], "sp0_n": [100, 100],
                    "tx_spread0": [0, 0]},
        "chains": {
            "X": {"amt": [150.0, 60.0], "n": [2, 2], "share": [0.75, 0.6],
                  "ret_ew": [0.045, -0.01], "ret_mw": [0.0266, -0.02]},
            "Y": {"amt": [130.0, None], "n": [2, None], "share": [0.65, None],
                  "ret_ew": [0.05, None], "ret_mw": [0.1, None]},
        },
        "meta": {"nontrading": [], "mw_amt_coverage": 0.8333, "limits": ["測試用限制"]},
    }


def test_pctl_and_corr():
    check("pctl 端點", close(rd.pctl([1, 2, 3, 4], 0), 1) and close(rd.pctl([1, 2, 3, 4], 100), 4))
    check("pctl 內插 P50=2.5", close(rd.pctl([1, 2, 3, 4], 50), 2.5))
    check("pctl 空清單回 nan", math.isnan(rd.pctl([], 50)))
    check("corr 完全正相關 = 1", close(rd.corr([1, 2, 3], [2, 4, 6]), 1.0, 1e-9))
    check("corr 完全負相關 = −1", close(rd.corr([1, 2, 3], [6, 4, 2]), -1.0, 1e-9))
    check("corr 常數序列回 nan（不除以 0）", math.isnan(rd.corr([1, 1, 1], [1, 2, 3])))
    check("corr 樣本 <3 回 nan", math.isnan(rd.corr([1, 2], [1, 2])))


def test_missing_weekdays():
    # 2026-08-10(一) ~ 2026-08-14(五)；序列只有 10 與 14，12 標為已知休市
    got = rd.missing_weekdays(["2026-08-10", "2026-08-14"], ["2026-08-12"])
    check("缺口只列未解釋的平日（11、13）", got == ["2026-08-11", "2026-08-13"])
    check("已知休市不算缺口", "2026-08-12" not in got)
    check("空序列回空", rd.missing_weekdays([], []) == [])


def test_chain_set_diff():
    s = mk_series()
    check("雙向差集為空 → 一致", rd.chain_set_diff(s, {"X", "Y"}) == ([], []))
    check("classify 多一條會被抓到", rd.chain_set_diff(s, {"X", "Y", "Z"}) == ([], ["Z"]))
    check("序列多一條會被抓到", rd.chain_set_diff(s, {"X"}) == (["Y"], []))


def test_daily_counts_and_gaps():
    s = mk_series()
    check("每日鏈數（第二天 Y 缺格）", rd.daily_chain_counts(s) == [2, 1])
    check("缺格統計抓到 Y", rd.coverage_gaps(s) == [("Y", 1, 1)])


def test_share_concentration():
    s = mk_series()
    c = rd.share_concentration(s)
    check("平均佔比排序 X 在前", [x[0] for x in c["ranked"]] == ["X", "Y"])
    check("Top10 覆蓋率在只有 2 條鏈時為 1", close(c["mean_top_share"], 1.0, 1e-9))


def test_cross_validate_numbers():
    """交叉驗證要給得出數字，且分母各用自家市場總額（水準差在佔比中被吸收）。"""
    s = mk_series()
    ds = [{
        "date": "2026-08-10",
        "index": {"tse": {"amt_yi": 100.0}},          # 我方 200 億 → 市場相對差 +100%
        "chain_top5": [{"n": "X", "amt_yi": 75.0, "n_stk": 2}],
        "chain_bot3": [{"n": "Y", "amt_yi": 60.0, "n_stk": 3}],
    }]
    cv = rd.cross_validate(s, ds)
    check("樣本數 = 2", cv["n"] == 2)
    check("X 成交額相對差 = +100%", close(cv["per_chain"]["X"][0], 1.0, 1e-9))
    check("X 佔比相對差 = 0（水準差被分母吸收）", close(cv["share_rel"][0], 0.0, 1e-9))
    check("Y 佔比相對差 ≠ 0（真實歸戶差異不會被吸收）", abs(cv["share_rel"][1]) > 1e-6)
    check("n_stk 相符 1/2", cv["nstk_match"] == 1 and cv["nstk_n"] == 2)
    check("市場總額比對有落下", cv["mkt"] == [("2026-08-10", 200.0, 100.0)])
    check("序列沒有的 daysummary 日期進 skipped",
          rd.cross_validate(s, [{"date": "2020-01-01"}])["skipped"] == ["2020-01-01"])


def test_drift_decomposition_math():
    """報告頭條數字（總標準差 ＝ 鏈間 ⊕ 鏈內、固定成分佔變異數 x%）的回歸守門。

    2026-08-11 複驗抓到這支函式**完全沒有單元測試**：把鏈內漂移 `wit` 歸零，
    全部測試仍綠。這裡用可手算的合成樣本釘死每一項。
    樣本：A = [+10%, +20%, +30%]（鏈平均 +20%），B = [−25%, −20%, −15%]（鏈平均 −20%），
    總平均 0 → 鏈間 = 0.20（兩鏈平均各距 0 有 0.20）、
    鏈內 = √(((0.01+0+0.01)+(0.0025+0+0.0025))/6) = √(0.025/6) = 0.0645497。"""
    A3 = [0.10, 0.20, 0.30]
    B3 = [-0.25, -0.20, -0.15]
    dec = rd.drift_decomposition({"share_rel": A3 + B3,
                                  "per_chain_share": {"A": A3, "B": B3}})
    check("總標準差 = 0.2101587", close(dec["total"], 0.2101586702, 1e-9))
    check("鏈間固定成分 = 0.20", close(dec["between"], 0.20, 1e-9))
    check("鏈內漂移 = 0.0645497（非 0）", close(dec["within"], 0.0645497224, 1e-9))
    # 這條恆等式就是報告寫的「總標準差 ＝ 鏈間 ⊕ 鏈內（平方和）」
    check("恆等式 total² = between² + within²",
          close(dec["total"] ** 2, dec["between"] ** 2 + dec["within"] ** 2, 1e-12))
    check("固定成分佔總變異數 = 90.566%（不是 100%）",
          close(dec["between_var_share"], 0.9056603774, 1e-9))
    check("樣本數／鏈數／單鏈最大樣本",
          dec["n"] == 6 and dec["chains"] == 2 and dec["chains_ge3"] == 2
          and dec["max_n"] == 3)
    check("逐鏈漂移標準差取中位（0.0612372）＝ 兩鏈 0.0816497／0.0408248 的中位",
          close(dec["drift_sd_median"], 0.0612372436, 1e-9))
    check("逐鏈漂移標準差最大 = 0.0816497", close(dec["drift_sd_max"], 0.0816496581, 1e-9))

    # 語意錨點：逐鏈**完全固定**的乘性偏差 → 鏈內漂移必須是 0、固定成分佔 100%
    fx = {"share_rel": [0.10] * 3 + [-0.10] * 3,
          "per_chain_share": {"A": [0.10] * 3, "B": [-0.10] * 3}}
    f = rd.drift_decomposition(fx)
    check("逐鏈固定偏差 → 鏈內漂移 = 0", close(f["within"], 0.0, 1e-12))
    check("逐鏈固定偏差 → 固定成分佔變異數 = 100%",
          close(f["between_var_share"], 1.0, 1e-12))
    check("樣本不足（<3）回空 dict", rd.drift_decomposition({"share_rel": [0.1]}) == {})


def rs_noise_series() -> dict:
    """RS 噪音測試用序列：每條鏈的佔比在 1 與 2 之間交替。

    取 K=2 時 RS_Ratio = 100 × s[i] ÷ mean(s[i−2], s[i−1]) 只會是兩個值：
    s[i]=1 時 100×1÷1.5 = 66.667、s[i]=2 時 100×2÷1.5 = 133.333，全部距 100 恰 33.333 點。
    這讓「|RS−100| < σ_RS 的比例」可以被 σ_RS 的兩側精確夾住。
    另加一條在 index 5 有缺值的鏈，驗窗內含 None 時整格被跳過（不是當 0 算）。"""
    sh = [1.0, 2.0] * 7                       # 14 天
    chains = {f"c{i}": {"share": list(sh)} for i in range(20)}
    gap = list(sh)
    gap[5] = None                             # 使 i=5（本身缺）、i=6、i=7（窗內含缺）皆跳過
    chains["cGap"] = {"share": gap}
    return {"dates": [f"2026-01-{i + 1:02d}" for i in range(14)], "chains": chains}


def test_rs_noise_sigma_and_band():
    """σ_RS 換算與噪音帶比例的回歸守門（2026-08-11 複驗抓到：本函式原本零測試）。

    報告的 σ_RS 1.17／1.12／1.10 全靠這條公式：σ_RS = 100 × 漂移標準差 × √(1 + 1/K)。"""
    s = rs_noise_series()
    wide = rd.rs_noise(s, 0.40, ks=(2, 4, 13))    # σ = 48.99 > 33.33 → 全部落在噪音帶內
    narrow = rd.rs_noise(s, 0.20, ks=(2, 4, 13))  # σ = 24.49 < 33.33 → 全部落在帶外
    check("K 的樣本數不足 100 時整列略過（K=13 只剩 1 天）",
          [r["k"] for r in wide] == [2, 4] and [r["k"] for r in narrow] == [2, 4])
    w = wide[0]
    check("σ_RS = 100 × 0.40 × √(1+1/2) = 48.9898",
          close(w["sigma"], 100 * 0.40 * math.sqrt(1.5), 1e-12))
    check("σ_RS 確實含 √(1+1/K) 因子（不等於 100 × drift）",
          abs(w["sigma"] - 40.0) > 1.0)
    check("σ_RS = 100 × 0.20 × √(1+1/2) = 24.4949",
          close(narrow[0]["sigma"], 100 * 0.20 * math.sqrt(1.5), 1e-12))
    check("RS 觀測數 249（20 條完整鏈各 12 格 ＋ 缺值鏈只剩 9 格）", w["n"] == 249)
    check("缺值鏈的 3 格確實被跳過（不是補 0）",
          w["n"] == 21 * 12 - 3)
    check("實測 RS 分布 P25=66.667、P75=133.333、IQR=66.667",
          close(w["p25"], 200 / 3, 1e-9) and close(w["p75"], 400 / 3, 1e-9)
          and close(w["iqr"], 200 / 3, 1e-9))
    check("σ_RS(48.99) > 偏離量(33.33) → 噪音帶比例 = 100%", close(w["band"], 1.0, 1e-12))
    check("σ_RS(24.49) < 偏離量(33.33) → 噪音帶比例 = 0%",
          close(narrow[0]["band"], 0.0, 1e-12))
    # K 愈大分母的平均帶進愈少漂移 → σ_RS 遞減（報告 1.17 > 1.12 > 1.10 的來源）
    check("σ_RS 隨 K 增大而縮小：K=4 的 48.99 → 44.7214",
          close(wide[1]["sigma"], 100 * 0.40 * math.sqrt(1.25), 1e-12)
          and wide[1]["sigma"] < wide[0]["sigma"])
    check("K=4 的觀測數 205（20 條各 10 格 ＋ 缺值鏈 5 格）", wide[1]["n"] == 205)


def test_render_runs_on_synthetic():
    s = mk_series()
    txt = rd.render(s, rd.cross_validate(s, []), {"X", "Y"})
    check("合成資料也產得出報告", len(txt) > 500)
    check("無重疊樣本時據實說明", "無重疊樣本" in txt)
    for sec in ("## 資料品質", "## 基本統計"):
        check(f"合成報告含「{sec}」", sec in txt)


# ---------------------------------------------------------------- 已交付的報告

REQUIRED_SECTIONS = ["## 資料品質", "### 回補範圍", "### 交叉驗證", "### 已知限制",
                     "## 基本統計", "### 報酬分布",
                     # 2026-08-11 驗收要求，缺一不可（各對應一項已修正的錯誤）
                     "FinMind 指數列的整段壞資料", "三種定義，不可互比",
                     "誤差換算成 RS 座標的噪音尺度"]


def test_committed_report_sections():
    p = ROOT / "backtest" / "report_rrg_daily.md"
    check(f"{p.name} 已產出", p.exists())
    if not p.exists():
        return
    txt = p.read_text(encoding="utf-8")
    for sec in REQUIRED_SECTIONS:
        check(f"報告含「{sec}」段", sec in txt)
    for kw in ("classify.json", "spread", "盤中零股", "市值加權",
               "選擇偏誤", "--refetch-stale", "兩種軸 × 兩種加權"):
        check(f"報告寫出關鍵成因「{kw}」", kw in txt)
    q = txt.split("## 資料品質", 1)[-1].split("## 基本統計", 1)[0]
    check("資料品質段給出交易日數", "交易日數" in q)
    check("資料品質段量化了差異（有百分比數字）", "%" in q)
    lim = txt.split("### 已知限制", 1)[-1].split("## 基本統計", 1)[0]
    check("已知限制段寫出輸出檔進 repo 的體積成本", "KB" in lim and "git" in lim)
    check("已知限制段標明 1.07% 漂移是下限估計", "下限估計" in lim)


def test_series_file_if_present():
    """真實產物在場時做最低限度的健檢（不在場則跳過，維持離線可跑）。"""
    p = ROOT / "data" / "chain_daily" / "series.json"
    if not p.exists():
        print("SKIP  data/chain_daily/series.json 不存在（離線環境）")
        return
    s = rd.load_series(p)
    n = len(s.get("dates") or [])
    check(f"回補交易日 {n} ≥ {bc.MIN_DAYS}", n >= bc.MIN_DAYS)
    check("鏈集合與 classify 的 twse 鏈雙向差集為空",
          rd.chain_set_diff(s, rd.classify_chains()) == ([], []))
    check("所有欄位陣列與 dates 等長",
          all(len(v) == n for v in (s.get("market") or {}).values())
          and all(len(v) == n for t in (s.get("chains") or {}).values() for v in t.values())
          and all(len(v) == n for v in (s.get("quality") or {}).values())
          and all(len(v) == n for k, v in (s.get("coverage") or {}).items() if k != "topn"))
    check("沒有未解釋的平日缺口",
          rd.missing_weekdays(s["dates"], (s.get("meta") or {}).get("nontrading") or []) == [])
    meta = s.get("meta") or {}
    check("指數報酬用收盤比值", meta.get("taiex_ret_method") == "close_ratio")
    bad = set(meta.get("taiex_spread_zero_days") or [])
    tr = (s.get("market") or {}).get("taiex_ret") or []
    check(f"指數壞列日（{len(bad)} 天）的報酬不再是 0",
          all(tr[i] not in (0, 0.0) for i, d in enumerate(s["dates"]) if d in bad))
    mwc = [v for v in ((s.get("quality") or {}).get("mw_cov") or []) if v is not None]
    # 直接拿邏輯本體來比（不是在測試裡另寫一份算法），落檔值與函式必須逐位相同
    check("meta.mw_amt_coverage 等於逐日陣列的全期平均（不是單日值）",
          bool(mwc) and close(meta.get("mw_amt_coverage"),
                              bc.mw_amt_coverage_of(s.get("quality") or {}), 1e-12))
    stored = meta.get("mw_amt_coverage")
    check("meta.mw_amt_coverage 的精度足以正確印到小數 2 位（無假精度）",
          bool(mwc) and stored is not None
          and f"{stored * 100:.2f}" == f"{bc.mean(mwc) * 100:.2f}")


def main():
    for fn in [test_ret_of_uses_spread_not_close_ratio, test_ret_of_guards,
               test_keep_codes_drops_index_pseudocodes, test_rows_to_prices,
               test_market_row, test_chain_dedup, test_twse_only,
               test_etf_counts_in_denominator_only, test_share_denominator_is_market_total,
               test_chain_returns, test_empty_day_is_degraded_not_crash,
               test_wmean_and_mean_guards, test_weekdays,
               test_columnar_fills_missing_chain_day,
               test_taiex_ret_uses_close_ratio, test_taiex_ret_none_across_gap,
               test_topn_coverage_three_definitions, test_spread_zero_up_detection,
               test_cache_roundtrip_and_staleness,
               test_load_existing_roundtrip, test_incremental_does_not_clobber_coverage,
               test_mw_amt_coverage_is_full_period_mean,
               test_pctl_and_corr, test_missing_weekdays, test_chain_set_diff,
               test_daily_counts_and_gaps, test_share_concentration,
               test_cross_validate_numbers,
               test_drift_decomposition_math, test_rs_noise_sigma_and_band,
               test_render_runs_on_synthetic,
               test_committed_report_sections, test_series_file_if_present]:
        print(f"\n--- {fn.__name__} ---")
        fn()
    print(f"\n{'=' * 50}")
    if FAILED:
        print(f"{len(FAILED)} 項失敗：")
        for f in FAILED:
            print(f"  - {f}")
        return 1
    print("全部通過")
    return 0


if __name__ == "__main__":
    sys.stdout = __import__("io").TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.exit(main())
