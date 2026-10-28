"""从命令行生成每日报告，可保存并推送到远端仓库。

用法：
    python run.py report                 # 生成并保存到 reports/ 与 data/reports/
    python run.py report --push          # 额外提交并推送到远端仓库
    python run.py report --date 2026-10-28
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from typing import Any, Dict, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _wait_quotes(service: Any, timeout: float = 90.0) -> None:
    """等首批行情到位（最多 timeout 秒）。"""
    from . import config

    target = int(len(config.ASSETS) * 0.6)
    deadline = time.time() + timeout
    while time.time() < deadline:
        quotes = service.quote_map()
        ready = sum(1 for q in quotes.values() if q.get("price") is not None)
        if ready >= target:
            return
        time.sleep(2.0)


def generate(service: Any, timeout: float = 90.0) -> Dict[str, Any]:
    from . import report

    _wait_quotes(service, timeout)
    return report.build(service)


def save(payload: Dict[str, Any], root: Optional[str] = None) -> Dict[str, str]:
    """写入 reports/YYYY-MM-DD.md（仓库）与 data/reports/（本地归档）。"""
    root = root or ROOT
    date = payload.get("date") or time.strftime("%Y-%m-%d")
    text = payload.get("text") or ""
    repo_dir = os.path.join(root, "reports")
    local_dir = os.path.join(root, "data", "reports")
    os.makedirs(repo_dir, exist_ok=True)
    os.makedirs(local_dir, exist_ok=True)
    repo_path = os.path.join(repo_dir, f"{date}.md")
    local_path = os.path.join(local_dir, f"{date}.md")
    for path in (repo_path, local_path):
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text.rstrip() + "\n")
    return {"repo": repo_path, "local": local_path}


def push(root: Optional[str] = None) -> Dict[str, Any]:
    """提交 reports/ 并推送。"""
    root = root or ROOT
    env = dict(os.environ)
    env.setdefault("GIT_SSH", r"C:\Windows\System32\OpenSSH\ssh.exe")
    steps = []
    try:
        subprocess.run(["git", "add", "reports"], cwd=root, check=True, capture_output=True)
        diff = subprocess.run(["git", "diff", "--cached", "--name-only"], cwd=root, capture_output=True, text=True)
        if not (diff.stdout or "").strip():
            return {"ok": True, "pushed": False, "detail": "没有变更"}
        date = time.strftime("%Y-%m-%d")
        subprocess.run(["git", "commit", "-m", f"report: {date} 每日报告"], cwd=root, check=True, capture_output=True)
        out = subprocess.run(["git", "push", "origin", "HEAD"], cwd=root, env=env, capture_output=True, text=True)
        steps.append(out.stderr.strip()[-200:])
        if out.returncode != 0:
            return {"ok": False, "pushed": False, "detail": out.stderr.strip()[-300:]}
        return {"ok": True, "pushed": True, "detail": steps[-1] if steps else ""}
    except subprocess.CalledProcessError as exc:
        msg = (exc.stderr or b"").decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else str(exc)
        return {"ok": False, "pushed": False, "detail": msg[-300:]}
