# src/build_us_sectors.py — 美股族群資金流向 → data/us_sector_flow.json
#
# 給每日晨報用的「昨夜錢往哪裡去」。回答兩個分開的問題，**不可混為一談**：
#   當日流向 = 漲跌% + 收在當日區間% + 量比   （昨晚誰在買賣、收盤時買方還在不在）
#   多週趨勢 = 近 20 日報酬                    （這個族群在什麼位置）
# 用本檔的算法實算:2026-09-17 光通訊核心 −0.08%／收在區間 10%,隔天 9/18 就是
# +3.96%／收在區間 58%。單日訊號很不穩定，寫晨報時不要用趨勢的語言描述它。
#
# **刻意與 build_us.py 分開、排在它之後。**
# 理由是故障隔離,不是耗時:實測 52 檔全程約 11 秒,併進去也不會拖垮什麼。
# 但 us.json 是晨報最依賴的資料,而它的 FinMind 來源約 08:00 才入庫、晨報 08:15 啟動,
# 中間只有十幾分鐘。族群這支多打 52 次 API,多一分失敗或卡住的機會就多一分風險——
# 分開跑,它掛掉時 us.json 已經 commit+push 完了,晨報照常產製,只是少一個區塊。
#
# 成分怎麼來的：光通訊三組是**驗過的**（每檔算「跟核心籃子的日報酬相關」減
# 「跟 ^IXIC 的相關」，差 ≥0.15 才收；低於大盤基準的一律排除）。
# 其餘各組仍是依產品線推的，尚未驗證——`validated` 欄標示這件事，讀的人要知道差別。
# 驗證方法與排除名單見 FinMind workspace 的 data/us_sectors.json 與 tools/verify_peers.py。
#
# 用法：FINMIND_TOKEN=... python src/build_us_sectors.py

from __future__ import annotations
import json
import re
import statistics as st
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fin  # noqa: E402

# 檔名刻意不叫 us_sectors.json——FinMind workspace 的 data/us_sectors.json 是
# **成分定義與驗證記錄**（哪些代號憑什麼收進這組），這支寫的是**每日流向數據**。
# 兩者同名不同義，撞名遲早出事。
OUT = fin.ROOT / "data" / "us_sector_flow.json"
TPE = timezone(timedelta(hours=8))
TREND_DAYS = 20
DISP_WARN = 2.5          # 組內漲跌標準差超過這個值，組平均不宜直接解讀

SECTORS: list[tuple[str, list[str], bool]] = [
    # (族群, 成分, 成分是否驗證過)
    ("光通訊核心",   ["LITE", "AAOI", "COHR", "VIAV", "FN", "CIEN"], True),
    ("光通訊上游",   ["AXTI", "TSEM", "GLW"], True),
    ("AI／GPU",      ["NVDA", "AMD", "AVGO", "MRVL", "SMCI"], False),
    ("半導體設備",   ["AMAT", "LRCX", "KLAC", "ASML", "ONTO", "TER", "COHU"], False),
    ("記憶體",       ["MU", "STX", "SNDK"], False),
    ("類比／功率",   ["ON", "DIOD", "VSH", "ADI", "TXN", "MPWR", "NXPI"], False),
    ("封測",         ["AMKR", "ASX"], False),
    ("材料／基板",   ["ROG", "DD", "CE"], False),
    ("連接器",       ["APH", "TEL"], False),
    ("EDA／IP",      ["SNPS", "CDNS", "ARM"], False),
    ("網通",         ["ANET", "CSCO"], False),
    ("IT 方案／通路", ["CDW", "NSIT", "CNXN", "ARW", "AVT", "SNX"], False),
    ("光罩",         ["PLAB"], False),
]
INDEXES = [("^SOX", "費城半導體"), ("^IXIC", "那斯達克")]


def redact(e: object) -> str:
    """錯誤訊息脫敏後才能印。

    fin.api_get 把 token 放在 query string,而 requests 的 HTTPError 訊息會帶上
    完整 URL——直接 print(e) 等於把 FinMind token 明文印進終端與 scrollback。
    CI 裡 GitHub 會把 secret 遮成 ***,本機不會。
    """
    return re.sub(r"(token=)[^&\s]+", r"\1<redacted>", str(e))


def fv(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def series(tic: str) -> list[dict]:
    start = (date.today() - timedelta(days=70)).isoformat()
    rows = fin.api_get("USStockPrice", data_id=tic, start_date=start)
    return [r for r in rows if fv(r.get("Close"))]


def metrics(rows: list[dict]) -> dict | None:
    if len(rows) < 2:
        return None
    cur, prev = rows[-1], rows[-2]
    c, pc = fv(cur["Close"]), fv(prev["Close"])
    hi, lo = fv(cur.get("High")), fv(cur.get("Low"))
    # 要滿 6 筆才算得出「前 5 日均量」。不足就不給 vr,不要拿 2 日均量冒充。
    vols = [fv(r.get("Volume")) or 0 for r in rows[-6:-1]] if len(rows) >= 6 else []
    v5 = sum(vols) / len(vols) if vols else 0
    vol = fv(cur.get("Volume")) or 0
    closes = [fv(r["Close"]) for r in rows]
    t20 = ((closes[-1] / closes[-1 - TREND_DAYS] - 1) * 100
           if len(closes) > TREND_DAYS else None)
    return dict(
        date=cur["date"], c=c,
        chg=(c / pc - 1) * 100,
        # 收在當日區間的位置。**比漲跌重要**：漲 2% 收在 30% 是開高走低，
        # 漲 2% 收在 95% 才是全天走強收高。
        pos=((c - lo) / (hi - lo) * 100) if (hi is not None and lo is not None and hi > lo) else None,
        vr=(vol / v5) if (v5 and vol) else None,
        t20=t20,
    )


def main() -> int:
    tics = sorted({t for _, m, _ in SECTORS for t in m} | {s for s, _ in INDEXES})
    print(f"抓 {len(tics)} 檔…", flush=True)
    M: dict[str, dict] = {}
    for t in tics:
        try:
            m = metrics(series(t))
        except Exception as e:                                # noqa: BLE001
            print(f"  ! {t}: {redact(e)}", file=sys.stderr)
            m = None
        if m:
            M[t] = m

    if not M:
        print("一檔都沒抓到，不寫檔", file=sys.stderr)
        return 1

    # 多數代號的最新日＝本檔的資料日。個別落後的不併入該組平均，另外列出來——
    # FinMind 是逐檔更新的，混在一起算平均會把不同日的數字加在一起。
    newest = st.mode([m["date"] for m in M.values()])
    lagging = sorted(t for t, m in M.items() if m["date"] != newest)

    out_sectors = []
    for name, members, validated in SECTORS:
        got = [M[t] for t in members if t in M and M[t]["date"] == newest]
        if not got:
            continue
        pos = [g["pos"] for g in got if g["pos"] is not None]
        vr = [g["vr"] for g in got if g["vr"]]
        t20 = [g["t20"] for g in got if g["t20"] is not None]
        chg = [g["chg"] for g in got]
        disp = st.pstdev(chg) if len(chg) > 1 else 0.0
        out_sectors.append({
            "g": name,
            "n": len(got),
            "n_defined": len(members),
            "chg": round(st.mean(chg), 2),
            "pos": round(st.median(pos)) if pos else None,
            "vr": round(st.mean(vr), 2) if vr else None,
            "disp": round(disp, 1),
            "t20": round(st.mean(t20), 1) if t20 else None,
            "validated": validated,
            # 離散大就別把組平均當一回事;這欄是給讀的人打折用的
            "reliable": disp <= DISP_WARN and len(got) == len(members),
            "rows": [{"s": t, "chg": round(M[t]["chg"], 2),
                      "pos": round(M[t]["pos"]) if M[t]["pos"] is not None else None,
                      "vr": round(M[t]["vr"], 2) if M[t]["vr"] else None}
                     for t in members if t in M and M[t]["date"] == newest],
        })

    # 指數也要對齊資料日,否則 sox_vs_ndx 會是「跨日相減」。族群成員在 :118 已經擋掉了,
    # 指數漏掉的話反而是最會誤導的一格——整份的 event_kind 都掛在它上面。
    idx = [{"s": s, "n": n, "chg": round(M[s]["chg"], 2),
            "pos": round(M[s]["pos"]) if M[s]["pos"] is not None else None}
           for s, n in INDEXES if s in M and M[s]["date"] == newest]
    # 費半與那斯達克的背離:同向同幅＝大盤事件，整組面對同樣的環境;
    # 背離大＝族群事件，只有對應到弱勢族群的標的承壓，不要整組同等看待。
    div = None
    if len(idx) == 2:
        div = round(idx[0]["chg"] - idx[1]["chg"], 2)

    out = {
        "schema": 1,
        "date": newest,
        "generated_at": datetime.now(TPE).isoformat(timespec="seconds"),
        "trend_days": TREND_DAYS,
        "indexes": idx,
        "sox_vs_ndx": div,
        "event_kind": (None if div is None else ("族群事件" if abs(div) >= 3 else "大盤事件")),
        "lagging": lagging,
        "sectors": sorted(out_sectors, key=lambda s: -s["chg"]),
    }
    # 與 build_us.py 同樣的慣例：內容沒變就保留原時戳，重試空轉不污染 generated_at
    try:
        old = json.loads(OUT.read_text(encoding="utf-8"))
        strip = lambda d: {k: v for k, v in d.items() if k != "generated_at"}  # noqa: E731
        if strip(old) == strip(out) and old.get("generated_at"):
            out["generated_at"] = old["generated_at"]
    except (OSError, ValueError, AttributeError):
        pass
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")) + "\n",
                   encoding="utf-8")
    print(f"us_sector_flow.json: {newest}, {len(out_sectors)} 族群, "
          f"{len(M)} 檔" + (f", 落後 {' '.join(lagging)}" if lagging else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
