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
from typing import Any, Dict, Optional

import requests

REPO = "uninterestingfun/Market-Monitor"
API_COMMIT = f"https://api.github.com/repos/{REPO}/commits/main"
RAW_SETUP = f"https://raw.githubusercontent.com/{REPO}/main/release/MarketMonitor-Setup.exe"
HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "MarketMonitor-Updater"}

VERSION = "1.0.0"

_LOCK = threading.Lock()
_STATE: Dict[str, Any] = {
    "running": False,
    "done": False,
    "error": None,
    "path": None,
    "total": 0,
    "received": 0,
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


def check() -> Dict[str, Any]:
    local = local_info()
    try:
        r = requests.get(API_COMMIT, headers=HEADERS, timeout=15)
        r.raise_for_status()
        j = r.json()
        latest_sha = (j.get("sha") or "")[:7]
        commit = j.get("commit") or {}
        date = (commit.get("committer") or {}).get("date") or ""
        message = (commit.get("message") or "").splitlines()[0][:80]
        up_to_date = bool(local["sha"]) and local["sha"] == latest_sha
        return {
            "ok": True,
            "local": local,
            "latest_sha": latest_sha,
            "latest_date": date,
            "latest_message": message,
            "up_to_date": up_to_date,
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
    try:
        with requests.get(RAW_SETUP, headers=HEADERS, timeout=30, stream=True) as r:
            r.raise_for_status()
            total = int(r.headers.get("Content-Length") or 0)
            with _LOCK:
                _STATE.update({"total": total, "received": 0})
            tmp = dest + ".part"
            with open(tmp, "wb") as fh:
                for chunk in r.iter_content(chunk_size=256 * 1024):
                    if not chunk:
                        continue
                    fh.write(chunk)
                    with _LOCK:
                        _STATE["received"] += len(chunk)
            os.replace(tmp, dest)
        with _LOCK:
            _STATE.update({"running": False, "done": True, "path": dest, "error": None})
    except Exception as exc:
        with _LOCK:
            _STATE.update({"running": False, "done": False, "error": str(exc)[:140]})


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
