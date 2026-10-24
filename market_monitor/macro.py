"""全球主要经济体宏观数据。

数据源：世界银行公开 API（GDP / GNI / 人均 GDP / 失业率 / 政府债务 / 广义货币 / CPI / 人口）。
政策利率没有稳定免费接口，使用本文件内维护的参考值（见 POLICY_RATES / POLICY_AS_OF）。
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from .net import http

# 前 20 大经济体（按名义 GDP），ISO3 代码
COUNTRIES: List[tuple[str, str]] = [
    ("USA", "美国"), ("CHN", "中国"), ("DEU", "德国"), ("JPN", "日本"), ("IND", "印度"),
    ("GBR", "英国"), ("FRA", "法国"), ("ITA", "意大利"), ("BRA", "巴西"), ("CAN", "加拿大"),
    ("RUS", "俄罗斯"), ("MEX", "墨西哥"), ("AUS", "澳大利亚"), ("KOR", "韩国"), ("ESP", "西班牙"),
    ("IDN", "印尼"), ("TUR", "土耳其"), ("NLD", "荷兰"), ("SAU", "沙特"), ("CHE", "瑞士"),
]

INDICATORS: List[tuple[str, str, str, str]] = [
    ("gdp", "GDP", "NY.GDP.MKTP.CD", "usd"),
    ("gni", "GNI", "NY.GNP.MKTP.CD", "usd"),
    ("gdp_pc", "人均 GDP", "NY.GDP.PCAP.CD", "usd"),
    ("unemployment", "失业率", "SL.UEM.TOTL.ZS", "pct"),
    ("debt", "政府债务/GDP", "GC.DOD.TOTL.GD.ZS", "pct"),
    ("m2", "广义货币/GDP", "FM.LBL.BMNY.GD.ZS", "pct"),
    ("cpi", "CPI 同比", "FP.CPI.TOTL.ZG", "pct"),
    ("population", "人口", "SP.POP.TOTL", "num"),
]

# 政策利率参考值（%，手动维护；格式：央行主要政策利率）
POLICY_AS_OF = "2025-10"
POLICY_RATES: Dict[str, float] = {
    "USA": 4.00, "CHN": 3.00, "DEU": 2.15, "JPN": 0.50, "IND": 5.50,
    "GBR": 4.00, "FRA": 2.15, "ITA": 2.15, "BRA": 15.00, "CAN": 2.25,
    "RUS": 17.00, "MEX": 7.50, "AUS": 3.35, "KOR": 2.50, "ESP": 2.15,
    "IDN": 4.75, "TUR": 39.50, "NLD": 2.15, "SAU": 4.25, "CHE": 0.00,
}

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


def _fetch_indicator(indicator: str, years: str = "2017:2026") -> Dict[str, List[tuple[str, float]]]:
    """返回 {ISO3: [(year, value), ...]}，按年份升序。单指标最多重试 2 次。"""
    codes = ";".join(code for code, _ in COUNTRIES)
    url = f"https://api.worldbank.org/v2/country/{codes}/indicator/{indicator}"
    data = None
    for attempt in range(2):
        try:
            data = http.get_json(
                url,
                params={"format": "json", "per_page": 500, "date": years},
                timeout=16,
            )
            break
        except Exception:
            time.sleep(0.6 * (attempt + 1))
    if data is None:
        return {}
    rows = data[1] if isinstance(data, list) and len(data) > 1 else []
    out: Dict[str, List[tuple[str, float]]] = {}
    for r in rows or []:
        value = r.get("value")
        if value is None:
            continue
        out.setdefault(r.get("countryiso3code") or "", []).append((str(r.get("date")), float(value)))
    for series in out.values():
        series.sort()
    return out


def _latest(series: List[tuple[str, float]]) -> tuple[Optional[float], Optional[str]]:
    if not series:
        return None, None
    year, value = series[-1]
    return value, year


def snapshot(force: bool = False) -> Dict[str, Any]:
    """20 个经济体的最新宏观指标。缓存 12 小时，force=True 强制刷新。"""
    if not force:
        cached = _get("snapshot", 43200)
        if cached is not None:
            return cached

    data: Dict[str, Dict[str, List[tuple[str, float]]]] = {}
    with ThreadPoolExecutor(max_workers=len(INDICATORS)) as pool:
        futures = {key: pool.submit(_fetch_indicator, ind) for key, _l, ind, _u in INDICATORS}
        for key, fut in futures.items():
            try:
                data[key] = fut.result(timeout=60)
            except Exception:
                data[key] = {}

    rows: List[Dict[str, Any]] = []
    for code, name in COUNTRIES:
        row: Dict[str, Any] = {
            "code": code,
            "name": name,
            "rate": POLICY_RATES.get(code),
            "years": {},
        }
        for key, _label, _indicator, _unit in INDICATORS:
            series = data.get(key, {}).get(code, [])
            value, year = _latest(series)
            row[key] = value
            row["years"][key] = year
        # GDP 同比
        gdp_series = data.get("gdp", {}).get(code, [])
        row["gdp_yoy"] = None
        if len(gdp_series) >= 2:
            prev = gdp_series[-2][1]
            cur = gdp_series[-1][1]
            if prev:
                row["gdp_yoy"] = (cur / prev - 1) * 100
        rows.append(row)

    rows.sort(key=lambda r: (r.get("gdp") is None, -(r.get("gdp") or 0)))
    total = sum((r.get("gdp") or 0) for r in rows)
    for r in rows:
        r["share"] = (r["gdp"] / total * 100) if total and r.get("gdp") else None

    payload = {
        "rows": rows,
        "indicators": [{"key": k, "label": l, "unit": u} for k, l, _i, u in INDICATORS],
        "policy_as_of": POLICY_AS_OF,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "missing": [k for k, _l, _i, _u in INDICATORS if not data.get(k)],
        "source": "世界银行 World Bank（年度数据）；政策利率为手动维护参考值",
    }
    _put("snapshot", payload)
    return payload


def warm() -> None:
    """后台预热宏观缓存，避免用户首次打开宏观页等待。"""
    try:
        snapshot()
    except Exception:
        pass
