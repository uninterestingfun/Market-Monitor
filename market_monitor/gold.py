"""主要经济体央行黄金储备与买卖记录。

数据来源：
    世界银行 FI.RES.TOTL.CD（总储备）与 FI.RES.XGLD.CD（不含黄金储备）
    黄金价格：本地 COMEX 黄金日线（hf.GC）年均价，缺失时用内置年度均价兜底
吨数 = 黄金美元价值 / 当年均价 * 31.1034768 / 1e6（金衡盎司 -> 吨）
年度增减 = 当年吨数 - 上年吨数，正数为净买入（增持），负数为净卖出（减持）。
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional, Sequence

from .net import http

# GDP 前 50 经济体（ISO3）
COUNTRIES: List[tuple[str, str]] = [
    ("USA", "美国"), ("CHN", "中国"), ("DEU", "德国"), ("JPN", "日本"), ("IND", "印度"),
    ("GBR", "英国"), ("FRA", "法国"), ("ITA", "意大利"), ("BRA", "巴西"), ("CAN", "加拿大"),
    ("RUS", "俄罗斯"), ("MEX", "墨西哥"), ("AUS", "澳大利亚"), ("KOR", "韩国"), ("ESP", "西班牙"),
    ("IDN", "印尼"), ("TUR", "土耳其"), ("NLD", "荷兰"), ("SAU", "沙特"), ("CHE", "瑞士"),
    ("POL", "波兰"), ("BEL", "比利时"), ("ARG", "阿根廷"), ("SWE", "瑞典"), ("IRL", "爱尔兰"),
    ("AUT", "奥地利"), ("THA", "泰国"), ("ISR", "以色列"), ("NOR", "挪威"), ("SGP", "新加坡"),
    ("ARE", "阿联酋"), ("PHL", "菲律宾"), ("VNM", "越南"), ("MYS", "马来西亚"), ("BGD", "孟加拉"),
    ("DNK", "丹麦"), ("ZAF", "南非"), ("EGY", "埃及"), ("NGA", "尼日利亚"), ("IRN", "伊朗"),
    ("PAK", "巴基斯坦"), ("COL", "哥伦比亚"), ("CHL", "智利"), ("ROU", "罗马尼亚"), ("CZE", "捷克"),
    ("FIN", "芬兰"), ("PRT", "葡萄牙"), ("HKG", "中国香港"), ("PER", "秘鲁"), ("NZL", "新西兰"),
]

# 兜底年度均价（美元/盎司，伦敦金下午定盘年均值，仅在与本地行情对不上时使用）
FALLBACK_PRICE = {
    "2018": 1268.5, "2019": 1392.6, "2020": 1769.6, "2021": 1798.6,
    "2022": 1800.0, "2023": 1942.9, "2024": 2386.2, "2025": 2700.0,
}

OZ_TO_TONNE = 31.1034768 / 1e6
US_TONNES = 8133.5  # 美国官方黄金储备（吨），作为估值校准锚点

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


def _fetch_reserves(indicator: str) -> Dict[str, Dict[str, float]]:
    """返回 {ISO3: {year: 美元}}；失败重试 2 次。"""
    codes = ";".join(code for code, _ in COUNTRIES)
    url = f"https://api.worldbank.org/v2/country/{codes}/indicator/{indicator}"
    for attempt in range(2):
        try:
            data = http.get_json(
                url,
                params={"format": "json", "per_page": 2000, "date": "2017:2026"},
                timeout=40,
            )
            rows = data[1] if isinstance(data, list) and len(data) > 1 else []
            out: Dict[str, Dict[str, float]] = {}
            for r in rows or []:
                value = r.get("value")
                if value is None:
                    continue
                out.setdefault(r["countryiso3code"], {})[str(r["date"])] = float(value)
            if out:
                return out
        except Exception:
            time.sleep(1.0 * (attempt + 1))
    return {}


def _price_by_year() -> Dict[str, float]:
    cached = _get("prices", 21600)
    if cached is not None:
        return cached
    _put("prices", dict(FALLBACK_PRICE))
    return dict(FALLBACK_PRICE)


def set_prices(prices: Dict[str, float]) -> None:
    """由服务注入按年均价（来自本地 COMEX 黄金日线）。"""
    merged = dict(FALLBACK_PRICE)
    merged.update({k: v for k, v in prices.items() if v})
    _put("prices", merged)


def snapshot(prices: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    if prices:
        set_prices(prices)
    total = _get("snapshot", 21600)
    if total is not None:
        return total

    total_res = _fetch_reserves("FI.RES.TOTL.CD")
    ex_gold = _fetch_reserves("FI.RES.XGLD.CD")
    price = dict(_price_by_year())

    # 用美国的固定持仓（8,133.5 吨）反推各年估值金价，消除口径/时点差异
    us_tot = total_res.get("USA") or {}
    us_exg = ex_gold.get("USA") or {}
    for year, total_usd in us_tot.items():
        ex_usd = us_exg.get(year)
        if ex_usd is None:
            continue
        gold_usd = total_usd - ex_usd
        if gold_usd > 0:
            price[year] = gold_usd * OZ_TO_TONNE / US_TONNES

    rows: List[Dict[str, Any]] = []
    for code, name in COUNTRIES:
        tot = total_res.get(code) or {}
        exg = ex_gold.get(code) or {}
        series: List[Dict[str, Any]] = []
        for year in sorted(set(tot) & set(exg)):
            gold_usd = tot[year] - exg[year]
            if gold_usd <= 0:
                continue
            px = price.get(year) or 0
            if px <= 0:
                continue
            tonnes = gold_usd / px * OZ_TO_TONNE
            series.append({
                "year": year,
                "tonnes": round(tonnes, 1),
                "usd": round(gold_usd, 0),
                "share": round(gold_usd / tot[year] * 100, 1) if tot[year] else None,
            })
        if not series:
            continue
        latest = series[-1]
        prev = series[-2] if len(series) >= 2 else None
        change = round(latest["tonnes"] - prev["tonnes"], 1) if prev else None
        rows.append({
            "code": code,
            "name": name,
            "year": latest["year"],
            "tonnes": latest["tonnes"],
            "usd": latest["usd"],
            "share": latest["share"],
            "change": change,
            "change_pct": round(change / prev["tonnes"] * 100, 2) if prev and prev["tonnes"] else None,
            "action": ("增持" if change and change > 0.5 else "减持" if change and change < -0.5 else "持平")
            if change is not None else "—",
            "history": series[-6:],
        })

    rows.sort(key=lambda r: r["tonnes"], reverse=True)
    latest_year = max((r["year"] for r in rows), default=None)
    buying = [r for r in rows if r["change"] is not None and r["change"] > 0.5]
    selling = [r for r in rows if r["change"] is not None and r["change"] < -0.5]
    payload = {
        "rows": rows,
        "year": latest_year,
        "total_tonnes": round(sum(r["tonnes"] for r in rows), 1),
        "buying": len(buying),
        "selling": len(selling),
        "net_change": round(sum(r["change"] for r in rows if r["change"] is not None), 1),
        "buyers": [{"name": r["name"], "change": r["change"]} for r in sorted(buying, key=lambda x: -x["change"])[:5]],
        "sellers": [{"name": r["name"], "change": r["change"]} for r in sorted(selling, key=lambda x: x["change"])[:5]],
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": "世界银行总储备/不含黄金储备 + COMEX 金价换算",
    }
    _put("snapshot", payload)
    return payload


def warm(prices: Optional[Dict[str, float]] = None) -> None:
    try:
        snapshot(prices)
    except Exception:
        pass
