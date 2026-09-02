"""單一換址點(Python 側)。

換帳號或換託管只改這裡;CI 上也可以用環境變數覆寫,不必改碼:

    RAW_ORG=https://raw.githubusercontent.com/<你的帳號>
    WORKER_BASE=https://taiwan-flow-v2.<你的帳號>.workers.dev

本專案總共四個換址點,搬家時要一起動:
  1. src/sites.py            這裡(Python 管線)
  2. worker/src/index.js     RAW_ORG(Worker 讀跨 repo raw)
  3. worker/wrangler.toml    DATA_BASE(Worker 讀本 repo 的 data 根)
  4. index.html              SITE(前端)
其中 2 與 3 由 worker/test/consistency.mjs 強制同源,忘記同步會在部署前失敗。

src/*.py 是以 `python src/xxx.py` 從 repo 根執行的,sys.path[0] 就是 src/,
所以 `from sites import ...` 不需要額外的路徑處理。
"""
from __future__ import annotations

import os

RAW_ORG = os.environ.get("RAW_ORG", "https://raw.githubusercontent.com/cowowboy")
WORKER = os.environ.get("WORKER_BASE", "https://taiwan-flow-v2.twradar.workers.dev")


def raw_base(repo: str) -> str:
    """<RAW_ORG>/<repo>/main"""
    return f"{RAW_ORG}/{repo}/main"


def raw(repo: str, path: str) -> str:
    """<RAW_ORG>/<repo>/main/<path>"""
    return f"{raw_base(repo)}/{path}"
