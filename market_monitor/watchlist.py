"""自选板块：用户自选股票 / ETF / 可转债等，实时取分时与历史，估算资金流与成交量。"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from . import analysis, providers
from .net import http

_LOCK = threading.Lock()
_CACHE: Dict[str, Tuple[Any, float]] = {}
_DATA_DIR: Optional[str] = None
FILE = "watchlist.json"

DEFAULT_ITEMS = [
    {"code": "sh600519", "type": "stock"},
    {"code": "sh510300", "type": "etf"},
    {"code": "sz159915", "type": "etf"},
]


def set_data_dir(path: str) -> None:
    global _DATA_DIR
    _DATA_DIR = path


def _path() -> Optional[str]:
    return os.path.join(_DATA_DIR, FILE) if _DATA_DIR else None


def _get(key: str, ttl: float) -> Optional[Any]:
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and time.time() - hit[1] < ttl:
            return hit[0]
    return None


def _put(key: str, value: Any) -> None:
    with _LOCK:
        _CACHE[key] = (value, time.time())



_QT = "https://qt.gtimg.cn/q="
_MINUTE = "https://web.ifzq.gtimg.cn/appstock/app/minute/query"
_QT_HEADERS = {"Referer": "https://gu.qq.com/", "User-Agent": "Mozilla/5.0"}


def _f(value: Any) -> Optional[float]:
    try:
        if value in (None, "", "-"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def fetch_quotes(codes: List[str]) -> Dict[str, Dict[str, Any]]:
    """批量取报价（股票 / ETF / 指数 / 可转债通用）。"""
    out: Dict[str, Dict[str, Any]] = {}
    for i in range(0, len(codes), 50):
        batch = codes[i:i + 50]
        try:
            text = http.get_text(_QT + ",".join(batch), headers=_QT_HEADERS, timeout=15, encoding="gbk")
        except Exception:
            continue
        for line in (text or "").split(";"):
            if "~" not in line:
                continue
            head, _, body = line.partition("=")
            key = head.replace("v_", "").strip()
            f = body.strip().strip('"').split("~")
            if len(f) < 40:
                continue
            price = _f(f[3])
            if price is None:
                continue
            out[key] = {
                "code": key,
                "name": f[1],
                "price": price,
                "prev_close": _f(f[4]),
                "open": _f(f[5]),
                "pct": _f(f[32]),
                "high": _f(f[33]),
                "low": _f(f[34]),
                "volume": _f(f[36]),          # 手
                "amount": (_f(f[37]) or 0) * 1e4,  # 万元 → 元
                "time": f[30] if len(f) > 30 else "",
            }
    return out


def fetch_minute(code: str) -> List[List[Any]]:
    """分钟分时：[时间, 价格, 成交量(手), 成交额(元)]。"""
    try:
        data = http.get_json(_MINUTE, params={"code": code}, headers=_QT_HEADERS, timeout=15) or {}
        node = ((data.get("data") or {}).get(code) or {}).get("data") or {}
        rows = node.get("data") or []
    except Exception:
        return []
    out: List[List[Any]] = []
    for row in rows:
        parts = str(row).split()
        if len(parts) < 3:
            continue
        out.append([parts[0], _f(parts[1]), _f(parts[2]), _f(parts[3]) if len(parts) > 3 else None])
    return out


def fetch_daily(code: str, count: int = 120) -> List[List[Any]]:
    try:
        return providers._tencent_kline(code, "day", count) or []
    except Exception:
        return []


def normalize(raw: str) -> Optional[Dict[str, str]]:
    """把用户输入规范成 {code, type}。支持 600519 / sh600519 / 510300 / 159915 / 10004567。"""
    text = (raw or "").strip().lower().replace(" ", "")
    if not text:
        return None
    prefix = ""
    if text[:2] in ("sh", "sz", "bj"):
        prefix, text = text[:2], text[2:]
    if not text.isdigit() or len(text) < 6:
        return None
    if len(text) == 8 and text.startswith(("1000", "9000")):
        return {"code": "sh" + text, "type": "option"}
    if text.startswith(("11", "12")) and len(text) == 6:
        return {"code": ("sh" if text.startswith("11") else "sz") + text, "type": "bond"}
    if text.startswith(("5", "6")) or text.startswith("9"):
        return {"code": "sh" + text, "type": "etf" if text.startswith("5") else "stock"}
    if text.startswith(("0", "1", "2", "3")):
        return {"code": "sz" + text, "type": "etf" if text.startswith(("15", "16")) else "stock"}
    return {"code": (prefix or "sh") + text, "type": "stock"}


def load() -> List[Dict[str, str]]:
    path = _path()
    if not path:
        return list(DEFAULT_ITEMS)
    if not os.path.exists(path):
        save(list(DEFAULT_ITEMS))
        return list(DEFAULT_ITEMS)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        items = data.get("items") if isinstance(data, dict) else data
        return [i for i in (items or []) if i.get("code")]
    except Exception:
        return list(DEFAULT_ITEMS)


def save(items: List[Dict[str, str]]) -> None:
    path = _path()
    if not path:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"items": items}, fh, ensure_ascii=False, indent=2)


def add(raw: str) -> Dict[str, Any]:
    item = normalize(raw)
    if not item:
        return {"ok": False, "error": "无法识别该代码，请输入 6 位股票/ETF 代码或 8 位期权代码"}
    items = load()
    if any(i["code"] == item["code"] for i in items):
        return {"ok": False, "error": "已在自选列表中"}
    quote = fetch_quotes([item["code"]]).get(item["code"]) or {}
    if not quote.get("price"):
        hint = "（期权代码可能未被数据源收录）" if item["type"] == "option" else ""
        return {"ok": False, "error": f"未获取到该代码的行情{hint}"}
    item["name"] = quote.get("name") or item["code"]
    items.append(item)
    save(items)
    return {"ok": True, "item": item, "items": items}


def remove(code: str) -> Dict[str, Any]:
    items = [i for i in load() if i["code"] != code]
    save(items)
    return {"ok": True, "items": items}


def _intraday_flow(points: List[List[Any]]) -> Dict[str, Any]:
    """日内资金流估算（tick rule）。

    腾讯分时返回的是「累计成交量 / 累计成交额」，这里取相邻差值得到每分钟增量，
    再按价格涨跌把这一分钟的成交额归入流入或流出。
    """
    inflow = outflow = 0.0
    last_price = None
    prev_amount = 0.0
    cum = 0.0
    series: List[List[Any]] = []
    for p in points:
        if len(p) < 3 or p[1] is None:
            continue
        t, price = str(p[0]), float(p[1])
        amount = float(p[3]) if len(p) > 3 and p[3] is not None else None
        if amount is not None:
            delta_amt = max(0.0, amount - prev_amount)
            prev_amount = amount
        else:
            vol = float(p[2] or 0)
            delta_amt = price * vol * 100  # 手 → 股（无累计额时的兜底）
        if last_price is not None and price > last_price:
            inflow += delta_amt
            cum += delta_amt
        elif last_price is not None and price < last_price:
            outflow += delta_amt
            cum -= delta_amt
        last_price = price
        series.append([t, round(cum / 1e8, 4)])
    total = inflow + outflow
    net = inflow - outflow
    return {
        "inflow": inflow,
        "outflow": outflow,
        "net": net,
        "net_ratio": (net / total * 100) if total else 0.0,
        "series": series[::3],
    }


def _daily_flow(bars: List[List[Any]], days: int = 20) -> Dict[str, Any]:
    rows = analysis.money_flow(bars[-days:])
    net = sum(r[3] for r in rows) if rows else 0.0
    avg = net / len(rows) if rows else 0.0
    return {
        "rows": [[r[0], r[3]] for r in rows],
        "net_sum": net,
        "net_avg": avg,
        "days": len(rows),
        "inflow_avg": (sum(r[1] for r in rows) / len(rows)) if rows else 0.0,
        "outflow_avg": (sum(r[2] for r in rows) / len(rows)) if rows else 0.0,
    }



def _stock_valuation(code: str, quote: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """个股估值（ETF / 指数返回 None）。"""
    if not code.startswith(("sh6", "sz0", "sz3")):
        return None
    try:
        from . import valuation

        price = quote.get("price")
        rows = valuation.financials(code)
        cap = None
        if rows and price:
            eps = valuation._ttm(rows, "EPSJB")
            np_ttm = valuation._ttm(rows, "PARENTNETPROFIT")
            if eps and np_ttm:
                cap = price * (np_ttm / eps)
        return valuation.stock(code, price=price, cap=cap)
    except Exception:
        return None


def detail(code: str) -> Dict[str, Any]:
    """单个自选标的：报价 + 分时 + 日内资金流 + 近 20 日资金流 + 量能。"""
    cached = _get(f"detail|{code}", 20)
    if cached is not None:
        return cached
    item = normalize(code)
    if not item:
        return {"ok": False, "error": "代码格式不正确"}
    code = item["code"]

    quote = fetch_quotes([code]).get(code) or {}
    if not quote.get("price"):
        hint = "期权代码暂未被数据源收录，暂无法取分时与历史" if item["type"] == "option" else "数据源未返回该代码的行情"
        return {"ok": False, "error": hint, "code": code, "type": item["type"]}

    points = fetch_minute(code)
    bars = fetch_daily(code, 120)


    flow_intraday = _intraday_flow(points) if points else None
    flow_daily = _daily_flow(bars, 20) if bars else None

    volume = quote.get("volume") or 0
    avg5 = None
    if len(bars) >= 6:
        vols = [b[5] for b in bars[-6:-1] if len(b) > 5 and b[5]]
        avg5 = (sum(vols) / len(vols)) if vols else None
    volume_ratio = (volume / avg5) if (avg5 and volume) else None

    payload = {
        "ok": True,
        "code": code,
        "type": item["type"],
        "name": quote.get("name") or code,
        "quote": {
            "price": quote.get("price"),
            "pct": quote.get("pct"),
            "open": quote.get("open"),
            "high": quote.get("high"),
            "low": quote.get("low"),
            "prev_close": quote.get("prev_close"),
            "volume": volume,
            "amount": quote.get("amount"),
            "time": quote.get("time"),
        },
        "intraday": [[p[0], p[1]] for p in points],
        "flow_intraday": flow_intraday,
        "flow_daily": flow_daily,
        "volume_ratio": round(volume_ratio, 2) if volume_ratio else None,
        "bars": len(bars),
        "valuation": _stock_valuation(code, quote),
        "updated_at": time.strftime("%H:%M:%S"),
    }
    _put(f"detail|{code}", payload)
    return payload


def overview() -> Dict[str, Any]:
    """自选列表 + 实时报价 + 资金流摘要。"""
    items = load()
    codes = [i["code"] for i in items]
    quotes = fetch_quotes(codes) if codes else {}
    rows: List[Dict[str, Any]] = []
    for item in items:
        code = item["code"]
        q = quotes.get(code) or {}
        flow = _get(f"flow|{code}", 300)
        if flow is None:
            bars = fetch_daily(code, 40)
            flow = _daily_flow(bars, 20) if bars else None
            _put(f"flow|{code}", flow)
        rows.append({
            "code": code,
            "name": q.get("name") or item.get("name") or code,
            "type": item.get("type") or "stock",
            "price": q.get("price"),
            "pct": q.get("pct"),
            "volume": q.get("volume"),
            "amount": q.get("amount"),
            "flow_net": (flow or {}).get("net_sum"),
            "flow_avg": (flow or {}).get("net_avg"),
            "missing": not bool(q.get("price")),
        })
    return {
        "ok": True,
        "items": rows,
        "count": len(rows),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
