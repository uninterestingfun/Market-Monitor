"""黄金现货行情：伦敦金、纽约金、上海金（SGE Au99.99 / T+D）。

数据来源：新浪财经 hq.sinajs.cn（GBK）。
内外价差 = (上海金 - 伦敦金折人民币) / 伦敦金折人民币。
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional

from .net import http

SINA_URL = "https://hq.sinajs.cn/list="
HEADERS = {"Referer": "https://finance.sina.com.cn"}
OZ_IN_GRAM = 31.1034768

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


def _f(value: Any) -> Optional[float]:
    try:
        text = str(value).strip().replace(",", "")
        if not text or text in ("-", "--"):
            return None
        return float(text)
    except (TypeError, ValueError):
        return None


def _parse(text: str) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for line in text.splitlines():
        if "hq_str_" not in line or '="' not in line:
            continue
        code = line.split("hq_str_", 1)[1].split("=", 1)[0]
        body = line.split('="', 1)[1].rstrip('";')
        out[code] = body.split(",")
    return out


# 代码 -> (显示名, 市场, 单位, 小数位, 价格索引, 最高索引, 最低索引, 昨收索引, 时间索引)
SPECS = {
    "hf_XAU": ("伦敦金", "伦敦 LBMA", "美元/盎司", 2, 0, 4, 5, 7, 6),
    "hf_GC": ("纽约金", "纽约 COMEX", "美元/盎司", 2, 0, 4, 5, 7, 6),
    "gds_AU9999": ("上海金 Au99.99", "上海 SGE", "元/克", 2, 0, 4, 5, 7, 6),
    "gds_AUTD": ("上海金 T+D", "上海 SGE", "元/克", 2, 0, 4, 5, 7, 6),
}


def snapshot() -> Dict[str, Any]:
    cached = _get("spot", 5)
    if cached is not None:
        return cached

    codes = list(SPECS) + ["fx_susdcny"]
    text = ""
    for attempt in range(3):
        try:
            text = http.get_text(SINA_URL + ",".join(codes), headers=HEADERS,
                                 timeout=10, encoding="gbk")
            if text:
                break
        except Exception:
            time.sleep(0.4 * (attempt + 1))
    rows = _parse(text)

    items: List[Dict[str, Any]] = []
    for code, (name, market, unit, digits, ip, ih, il, iprev, it) in SPECS.items():
        fields = rows.get(code) or []
        if len(fields) <= max(ip, iprev):
            continue
        price = _f(fields[ip])
        prev = _f(fields[iprev])
        if price is None:
            continue
        change = price - prev if prev else None
        pct = change / prev * 100 if change is not None and prev else None
        items.append({
            "code": code,
            "name": name,
            "market": market,
            "unit": unit,
            "digits": digits,
            "price": price,
            "prev": prev,
            "high": _f(fields[ih]),
            "low": _f(fields[il]),
            "change": round(change, digits) if change is not None else None,
            "pct": round(pct, 2) if pct is not None else None,
            "time": fields[it] if len(fields) > it else "",
        })

    # 内外价差：上海金 vs 伦敦金折人民币/克
    usdcny_fields = rows.get("fx_susdcny") or []
    usdcny = None
    for idx in (1, 2, 3):
        if len(usdcny_fields) > idx:
            usdcny = _f(usdcny_fields[idx])
            if usdcny and usdcny > 1:
                break
    xau = next((i for i in items if i["code"] == "hf_XAU"), None)
    sge = next((i for i in items if i["code"] == "gds_AU9999"), None)
    premium = None
    london_cny = None
    if xau and sge and usdcny:
        london_cny = xau["price"] * usdcny / OZ_IN_GRAM
        if london_cny:
            premium = round((sge["price"] - london_cny) / london_cny * 100, 2)

    payload = {
        "items": items,
        "usdcny": usdcny,
        "london_cny_per_gram": round(london_cny, 2) if london_cny else None,
        "premium_pct": premium,
        "updated_at": time.strftime("%H:%M:%S"),
        "source": "新浪财经 hq.sinajs.cn",
    }
    _put("spot", payload)
    return payload
