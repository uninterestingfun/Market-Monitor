"""版本号单一来源。

规则见 VERSIONING.md：语义化版本 MAJOR.MINOR.PATCH[-预发布][+构建元数据]。
改版本只改这里；构建脚本会把它写进 _buildinfo.py，界面与更新检查都读同一处。
"""
from __future__ import annotations

from typing import Tuple

__version__ = "1.1.0"

# 预发布标识：正式版留空；候选版可写 "beta.1" / "rc.2"
PRE_RELEASE = ""

RELEASE_NAME = "正式版"


def version_tuple() -> Tuple[int, int, int]:
    """(major, minor, patch) 三元组，便于比较。"""
    parts = __version__.split(".")
    nums = []
    for p in parts[:3]:
        digits = "".join(ch for ch in p if ch.isdigit())
        nums.append(int(digits) if digits else 0)
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)  # type: ignore[return-value]


def full_version() -> str:
    """完整版本号，含预发布标识。"""
    return f"{__version__}-{PRE_RELEASE}" if PRE_RELEASE else __version__


def build_stamp() -> str:
    """构建元数据：版本+构建日期+git 短码（打包后才有）。"""
    try:
        from ._buildinfo import BUILD_SHA, BUILT_AT  # type: ignore

        date = (BUILT_AT or "")[:10].replace("-", "")
        return f"{full_version()}+{date}.{BUILD_SHA}" if date else f"{full_version()}+{BUILD_SHA}"
    except Exception:
        return full_version()
