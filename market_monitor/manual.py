"""内置使用手册 PDF：随安装包分发，首次启动复制到 data/ 目录。"""
from __future__ import annotations

import os
import shutil
import sys
from typing import Optional

FILENAME = "MarketMonitor-使用手册.pdf"


def _asset_dir() -> str:
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, "market_monitor", "assets")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")


def ensure_seed(data_dir: str) -> Optional[str]:
    """把内置手册复制到 data/（已存在则跳过），返回可用路径。"""
    os.makedirs(data_dir, exist_ok=True)
    dst = os.path.join(data_dir, FILENAME)
    if os.path.exists(dst):
        return dst
    src = os.path.join(_asset_dir(), FILENAME)
    if not os.path.exists(src):
        return None
    try:
        shutil.copyfile(src, dst)
        return dst
    except Exception:
        return None


def resolve(data_dir: str) -> Optional[str]:
    """优先 data/ 里的副本，其次程序内置资源。"""
    local = os.path.join(data_dir, FILENAME)
    if os.path.exists(local):
        return local
    bundled = os.path.join(_asset_dir(), FILENAME)
    return bundled if os.path.exists(bundled) else None
