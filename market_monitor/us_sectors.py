"""美股行业板块：数据来自纳斯达克官方 screener（含 sector / industry / 市值 / 涨跌幅）。"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional

from .net import http

SCREENER = "https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=25&offset=0&download=true"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Accept": "application/json",
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


def _num(text: Any) -> float:
    if text is None:
        return 0.0
    s = str(text).replace("$", "").replace(",", "").replace("%", "").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


def _stocks() -> List[Dict[str, Any]]:
    cached = _get("stocks", 600)
    if cached is not None:
        return cached
    rows: List[Dict[str, Any]] = []
    for attempt in range(3):
        try:
            data = http.get_json(SCREENER, headers=HEADERS, timeout=40)
            rows = (data.get("data") or {}).get("rows") or []
            if rows:
                break
        except Exception:
            time.sleep(1.5 * (attempt + 1))
    out = []
    for r in rows:
        cap = _num(r.get("marketCap"))
        if cap <= 0:
            continue
        out.append({
            "code": (r.get("symbol") or "").upper(),
            "name": r.get("name") or "",
            "pct": _num(r.get("pctchange")),
            "mktcap": cap,
            "sector": r.get("sector") or "Other",
            "industry": r.get("industry") or "",
        })
    _put("stocks", out)
    return out


def industries() -> List[Dict[str, Any]]:
    """按 sector 聚合：股票数、合计市值、市值加权涨跌幅。"""
    stocks = _stocks()
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for s in stocks:
        buckets.setdefault(s["sector"], []).append(s)
    items = []
    for name, members in buckets.items():
        cap = sum(m["mktcap"] for m in members)
        pct = sum(m["pct"] * m["mktcap"] for m in members) / cap if cap else 0.0
        items.append({
            "node": name,
            "name": name,
            "count": len(members),
            "pct": round(pct, 2),
            "cap": cap,
        })
    items.sort(key=lambda x: x["cap"], reverse=True)
    return items


def board(node: str, limit: int = 50) -> Dict[str, Any]:
    stocks = [s for s in _stocks() if s["sector"] == node]
    stocks.sort(key=lambda s: s["mktcap"], reverse=True)
    members = [{
        "code": s["code"],
        "name": s["name"],
        "pct": s["pct"],
        "mktcap": s["mktcap"],
    } for s in stocks[:limit]]
    total_cap = sum(s["mktcap"] for s in stocks)
    pct = (sum(s["pct"] * s["mktcap"] for s in stocks) / total_cap) if total_cap else 0.0
    return {
        "node": node,
        "name": node,
        "count": len(stocks),
        "pct": round(pct, 2),
        "members": members,
        "etf": None,
        "total_cap": round(total_cap, 2),
    }


def warm() -> None:
    try:
        _stocks()
    except Exception:
        pass
