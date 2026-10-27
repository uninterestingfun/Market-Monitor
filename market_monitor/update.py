"""应用版本与自动更新。

检查远端仓库最新提交，下载最新安装包并静默覆盖安装。
源码模式下则执行 git pull。
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional

import requests

REPO = "uninterestingfun/Market-Monitor"
API_COMMIT = f"https://api.github.com/repos/{REPO}/commits/main"
COMPARE_API = f"https://api.github.com/repos/{REPO}/compare/{{sha}}...main"
RAW_SETUP = f"https://raw.githubusercontent.com/{REPO}/main/release/MarketMonitor-Setup.exe"
HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "MarketMonitor-Updater"}

# 国内直连 raw.githubusercontent.com 经常超时，按顺序尝试加速镜像
MIRRORS = [
    "https://ghfast.top/",
    "https://gh-proxy.com/",
    "https://ghproxy.net/",
    "",
]
CDN_SETUP = f"https://cdn.jsdelivr.net/gh/{REPO}@main/release/MarketMonitor-Setup.exe"


def download_urls() -> List[str]:
    urls = [prefix + RAW_SETUP for prefix in MIRRORS]
    urls.append(CDN_SETUP)
    return urls

from .version import full_version as _full_version

VERSION = _full_version()

_LOCK = threading.Lock()
_STATE: Dict[str, Any] = {
    "running": False,
    "done": False,
    "error": None,
    "path": None,
    "total": 0,
    "received": 0,
    "mirror": "",
}


def app_dir() -> str:
    """程序所在目录（冻结时为 exe 所在目录，源码时为仓库根）。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def data_dir() -> str:
    return os.path.join(app_dir(), "data")


def local_info() -> Dict[str, Any]:
    sha = ""
    built_at = ""
    try:
        from ._buildinfo import BUILD_SHA, BUILT_AT  # type: ignore

        sha = BUILD_SHA
        built_at = BUILT_AT
    except Exception:
        pass
    if not sha:
        try:
            sha = subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"], cwd=app_dir(), timeout=5,
                stderr=subprocess.DEVNULL,
            ).decode().strip()
        except Exception:
            sha = ""
    return {
        "version": VERSION,
        "sha": sha,
        "built_at": built_at,
        "mode": "exe" if getattr(sys, "frozen", False) else "source",
        "frozen": bool(getattr(sys, "frozen", False)),
    }


def _compare_status(local_sha: str) -> Dict[str, Any]:
    """用 compare 接口判断本地提交与 main 的关系。

    status: ahead = 远端更新；behind = 本地更新；identical = 一致；diverged = 分叉
    """
    if not local_sha:
        return {}
    try:
        r = requests.get(COMPARE_API.format(sha=local_sha), headers=HEADERS, timeout=(10, 25))
        if r.status_code != 200:
            return {}
        j = r.json()
        msgs = []
        for c in j.get("commits") or []:
            msg = ((c.get("commit") or {}).get("message") or "").splitlines()
            if msg:
                msgs.append(msg[0])
        return {
            "status": j.get("status") or "",
            "ahead_by": int(j.get("ahead_by") or 0),
            "behind_by": int(j.get("behind_by") or 0),
            "messages": msgs,
        }
    except Exception:
        return {}


def check() -> Dict[str, Any]:
    """按 git 提交判断新旧：本地是否落后于远端 main。版本号只用于展示。"""
    local = local_info()
    try:
        last: Optional[Exception] = None
        j = None
        for _ in range(3):
            try:
                r = requests.get(API_COMMIT, headers=HEADERS, timeout=(10, 25))
                r.raise_for_status()
                j = r.json()
                break
            except Exception as exc:
                last = exc
                time.sleep(1.5)
        if j is None:
            raise last or RuntimeError("GitHub API 不可达")

        latest_sha = (j.get("sha") or "")[:7]
        commit = j.get("commit") or {}
        date = (commit.get("committer") or {}).get("date") or ""
        message = (commit.get("message") or "").splitlines()[0][:80]

        # 优先用 compare：本地提交在 main 历史里就不算过期（发布提交本身不触发更新）
        rel = _compare_status(local["sha"])
        status = rel.get("status", "")
        if status == "ahead":
            # 只有 release: 开头的提交（仅同步二进制）不算应用更新
            msgs = rel.get("messages") or []
            needs_update = not (msgs and all(m.startswith("release:") for m in msgs))
        elif status in ("identical", "behind"):
            needs_update = False
        elif status == "diverged":
            needs_update = False
        else:
            # compare 不可用时退回直接比较
            needs_update = bool(local["sha"]) and local["sha"] != latest_sha

        return {
            "ok": True,
            "local": local,
            "local_version": local["version"],
            "latest_sha": latest_sha,
            "latest_date": date,
            "latest_message": message,
            "behind_by": rel.get("ahead_by", 0),
            "relation": status,
            "up_to_date": not needs_update,
            "needs_update": needs_update,
            "compare": "sha",
            "repo": f"https://github.com/{REPO}",
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:120], "local": local, "repo": f"https://github.com/{REPO}"}


def status() -> Dict[str, Any]:
    with _LOCK:
        return dict(_STATE)


def _download_worker() -> None:
    dest_dir = os.path.join(data_dir(), "updates")
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, "MarketMonitor-Setup.exe")
    tmp = dest + ".part"
    total = 0
    received = 0
    errors: List[str] = []

    for url in download_urls():
        label = url.split("/")[2] if "//" in url else "direct"
        try:
            headers = dict(HEADERS)
            if received:
                headers["Range"] = f"bytes={received}-"
            # 直连给较短超时，镜像放宽
            timeout = (6, 18) if label == "raw.githubusercontent.com" else (12, 60)
            with requests.get(url, headers=headers, timeout=timeout, stream=True) as r:
                if r.status_code not in (200, 206):
                    raise RuntimeError(f"HTTP {r.status_code}")
                if not total:
                    cr = r.headers.get("Content-Range") or ""
                    if "/" in cr:
                        total = int(cr.rsplit("/", 1)[-1])
                    else:
                        total = int(r.headers.get("Content-Length") or 0)
                    with _LOCK:
                        _STATE["total"] = total
                mode = "ab" if (received and r.status_code == 206) else "wb"
                if mode == "wb":
                    received = 0
                    with _LOCK:
                        _STATE["received"] = 0
                with _LOCK:
                    _STATE["mirror"] = label
                with open(tmp, mode) as fh:
                    for chunk in r.iter_content(chunk_size=256 * 1024):
                        if not chunk:
                            continue
                        fh.write(chunk)
                        received += len(chunk)
                        with _LOCK:
                            _STATE["received"] = received
                if total and received < total:
                    raise RuntimeError("下载不完整")
            os.replace(tmp, dest)
            with _LOCK:
                _STATE.update({"running": False, "done": True, "path": dest, "error": None})
            return
        except Exception as exc:
            errors.append(f"{label}: {str(exc)[:60]}")
            continue

    with _LOCK:
        _STATE.update({
            "running": False,
            "done": False,
            "error": "所有下载地址均失败（" + "；".join(errors[:3]) + "），可点「打开仓库」手动下载安装包",
        })


def start_download() -> Dict[str, Any]:
    with _LOCK:
        if _STATE["running"]:
            return {"ok": False, "error": "下载已在进行中"}
        _STATE.update({"running": True, "done": False, "error": None, "received": 0, "total": 0, "path": None})
    threading.Thread(target=_download_worker, name="update-download", daemon=True).start()
    return {"ok": True}


def apply_update() -> Dict[str, Any]:
    """启动安装包（先退出本进程），或源码模式执行 git pull。"""
    local = local_info()
    if local["frozen"]:
        with _LOCK:
            path = _STATE.get("path")
        if not path or not os.path.exists(path):
            return {"ok": False, "error": "安装包尚未下载完成"}
        target = app_dir()
        # 用独立的 cmd 等本进程退出后再覆盖安装
        cmd = f'timeout /t 2 >nul & "{path}" --silent --dir "{target}" --no-desktop --no-startmenu --launch'
        try:
            subprocess.Popen(["cmd", "/c", cmd], creationflags=0x00000008, close_fds=True)
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:120]}
        threading.Timer(0.8, lambda: os._exit(0)).start()
        return {"ok": True, "restarting": True}

    # 源码模式：git pull
    try:
        out = subprocess.check_output(["git", "pull", "--ff-only"], cwd=app_dir(),
                                      timeout=120, stderr=subprocess.STDOUT)
        return {"ok": True, "mode": "source", "output": out.decode(errors="replace")[-400:]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:160]}
