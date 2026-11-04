"""官方机构 · 汇金相关 ETF 申购追踪。

说明：
    交易所不公布「谁申购了 ETF」，但每日公布各 ETF 的总份额。
    汇金买入宽基 ETF 会直接体现为份额增加，因此这里追踪宽基 ETF 的：
        · 每日总份额（上交所每日公布，深交所接口 403，故只覆盖沪市）
        · 份额日变化与估算净申购金额
        · 基金季报披露的期间申购 / 赎回（季度口径，含历史）
"""
from __future__ import annotations

import csv
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from .net import http

SSE_API = "http://query.sse.com.cn/commonQuery.do"
SSE_SQL = "COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L"
SSE_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
               "Referer": "http://www.sse.com.cn/"}

EM_API = "https://fundf10.eastmoney.com/FundArchivesDatas.aspx"
EM_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://fund.eastmoney.com/"}

# 汇金历次增持以宽基 ETF 为主，按「代码 + 名称简称」双匹配
# 上交所公布的是简称（300ETF / 50ETF / 500ETF / 科创50…），单靠名称不可靠
CODE_WATCH: Dict[str, str] = {
    "510300": "沪深300ETF·华泰柏瑞",
    "510310": "沪深300ETF·易方达",
    "510330": "沪深300ETF·华夏",
    "510350": "沪深300ETF·工银",
    "510360": "沪深300ETF·广发",
    "510050": "上证50ETF",
    "510500": "中证500ETF·南方",
    "510510": "中证500ETF·广发",
    "512100": "中证1000ETF·南方",
    "560010": "中证1000ETF·华夏",
    "560510": "中证A500ETF",
    "588000": "科创50ETF·华夏",
    "588080": "科创50ETF·易方达",
    "588090": "科创50ETF·工银",
}
BROAD_KEYWORDS = ("沪深300", "上证50", "中证500", "中证1000", "科创50", "上证180", "中证A500", "中证100")

CSV_FILE = "etf_shares.csv"

_LOCK = threading.Lock()
_CACHE: Dict[str, Tuple[Any, float]] = {}
_DATA_DIR: Optional[str] = None


def set_data_dir(path: str) -> None:
    global _DATA_DIR
    _DATA_DIR = path


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
        if value in (None, "", "-"):
            return None
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def sse_snapshot() -> Dict[str, Any]:
    """上交所 ETF 每日总份额（万份）。"""
    cached = _get("sse", 1800)
    if cached is not None:
        return cached
    params = {"sqlId": SSE_SQL, "pageHelp.pageSize": 2000, "pageHelp.pageNo": 1}
    data = http.get_json(SSE_API, params=params, headers=SSE_HEADERS, timeout=30) or {}
    rows = data.get("result") or []
    out: List[Dict[str, Any]] = []
    date = ""
    for r in rows:
        code = str(r.get("SEC_CODE") or "").strip()
        name = str(r.get("SEC_NAME") or "").strip()
        vol = _f(r.get("TOT_VOL"))
        if not code or vol is None:
            continue
        date = date or str(r.get("STAT_DATE") or "")[:10]
        out.append({"code": code, "name": name, "shares_wan": vol,
                    "type": str(r.get("ETF_TYPE") or ""), "date": str(r.get("STAT_DATE") or "")[:10]})
    payload = {"date": date, "rows": out}
    if out:
        _put("sse", payload)
    return payload


def save_snapshot(snap: Dict[str, Any]) -> Optional[str]:
    """把当日快照追加到本地 CSV（同一天只写一次）。"""
    if not _DATA_DIR or not snap.get("rows"):
        return None
    os.makedirs(_DATA_DIR, exist_ok=True)
    path = os.path.join(_DATA_DIR, CSV_FILE)
    date = snap.get("date")
    existing = set()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8-sig", newline="") as fh:
                for row in csv.DictReader(fh):
                    if row.get("date") == date:
                        existing.add(row.get("code"))
        except Exception:
            pass
    new_rows = [r for r in snap["rows"] if r["code"] not in existing]
    if not new_rows:
        return path
    write_header = not os.path.exists(path)
    with open(path, "a", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        if write_header:
            writer.writerow(["date", "code", "name", "type", "shares_wan"])
        for r in new_rows:
            writer.writerow([r["date"], r["code"], r["name"], r["type"], r["shares_wan"]])
    return path


def load_history() -> Dict[str, List[Tuple[str, float]]]:
    """{code: [(date, shares_wan), ...]} 升序。"""
    if not _DATA_DIR:
        return {}
    path = os.path.join(_DATA_DIR, CSV_FILE)
    if not os.path.exists(path):
        return {}
    out: Dict[str, List[Tuple[str, float]]] = {}
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                code = row.get("code") or ""
                date = row.get("date") or ""
                vol = _f(row.get("shares_wan"))
                if not code or not date or vol is None:
                    continue
                out.setdefault(code, []).append((date, vol))
    except Exception:
        return {}
    for series in out.values():
        series.sort()
    return out


def quarterly(code: str) -> List[Dict[str, Any]]:
    """基金季报的期间申购 / 赎回（亿份）。"""
    cached = _get(f"q|{code}", 12 * 3600)
    if cached is not None:
        return cached
    out: List[Dict[str, Any]] = []
    try:
        text = http.get_text(EM_API, params={"type": "gmbd", "code": code},
                             headers=EM_HEADERS, timeout=25, min_interval=0.3)
        for row in re.findall(r"<tr>(.*?)</tr>", text or "", re.S):
            cells = [re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
            if len(cells) < 5 or not re.match(r"\d{4}-\d{2}-\d{2}", cells[0]):
                continue
            out.append({
                "date": cells[0],
                "subscribe": _f(cells[1]),
                "redeem": _f(cells[2]),
                "total": _f(cells[3]),
                "nav_total": _f(cells[4]),
                "change": cells[5] if len(cells) > 5 else "",
            })
    except Exception:
        out = []
    _put(f"q|{code}", out)
    return out


def build(quotes: Optional[Dict[str, Dict[str, Any]]] = None, quarterly_limit: int = 6) -> Dict[str, Any]:
    """汇金相关宽基 ETF 的份额与申购追踪。"""
    cached = _get("build", 600)
    if cached is not None:
        return cached

    snap = sse_snapshot()
    save_snapshot(snap)
    history = load_history()

    rows: List[Dict[str, Any]] = []
    for item in snap.get("rows", []):
        name = item["name"]
        code = item["code"]
        if code not in CODE_WATCH:
            continue
        series = history.get(code) or [(item["date"], item["shares_wan"])]
        latest = series[-1][1]
        prev = series[-2][1] if len(series) > 1 else None
        delta = (latest - prev) if prev is not None else None
        price = None
        if quotes:
            q = quotes.get("sh" + code) or {}
            price = q.get("price")
        amount = None
        if delta is not None and price:
            amount = delta * 1e4 * float(price)  # 份额单位：万份
        rows.append({
            "code": code,
            "name": CODE_WATCH.get(code, name),
            "date": item["date"],
            "shares": latest * 1e4,              # 份
            "prev_shares": prev * 1e4 if prev else None,
            "delta": delta * 1e4 if delta is not None else None,
            "price": price,
            "amount": amount,
            "history_days": len(series),
        })
    rows.sort(key=lambda r: r["shares"], reverse=True)

    # 季度申购赎回（只取前几只，避免请求过多）
    for row in rows[:quarterly_limit]:
        q = quarterly(row["code"])
        row["quarterly"] = q[:4]
    for row in rows[quarterly_limit:]:
        row["quarterly"] = []

    result = {
        "ok": bool(rows),
        "date": snap.get("date") or "",
        "rows": rows[:20],
        "total_etf": len(snap.get("rows") or []),
        "history_days": max((r["history_days"] for r in rows), default=1),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": "上交所每日 ETF 份额 + 基金季报申购赎回（东方财富）",
    }
    if rows:
        _put("build", result)
    return result
