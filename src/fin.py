# src/fin.py — FinMind 共用 client（即時資金流監控用）
#
# 即時資料源：taiwan_stock_tick_snapshot（Sponsor 級，專屬 endpoint）
#   一次 request 取全市場快照（~2,800 檔），盤中即時更新、盤後為當日最終值。
#   欄位：close, change_rate, average_price, total_volume, total_amount(累計成交金額,元),
#         buy_volume/sell_volume(最佳買賣盤量), volume_ratio, date(時間戳), stock_id
#
# token：環境變數 FINMIND_TOKEN 或 repo 根 .env（不進 git）
#
# **token 走 Authorization header，不進 query string。**
# 原本是 params 裡帶 token=...，而 requests 的 HTTPError 訊息含完整 URL——
# 任何一句 `print(f"失敗: {e}")` 就會把 token 明文印出來。本 repo 有 11 支
# builder 用這支 client、其中 10 處直接印例外，等於 10 個外洩點。
# CI 裡 GitHub 會把 secret 遮成 ***，本機不會（終端＋scrollback）。
# 移到 header 之後，token 結構上不可能出現在錯誤訊息或任何 URL log 裡。
#
# 2026-09-21 實測確認 FinMind 認得這個 header（不是猜的）：
#   snapshot 端點完全不給 token → 400 "Your level is free"
#   只用 Authorization 給錯的 token → 400 "Token is illegal"
#   只用 Authorization 給正確 token → 200，2,878 檔
# 伺服器分得出「沒給」與「給錯」，代表 header 真的有被解析。

from __future__ import annotations
import os
import re
import requests
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = "https://api.finmindtrade.com/api/v4/data"
SNAP = "https://api.finmindtrade.com/api/v4/taiwan_stock_tick_snapshot"


def token() -> str:
    t = os.environ.get("FINMIND_TOKEN")
    if t:
        return t.strip()
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("FINMIND_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("找不到 FINMIND_TOKEN（環境變數或 .env）")


def auth() -> dict:
    """Authorization header。token 只走這裡，不要再放回 params。"""
    return {"Authorization": f"Bearer {token()}"}


def redact(e: object) -> str:
    """錯誤訊息脫敏後才能印。

    token 已經不在 URL 裡了，這支是第二道保險，遮兩種形式：
      token=...      日後有人把它加回 query string
      Bearer ...     header 路徑自己的例外會帶出整串——token 若含內嵌換行，
                     requests 丟 InvalidHeader 並把 'Bearer <整串>' 放進訊息；
                     含非 latin-1 字元則是 UnicodeEncodeError 帶出片段。
                     兩者都繞過只遮 token= 的版本。
    印例外一律過這支。
    """
    s = re.sub(r"(token=)[^&\s]+", r"\1<redacted>", str(e))
    return re.sub(r"(Bearer\s+)\S+", r"\1<redacted>", s)


def api_get(dataset: str, **params) -> list:
    """通用 /api/v4/data 查詢（建分類表用）。"""
    if "token" in params:
        # 舊版的 params.update(token=token()) 會把呼叫端傳的蓋掉,現在不會——
        # 不擋的話「token 不進 URL」就只是慣例而非保證。
        raise ValueError("token 不要放進 params，它走 Authorization header")
    params.update(dataset=dataset)
    r = requests.get(BASE, params=params, headers=auth(), timeout=40)
    r.raise_for_status()
    j = r.json()
    if j.get("status") not in (200, None):
        raise RuntimeError(f"{dataset}: {j.get('msg')}")
    return j.get("data") or []


def snapshot_all() -> list:
    """全市場即時快照（一次 request、無 data_id）。"""
    r = requests.get(SNAP, headers=auth(), timeout=40)
    r.raise_for_status()
    j = r.json()
    if j.get("status") != 200:
        raise RuntimeError(f"snapshot: {j.get('msg')}")
    return j.get("data") or []
