"""各国官方统计源的债务数据。

世界银行的债务数据有 1~2 年滞后，这里补充各国官方发布的实时数据。
已接入：
    USA  美国财政部 FiscalData（总公共债务，美元）
    BRA  巴西央行 SGS 13762（一般政府总债务 / GDP，%）
其余国家暂时回落到世界银行，并在界面上标明来源。
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional

from .net import http

_LOCK = threading.Lock()
_CACHE: Dict[str, tuple] = {}


def _get(key: str, ttl: float) -> Optional[Any]:
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and time.time() - hit[1] < ttl:
            return hit[0]
    return None


def _put(key: str, value: Any) -> None:
    with _LOCK:
        _CACHE[key] = (value, time.time())


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


# 国家 -> 抓取函数
ADAPTERS = {
    "USA": _us_debt,
    "BRA": _br_debt,
}


def snapshot(gdp: Dict[str, Optional[float]]) -> Dict[str, Dict[str, Any]]:
    """抓取可用的官方债务数据；gdp 用于把绝对金额换算成占 GDP 比例。"""
    cached = _get("snapshot", 21600)
    if cached is not None:
        return cached
    out: Dict[str, Dict[str, Any]] = {}
    for code, fn in ADAPTERS.items():
        try:
            item = fn()
        except Exception:
            item = None
        if not item:
            continue
        if item.get("pct") is None and item.get("amount") and gdp.get(code):
            item["pct"] = item["amount"] / gdp[code] * 100.0
        out[code] = item
    _put("snapshot", out)
    return out


def warm(gdp: Dict[str, Optional[float]]) -> None:
    try:
        snapshot(gdp)
    except Exception:
        pass
