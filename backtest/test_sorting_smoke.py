# backtest/test_sorting_smoke.py — run_sorting.py 的離線煙霧/回歸測試
#
# 為什麼需要這支：run_sorting.py 要有 backtest/cache/ 的真實快取才跑得動，
# 但快取不進 git（.gitignore），所以改壞了不會有任何東西擋。這支用合成樣本
# 直接呼叫各 run_* 函式，驗「程式路徑走得通、輸出格式對、降級分支文案正確」——
# **不驗數字正確性**（那要真實快取）。
#
# 守的具體 bug（2026-07-26 code review 抓到）：
#   run_m4 原本用 best_spread 初值 0.0 搭配 `>` 比較，當三個候選的分位都切得出來
#   但分離度恰為 0.0 時，best_name 會停在 None，於是印「三個候選皆樣本不足」——
#   與上一行剛印出的完整分位表自相矛盾。見 test_m4_zero_spread_not_called_insufficient。
#
# 用法：python backtest/test_sorting_smoke.py（免 token、免網路、免快取）

from __future__ import annotations
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_sorting as rs   # noqa: E402

FAILED = []


def check(label, cond):
    ok = bool(cond)
    print(f"{'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        FAILED.append(label)


def mk(n, *, e3=None, regime_mix=1.0, surge_in=False, seed=None):
    """造 n 筆合成 stock-day 樣本；欄位須與 build_stock_samples 的 27 個 key 一致。"""
    rnd = random.Random(seed if seed is not None else 42)
    rows = []
    for i in range(n):
        rows.append(dict(
            d=f"2025-{7 + i % 6:02d}-{1 + i % 28:02d}", c=f"{1000 + i}",
            surge=rnd.uniform(2.0, 8.0) if surge_in else 1.0,
            ret=rnd.uniform(0.02, 0.09) if surge_in else 0.01,
            pos=rnd.uniform(0.7, 1.0) if surge_in else 0.5,
            amt=rnd.uniform(1e8, 5e9),
            r1=0.0, r2=0.0, r3=0.0, e1=0.0, e2=0.0,
            e3=e3 if e3 is not None else rnd.gauss(-0.005, 0.04),
            lim=False, limd=False,
            it3=2 if surge_in else 1, fi3=2 if surge_in else 1,
            ints=rnd.uniform(-0.15, 0.15), ma=True,
            bias=rnd.uniform(-0.15, 0.25), brk=True, brkd=False,
            volt=rnd.uniform(0.4, 2.5),
            regime="bull" if rnd.random() < regime_mix else "bear",
            break_depth=None, trust_buy_amt=0.0, foreign_buy_amt=0.0,
            consec_exit=0,
        ))
    return rows


def concl_of(lines):
    return [l for l in lines if l.startswith("**結論**")]


def test_sample_keys_match_builder():
    """合成樣本的欄位必須與真實 builder 一致，否則這支測試等於在驗假的東西。"""
    import inspect
    src = inspect.getsource(rs.build_stock_samples)
    synth = set(mk(1)[0])
    missing = [k for k in synth if f"{k}=" not in src]
    check(f"合成樣本欄位都存在於 build_stock_samples（缺 {missing}）", not missing)


def test_r2_flip():
    lines = []
    rows = [*mk(200, e3=0.02, regime_mix=1.0, surge_in=True),
            *mk(200, e3=-0.02, regime_mix=0.0, surge_in=True)]
    rs.run_r2(rows, lines)
    txt = "\n".join(lines)
    check("R2 翻向時標 ⚠", "超額翻向：是 ⚠" in txt)
    check("R2 翻向時結論說需掛閘門", "需掛 regime 閘門" in concl_of(lines)[0])
    check("R2 有多空各自逐月", "### 多頭逐月" in txt and "### 空頭逐月" in txt)
    check("R2 寫出母體定義（與 M3 同一組）", "與 M3 同一母體" in txt)


def test_r2_no_flip():
    lines = []
    rows = [*mk(200, e3=0.02, regime_mix=1.0, surge_in=True),
            *mk(200, e3=0.01, regime_mix=0.0, surge_in=True)]
    rs.run_r2(rows, lines)
    check("R2 不翻向時結論說不需閘門", "不需 regime 閘門" in concl_of(lines)[0])


def test_r2_one_side_empty():
    lines = []
    rs.run_r2(mk(100, regime_mix=1.0, surge_in=True), lines)   # 全多頭，空頭無樣本
    check("R2 單邊無樣本不崩且維持未驗證", "維持「未驗證」" in concl_of(lines)[0])


def test_r2_pool_matches_m3():
    """R2 的全環境列必須能對上 M3 基準——同一個 pool，N 必須相同。"""
    rows = mk(300, surge_in=True)
    l2, l3 = [], []
    rs.run_r2(rows, l2)
    rs.run_m3(rows, l3)
    def first_n(lines):
        for l in lines:
            if "N=" in l:
                return l.split("N=")[1].split()[0]
        return None
    check(f"R2 全環境 N == M3 基準 N（{first_n(l2)} vs {first_n(l3)}）",
          first_n(l2) == first_n(l3))


def test_m4_zero_spread_not_called_insufficient():
    """回歸測試：分位切得出來但 spread 恰為 0.0，不得誤稱『樣本不足』。"""
    lines = []
    rs.run_m4(mk(300, e3=0.01), lines)
    txt, c = "\n".join(lines), concl_of(lines)
    check("M4 spread=0 情境確實切出分位", "Q1(前)" in txt)
    check("M4 spread=0 只有一行結論", len(c) == 1)
    check("M4 spread=0 不誤稱樣本不足", "樣本不足" not in c[0])
    check("M4 spread=0 走未達門檻分支", "未達先訂門檻" in c[0])
    check("M4 spread=0 仍判定不排序", "不排序" in c[0])


def test_m4_truly_insufficient():
    lines = []
    rs.run_m4(mk(20), lines)
    c = concl_of(lines)
    check("M4 真樣本不足才說切不出來", "切不出來" in c[0])
    check("M4 真樣本不足時沒印分位表", "Q1(前)" not in "\n".join(lines))


def test_m4_pool_matches_m2_fallback():
    """卡3 的母體必須等於 M2 陣亡後退回的 brk_only，否則排序驗在別的母體上。"""
    rows = mk(300)
    expect = len([r for r in rows if r["brk"] and not r["lim"]])
    lines = []
    rs.run_m4(rows, lines)
    got = next((l.split("N=")[1].split()[0] for l in lines if "N=" in l), None)
    check(f"M4 母體 == brk and not lim（{got} vs {expect}）", str(expect) == got)


def test_m4_threshold_uses_constant():
    lines = []
    rs.run_m4(mk(300, e3=0.01), lines)
    check(f"M4 報告印出的門檻＝M4_MIN_SPREAD（{rs.M4_MIN_SPREAD}）",
          f"{rs.M4_MIN_SPREAD:.2f}%" in "\n".join(lines))


def test_tertile_fallback():
    lines = []
    rs.run_m4(mk(200), lines)
    txt = "\n".join(lines)
    check("150≤N<250 走三分位並標註", "T1(前)" in txt and "三分位" in txt)


def test_section_conventions():
    """段落慣例：標題帶前置換行、結論行格式、段尾空行。"""
    for fn, title in [(rs.run_r2, "# R2."), (rs.run_m4, "# M4.")]:
        lines = []
        fn(mk(300, surge_in=True), lines)
        check(f"{title} 標題帶前置空行", lines[0].startswith("\n" + title))
        check(f"{title} 有結論行", len(concl_of(lines)) >= 1)
        check(f"{title} 段尾為空行", lines[-1] == "")


def main():
    for fn in [test_sample_keys_match_builder,
               test_r2_flip, test_r2_no_flip, test_r2_one_side_empty,
               test_r2_pool_matches_m3,
               test_m4_zero_spread_not_called_insufficient,
               test_m4_truly_insufficient, test_m4_pool_matches_m2_fallback,
               test_m4_threshold_uses_constant, test_tertile_fallback,
               test_section_conventions]:
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
    sys.exit(main())
