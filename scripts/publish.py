"""
publish.py —— 把 PDF 放到 GitHub Pages(docs/ 資料夾),回傳可以直接點開的網址

流程:PDF 存到 docs/<資料夾>/<日期>.pdf → git commit + push → 輪詢網址直到打得開
只有在環境變數 PUBLISH_PDF=1(GitHub Actions 裡設定)時才會 commit/push,
在自己電腦上跑只會產生檔案,不會動到 GitHub。

注意:repo 是公開的,所以這個網址知道的人都打得開。資料夾名稱是一串亂數(data/pdf_dir.txt),
只能擋住隨手亂猜,不是真正的保密。
"""

import os
import subprocess
import time
import urllib.request
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
KEEP_DAYS = 45


def pdf_dir_name():
    p = ROOT / "data" / "pdf_dir.txt"
    return p.read_text(encoding="utf-8").strip() if p.exists() else "reports"


def site_base():
    """https://<帳號>.github.io/<repo名稱>;在 Actions 裡從 GITHUB_REPOSITORY 算出來"""
    repo = os.environ.get("GITHUB_REPOSITORY", "frankchen095/tw-scanner")
    owner, name = repo.split("/", 1)
    return f"https://{owner}.github.io/{name}"


def pdf_path_for(day):
    return DOCS / pdf_dir_name() / f"{day}.pdf"


def pdf_url_for(day):
    return f"{site_base()}/{pdf_dir_name()}/{day}.pdf"


def _git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def _cleanup_old(day):
    cutoff = date.fromisoformat(day) - timedelta(days=KEEP_DAYS)
    for f in (DOCS / pdf_dir_name()).glob("*.pdf"):
        try:
            if date.fromisoformat(f.stem) < cutoff:
                f.unlink()
        except ValueError:
            pass


def _url_ok(url):
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "tw-scanner"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def publish(day, wait_seconds=420):
    """commit+push 今天的 PDF,等網址打得開。成功回傳網址,失敗回傳 None(並印出原因)。"""
    if os.environ.get("PUBLISH_PDF") != "1":
        print("  (沒有設定 PUBLISH_PDF=1,PDF 只存在本機,不發佈)")
        return None
    pdf = pdf_path_for(day)
    if not pdf.exists():
        return None
    (DOCS / ".nojekyll").touch()
    _cleanup_old(day)
    _git("config", "user.name", "github-actions[bot]")
    _git("config", "user.email", "github-actions[bot]@users.noreply.github.com")
    _git("add", "-A", "docs")
    if _git("diff", "--staged", "--quiet").returncode != 0:
        r = _git("commit", "-m", f"PDF 報表: {day}")
        if r.returncode != 0:
            print(f"  PDF commit 失敗:{r.stderr[:200]}")
            return None
    for attempt in range(3):
        r = _git("push")
        if r.returncode == 0:
            break
        _git("pull", "--rebase", "--autostash")
    else:
        print(f"  PDF push 失敗:{r.stderr[:200]}")
        return None

    url = pdf_url_for(day)
    t0 = time.time()
    while time.time() - t0 < wait_seconds:
        if _url_ok(url):
            return url
        time.sleep(10)
    print(f"  PDF 已上傳,但 {wait_seconds} 秒內網址還打不開:{url}")
    return None
