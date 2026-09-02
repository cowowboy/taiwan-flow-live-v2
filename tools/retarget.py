#!/usr/bin/env python3
"""把整套站台換到另一個帳號/託管:一次改完四個 repo 的所有換址點。

換址點分散在四個 repo、六種檔案,而且其中兩個(CSP meta、回 Hub 靜態連結)
插不了值必須手動改。手動換一定會漏,漏掉的後果是靜默壞掉——Worker 一半讀
新家一半讀舊家不會報錯,CSP 沒放行則是瀏覽器靜默擋掉、頁面只是空白。

這支把「換址」變成一個指令,改完自動跑三個 repo 的一致性測試驗收。

用法(從 taiwan-flow-live-v2 執行,四個 repo 需為同層目錄):

    python tools/retarget.py --dry-run \
        --raw-org https://raw.githubusercontent.com/myacct \
        --worker  https://taiwan-flow-v2.myacct.workers.dev \
        --hub     https://myacct.github.io

    python tools/retarget.py --raw-org ... --worker ... --hub ...   # 實際寫入

改完務必逐 repo 檢視 `git diff` 再提交。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # taiwan-flow-live-v2
SIBLINGS = ROOT.parent                                  # 四個 repo 的同層父目錄


def sub_file(path: Path, pairs: list[tuple[str, str]], dry: bool) -> int:
    """對單一檔案套用 (regex, repl);回傳實際替換次數。"""
    if not path.exists():
        print(f"    ! 找不到 {path}")
        return 0
    src = path.read_text(encoding="utf-8")
    out, total = src, 0
    for pat, repl in pairs:
        out, n = re.subn(pat, repl, out)
        total += n
    if total and not dry:
        path.write_text(out, encoding="utf-8")
    rel = path.relative_to(SIBLINGS)
    print(f"    {'(dry) ' if dry else ''}{rel}: {total} 處")
    return total


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw-org", required=True, help="例 https://raw.githubusercontent.com/myacct")
    ap.add_argument("--worker", required=True, help="例 https://taiwan-flow-v2.myacct.workers.dev")
    ap.add_argument("--hub", required=True, help="例 https://myacct.github.io")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    for name, val in (("raw-org", a.raw_org), ("worker", a.worker), ("hub", a.hub)):
        if val.endswith("/"):
            print(f"--{name} 結尾不要斜線: {val}", file=sys.stderr)
            return 2

    # 舊值用寬鬆 pattern 比對,不預設現在是哪個帳號
    RAW_RE = r'https://raw\.githubusercontent\.com/[A-Za-z0-9_.-]+'
    WK_RE = r'https://[a-z0-9-]+\.[a-z0-9-]+\.workers\.dev'
    HUB_RE = r'https://[A-Za-z0-9-]+\.github\.io'
    # 2026-09-02 補:第一次換址漏掉整整一類——不是 raw 網址但一樣綁帳號的地方。
    # 最嚴重的是 worker 的 GH_OWNER(現已改成從 RAW_ORG 衍生),其次是 footer 的
    # github.com 連結、api.github.com 呼叫、taiwan-flows 的 SITE.owner。
    # 漏掉 GH_OWNER 的後果特別隱蔽:dispatch 會打到原作者的 repo 全部 403,
    # 但程式的「secret 未設就跳過」邏輯讓它看起來像沒設定。
    GH_RE = r'https://github\.com/[A-Za-z0-9_.-]+'
    APIGH_RE = r'https://api\.github\.com/repos/[A-Za-z0-9_.-]+'
    owner = a.raw_org.rstrip("/").split("/")[-1]
    GH_NEW = f"https://github.com/{owner}"
    APIGH_NEW = f"https://api.github.com/repos/{owner}"
    OWNER_RE = r'owner:\s*"[A-Za-z0-9_.-]+"'
    OWNER_NEW = f'owner: "{owner}"'

    total = 0
    print(f"目標: raw={a.raw_org}  worker={a.worker}  hub={a.hub}\n")

    print("[1] taiwan-flow-live-v2")
    total += sub_file(ROOT / "worker/src/index.js", [(RAW_RE, a.raw_org), (GH_RE, GH_NEW), (APIGH_RE, APIGH_NEW)], a.dry_run)
    total += sub_file(ROOT / "worker/wrangler.toml", [(RAW_RE, a.raw_org), (GH_RE, GH_NEW), (APIGH_RE, APIGH_NEW)], a.dry_run)
    total += sub_file(ROOT / "src/sites.py",
                      [(RAW_RE, a.raw_org), (WK_RE, a.worker), (HUB_RE, a.hub),
                       (GH_RE, GH_NEW), (APIGH_RE, APIGH_NEW), (OWNER_RE, OWNER_NEW)], a.dry_run)
    total += sub_file(ROOT / "index.html",
                      [(RAW_RE, a.raw_org), (WK_RE, a.worker), (HUB_RE, a.hub),
                       (GH_RE, GH_NEW), (APIGH_RE, APIGH_NEW), (OWNER_RE, OWNER_NEW)], a.dry_run)

    for repo in ("postmkt", "taiwan-stock-news", "taiwan-flows"):
        d = SIBLINGS / repo
        if not d.is_dir():
            print(f"[!] 跳過 {repo}(不在 {SIBLINGS})")
            continue
        print(f"[{repo}]")
        sites = d / "src/sites.py"
        if not sites.exists():
            sites = d / "sites.py"
        if sites.exists():   # taiwan-flows 沒有 sites.py——它不讀 raw 資料,只需要 owner
            total += sub_file(sites, [(RAW_RE, a.raw_org), (WK_RE, a.worker), (HUB_RE, a.hub),
                              (GH_RE, GH_NEW), (APIGH_RE, APIGH_NEW), (OWNER_RE, OWNER_NEW)],
                              a.dry_run)
        # index.html 同時涵蓋 SITE、CSP connect-src 白名單、回 Hub 靜態連結
        total += sub_file(d / "index.html",
                          [(RAW_RE, a.raw_org), (WK_RE, a.worker), (HUB_RE, a.hub),
                       (GH_RE, GH_NEW), (APIGH_RE, APIGH_NEW), (OWNER_RE, OWNER_NEW)], a.dry_run)

    print(f"\n合計 {total} 處{'(dry-run,未寫入)' if a.dry_run else ''}")
    if a.dry_run:
        return 0

    print("\n[驗收] 跑三個 repo 的一致性測試")
    checks = [
        ("v2 worker", ["node", "test/consistency.mjs"], ROOT / "worker"),
        ("postmkt", [sys.executable, "-m", "pytest", "tests/test_sites_consistency.py", "-q"],
         SIBLINGS / "postmkt"),
        ("news", [sys.executable, "tests/test_sites_consistency.py"], SIBLINGS / "taiwan-stock-news"),
    ]
    bad = 0
    for label, cmd, cwd in checks:
        if not cwd.is_dir():
            continue
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
        print(f"  {'✓' if r.returncode == 0 else '✗'} {label}")
        if r.returncode:
            bad += 1
            print("    " + (r.stdout + r.stderr).strip().replace("\n", "\n    ")[:800])
    if bad:
        print(f"\n✗ {bad} 個 repo 的換址點不一致 —— 修好再提交")
        return 1
    print("\n✓ 全部一致。逐 repo 檢視 git diff 後再提交。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
