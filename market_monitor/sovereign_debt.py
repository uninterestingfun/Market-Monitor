"""各国官方统计源的债务数据。

优先级：本地 CSV（data/sovereign_debt/<ISO3>.csv，用户从各国财政部导出）
      > 官方接口（美国财政部 FiscalData、巴西央行 SGS 13762）
      > 世界银行
"""
from __future__ import annotations

import csv
import os
import threading
import time
from typing import Any, Dict, List, Optional

from .net import http

_LOCK = threading.Lock()
_CACHE: Dict[str, tuple] = {}
_DATA_DIR: Optional[str] = None

LOCAL_DIRNAME = "sovereign_debt"


def set_data_dir(path: str) -> None:
    """由服务端注入数据目录（通常是 <安装目录>/data）。"""
    global _DATA_DIR
    _DATA_DIR = path


def _local_dir() -> Optional[str]:
    if not _DATA_DIR:
        return None
    return os.path.join(_DATA_DIR, LOCAL_DIRNAME)


def _num(value: Any) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace("%", "").replace("$", "")
    if not text or text in ("-", "--", "N/A", "na"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _get(key: str, ttl: float) -> Optional[Any]:
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and time.time() - hit[1] < ttl:
            return hit[0]
    return None


def _put(key: str, value: Any) -> None:
    with _LOCK:
        _CACHE[key] = (value, time.time())


def _load_local(code: str) -> Optional[Dict[str, Any]]:
    """读取 data/sovereign_debt/<CODE>.csv 的最后一条有效记录。"""
    base = _local_dir()
    if not base:
        return None
    path = os.path.join(base, f"{code}.csv")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            lines = [ln for ln in fh if ln.strip() and not ln.lstrip().startswith("#")]
        rows: List[tuple[str, Optional[float], Optional[float], str]] = []
        for row in csv.DictReader(lines):
            day = (row.get("date") or row.get("year") or "").strip()
            if not day:
                continue
            pct = _num(row.get("debt_pct") or row.get("pct"))
            amount = _num(row.get("debt_amount") or row.get("amount"))
            if pct is None and amount is None:
                continue
            rows.append((day, pct, amount, (row.get("source") or "").strip()))
        if not rows:
            return None
        rows.sort(key=lambda item: item[0])
        day, pct, amount, source = rows[-1]
        return {
            "pct": pct,
            "amount": amount,
            "as_of": day,
            "source": source or f"本地 CSV（{code}.csv）",
            "local": True,
        }
    except Exception:
        return None


def _us_debt() -> Optional[Dict[str, Any]]:
    """美国财政部：总公共债务余额（美元）。"""
    url = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v2/accounting/od/debt_to_penny"
    try:
        data = http.get_json(url, params={"sort": "-record_date", "page[size]": 1}, timeout=15)
    except Exception:
        return None
    rows = (data or {}).get("data") or []
    if not rows:
        return None
    row = rows[0]
    amount = row.get("tot_pub_debt_out_amt")
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return None
    return {"amount": amount, "as_of": row.get("record_date"), "source": "美国财政部 FiscalData"}


def _br_debt() -> Optional[Dict[str, Any]]:
    """巴西央行 SGS 13762：一般政府总债务占 GDP（%）。"""
    url = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.13762/dados/ultimos/2"
    try:
        rows = http.get_json(url, params={"formato": "json"}, timeout=15) or []
    except Exception:
        return None
    if not rows:
        return None
    last = rows[-1]
    try:
        pct = float(last.get("valor"))
    except (TypeError, ValueError):
        return None
    raw = last.get("data") or ""
    as_of = raw
    if "/" in raw:
        d, m, y = raw.split("/")
        as_of = f"{y}-{m}-{d}"
    return {"pct": pct, "as_of": as_of, "source": "巴西央行 BCB SGS 13762"}


# 国家 -> 官方接口抓取函数
ADAPTERS = {
    "USA": _us_debt,
    "BRA": _br_debt,
}


def snapshot(gdp: Dict[str, Optional[float]]) -> Dict[str, Dict[str, Any]]:
    """优先本地 CSV，其次官方接口；gdp 用于把绝对金额换算成占 GDP 比例。"""
    cached = _get("snapshot", 3600)
    if cached is not None:
        return cached
    out: Dict[str, Dict[str, Any]] = {}

    # 1) 本地 CSV 覆盖（用户从各国财政部导出）
    base = _local_dir()
    if base and os.path.isdir(base):
        for name in os.listdir(base):
            if not name.lower().endswith(".csv"):
                continue
            code = os.path.splitext(name)[0].upper()
            item = _load_local(code)
            if item:
                out[code] = item

    # 2) 官方接口（本地 CSV 优先，不覆盖已有项）
    for code, fn in ADAPTERS.items():
        if code in out:
            continue
        try:
            item = fn()
        except Exception:
            item = None
        if item:
            out[code] = item

    # 3) 绝对金额 -> 占 GDP 比例
    for code, item in out.items():
        if item.get("pct") is None and item.get("amount") and gdp.get(code):
            item["pct"] = item["amount"] / gdp[code] * 100.0
    _put("snapshot", out)
    return out


TEMPLATE = """# {name} 主权债务数据（从本国财政部 / 统计局页面导出后粘贴到此处）
#
# 列说明：
#   date        数据日期，YYYY-MM-DD 或 YYYY
#   debt_pct    债务总额 / GDP，百分比数值（如 131.4）
#   debt_amount 债务总额，本国货币绝对金额（如 40444342709389）
#   source      来源说明（会显示在界面上）
#
# 规则：
#   - debt_pct 与 debt_amount 至少填一个；只填 amount 时按最新 GDP 自动换算
#   - 按 date 排序，程序取最后一条有效记录
#   - 以 # 开头的行会被忽略
date,debt_pct,debt_amount,source
2026-09-30,,,{name}财政部
"""


def write_templates(data_dir: str) -> int:
    """在 data/sovereign_debt/ 下为 20 个国家各生成一个 CSV 模板。"""
    from .macro import COUNTRIES

    base = os.path.join(data_dir, LOCAL_DIRNAME)
    os.makedirs(base, exist_ok=True)
    count = 0
    for code, name in COUNTRIES:
        path = os.path.join(base, f"{code}.csv")
        if os.path.exists(path):
            continue
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(TEMPLATE.format(name=name))
        count += 1
    guide = os.path.join(base, "README.txt")
    if not os.path.exists(guide):
        with open(guide, "w", encoding="utf-8", newline="") as fh:
            fh.write(
                "把各国财政部/统计局导出的债务数据按 CSV 格式放进来，文件名用 ISO3 代码，例如 USA.csv、JPN.csv。\n"
                "程序会优先使用这里的数字，覆盖世界银行与内置接口；界面上会标记「本」。\n\n"
                "格式：date,debt_pct,debt_amount,source\n"
                "  debt_pct 与 debt_amount 至少填一个；只填金额时自动除以最新 GDP。\n\n"
                "示例（日本）：\n"
                "date,debt_pct,debt_amount,source\n"
                "2026-06-30,248.7,,日本财务省\n"
            )
    return count
