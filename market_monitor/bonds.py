"""债券市场：收益率曲线（中/美）+ 美债发行量（按品种、按月）。"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional

from .net import http

AUCTIONS = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/od/auctions_query"

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


def us_issuance(months: int = 12) -> Dict[str, Any]:
    """美债发行量：按拍卖月份 × 品种汇总（单位：十亿美元）。"""
    cached = _get("issuance", 21600)
    if cached is not None:
        return cached

    rows: List[Dict[str, Any]] = []
    for page in (1, 2, 3, 4):
        try:
            data = http.get_json(
                AUCTIONS,
                params={
                    "sort": "-auction_date",
                    "page[size]": 1000,
                    "page[number]": page,
                    "fields": "auction_date,security_type,security_term,offering_amt",
                },
                timeout=30,
            )
            batch = (data or {}).get("data") or []
        except Exception:
            break
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < 1000:
            break

    buckets: Dict[str, Dict[str, float]] = {}
    types: Dict[str, float] = {}
    for r in rows:
        day = str(r.get("auction_date") or "")
        if len(day) < 7:
            continue
        month = day[:7]
        kind = str(r.get("security_type") or "Other")
        try:
            amt = float(r.get("offering_amt") or 0)
        except (TypeError, ValueError):
            amt = 0.0
        if amt <= 0:
            continue
        buckets.setdefault(month, {}).setdefault(kind, 0.0)
        buckets[month][kind] += amt
        types[kind] = types.get(kind, 0.0) + amt

    ordered_months = sorted(buckets)[-months:]
    ordered_types = [t for t, _ in sorted(types.items(), key=lambda kv: -kv[1]) if t != "Other"]
    series = {t: [round(buckets[m].get(t, 0.0) / 1e9, 2) for m in ordered_months] for t in ordered_types}
    payload = {
        "months": ordered_months,
        "types": ordered_types,
        "series": series,
        "total_by_type": {t: round(types[t] / 1e9, 2) for t in ordered_types},
        "unit": "十亿美元",
        "source": "美国财政部 拍卖数据（auctions_query）",
    }
    _put("issuance", payload)
    return payload


def yield_curve(quotes: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """用现有资产的中/美债收益率拼曲线（quotes 来自 service）。"""
    terms = [("2Y", "2Y"), ("5Y", "5Y"), ("10Y", "10Y"), ("30Y", "30Y")]
    curves: Dict[str, List[Dict[str, Any]]] = {}
    for market, prefix in (("CN", "bond.CN"), ("US", "bond.US")):
        points = []
        for term, label in terms:
            q = quotes.get(f"{prefix}{term}") or {}
            if q.get("price") is not None:
                points.append({"term": label, "value": round(float(q["price"]), 4)})
        if points:
            curves[market] = points
    return curves


def snapshot(quotes: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "curves": yield_curve(quotes),
        "issuance": us_issuance(),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
