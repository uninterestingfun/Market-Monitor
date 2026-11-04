"""官方机构 · 中央汇金持仓与操作记录。

数据来源：
    上交所 / 深交所定期报告披露的「前十大流通股东」，经东方财富数据中心汇总
    （上交所 query.sse.com.cn 可直接访问；深交所接口返回 403，故走汇总口径）

口径说明：
    只能看到进入前十大流通股东的持仓，未进前十的仓位不可见；
    按季度披露，最新一期通常滞后 1~2 个月。
"""
from __future__ import annotations

import csv
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from .net import http

API = "https://datacenter-web.eastmoney.com/api/data/v1/get"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Referer": "https://data.eastmoney.com/",
}

HOLDERS: List[Tuple[str, str]] = [
    ("中央汇金投资有限责任公司", "汇金投资"),
    ("中央汇金资产管理有限责任公司", "汇金资管"),
]

QUARTER_SUFFIX = ("03-31", "06-30", "09-30", "12-31")
CSV_NAME = "records.csv"

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


def _num(value: Any) -> Optional[float]:
    try:
        if value in (None, "", "-"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _quarter_end(value: Any) -> str:
    text = str(value or "")[:10]
    return text if text[5:] in QUARTER_SUFFIX else ""


def _fetch_holder(holder: str, since: str) -> List[Dict[str, Any]]:
    """拉取某个主体的股东记录（分页）。"""
    rows: List[Dict[str, Any]] = []
    page = 1
    while page <= 12:
        params = {
            "reportName": "RPT_F10_EH_FREEHOLDERS",
            "columns": "ALL",
            "pageSize": 500,
            "pageNumber": page,
            "sortColumns": "END_DATE,SECURITY_CODE",
            "sortTypes": "-1,1",
            "filter": f"(HOLDER_NAME=\"{holder}\")(END_DATE>='{since}')",
        }
        try:
            data = http.get_json(API, params=params, headers=HEADERS, timeout=40)
        except Exception:
            break
        result = (data or {}).get("result") or {}
        batch = result.get("data") or []
        if not batch:
            break
        rows.extend(batch)
        pages = int(result.get("pages") or 1)
        if page >= pages:
            break
        page += 1
    return rows


def _load_csv() -> List[Dict[str, Any]]:
    """本地覆盖：data/huijin/records.csv（列：date,code,name,holder,action,shares,ratio,note）。"""
    if not _DATA_DIR:
        return []
    path = os.path.join(_DATA_DIR, CSV_NAME)
    if not os.path.exists(path):
        return []
    out: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            lines = [ln for ln in fh if ln.strip() and not ln.lstrip().startswith("#")]
        for raw in csv.DictReader(lines):
            out.append({
                "date": (raw.get("date") or "").strip(),
                "code": (raw.get("code") or "").strip(),
                "name": (raw.get("name") or "").strip(),
                "holder": (raw.get("holder") or "").strip(),
                "action": (raw.get("action") or "").strip(),
                "shares": _num(raw.get("shares")),
                "ratio": _num(raw.get("ratio")),
                "note": (raw.get("note") or "").strip(),
                "source": "本地 CSV",
            })
    except Exception:
        return []
    return out


def fetch(quarters: int = 6) -> Dict[str, Any]:
    """抓取最近若干季度的十大流通股东记录。"""
    key = f"fetch|{quarters}"
    cached = _get(key, 6 * 3600)
    if cached is not None:
        return cached

    import datetime

    since = (datetime.date.today() - datetime.timedelta(days=max(90, quarters * 92))).strftime("%Y-%m-%d")

    records: List[Dict[str, Any]] = []
    errors: List[str] = []
    for holder, label in HOLDERS:
        try:
            rows = _fetch_holder(holder, since)
        except Exception as exc:
            errors.append(f"{label}: {str(exc)[:60]}")
            rows = []
        for r in rows:
            end = _quarter_end(r.get("END_DATE"))
            if not end:
                continue
            records.append({
                "date": end,
                "code": str(r.get("SECURITY_CODE") or ""),
                "name": str(r.get("SECURITY_NAME_ABBR") or ""),
                "holder": label,
                "holder_full": holder,
                "shares": _num(r.get("HOLD_NUM")),
                "ratio": _num(r.get("FREE_HOLDNUM_RATIO")),
                "change": str(r.get("HOLD_NUM_CHANGE") or ""),
                "rank": _num(r.get("HOLDER_RANK")),
            })
    payload = {"records": records, "errors": errors, "since": since}
    _put(key, payload)
    return payload


def analyze(quarters: int = 6) -> Dict[str, Any]:
    """汇总持仓与逐季度的增持 / 减持 / 新进 / 退出。"""
    cached = _get("analyze|%d" % quarters, 6 * 3600)
    if cached is not None:
        return cached

    data = fetch(quarters)
    records = data.get("records") or []
    if not records:
        return {"ok": False, "error": "未能获取汇金持仓数据（数据源不可用）", "errors": data.get("errors") or []}

    dates = sorted({r["date"] for r in records})
    latest = dates[-1] if dates else ""
    prev = dates[-2] if len(dates) > 1 else ""

    by_key: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for r in records:
        by_key.setdefault((r["code"], r["holder"]), []).append(r)
    for series in by_key.values():
        series.sort(key=lambda x: x["date"])

    operations: List[Dict[str, Any]] = []
    for (code, holder), series in by_key.items():
        for idx in range(1, len(series)):
            cur, pre = series[idx], series[idx - 1]
            cur_sh, pre_sh = cur.get("shares"), pre.get("shares")
            if cur_sh is None:
                continue
            if pre_sh is None:
                action = "新进"
                delta = cur_sh
            else:
                delta = cur_sh - pre_sh
                if abs(delta) < 1:
                    continue
                action = "增持" if delta > 0 else "减持"
            pct = (delta / pre_sh * 100) if pre_sh else None
            operations.append({
                "date": cur["date"],
                "code": code,
                "name": cur["name"],
                "holder": holder,
                "action": action,
                "delta": delta,
                "shares": cur_sh,
                "change_pct": round(pct, 2) if pct is not None else None,
                "ratio": cur.get("ratio"),
            })
    operations.sort(key=lambda x: (x["date"], abs(x["delta"])), reverse=True)

    # 退出：上一期有、本期没有
    exits: List[Dict[str, Any]] = []
    latest_count = {
        label: sum(1 for r in records if r["date"] == latest and r["holder"] == label)
        for _, label in HOLDERS
    }
    prev_count = {
        label: sum(1 for r in records if r["date"] == prev and r["holder"] == label)
        for _, label in HOLDERS
    }
    if prev:
        latest_codes = {(r["code"], r["holder"]) for r in records if r["date"] == latest}
        for (code, holder), series in by_key.items():
            dates_of = {s["date"] for s in series}
            if prev not in dates_of or latest in dates_of:
                continue
            if prev_count.get(holder, 0) < 5 or latest_count.get(holder, 0) < prev_count.get(holder, 0) * 0.8:
                continue
            if True:
                last = next((s for s in series if s["date"] == prev), None)
                if last and last.get("shares"):
                    exits.append({
                        "date": latest, "code": code, "name": last["name"], "holder": holder,
                        "action": "退出前十", "delta": -last["shares"], "shares": 0,
                        "change_pct": -100.0, "ratio": 0,
                    })
    operations.extend(exits)
    operations.sort(key=lambda x: (x["date"], abs(x["delta"])), reverse=True)

    # 最新一期持仓
    holdings = [r for r in records if r["date"] == latest]
    summary: Dict[str, Any] = {"latest": latest, "prev": prev, "quarters": dates[-8:]}
    for _, label in HOLDERS:
        rows = [r for r in holdings if r["holder"] == label]
        summary[label] = {
            "count": len(rows),
            "shares": sum(r.get("shares") or 0 for r in rows),
            "changes": {
                "增持": sum(1 for r in rows if r.get("change") == "增持"),
                "减持": sum(1 for r in rows if r.get("change") == "减持"),
                "新进": sum(1 for r in rows if r.get("change") == "新进"),
            },
        }

    top = sorted(holdings, key=lambda r: r.get("shares") or 0, reverse=True)[:20]

    local = _load_csv()
    if local:
        operations = local + operations
        operations.sort(key=lambda x: x.get("date") or "", reverse=True)

    result = {
        "ok": True,
        "summary": summary,
        "operations": operations[:120],
        "holdings": [
            {"code": r["code"], "name": r["name"], "holder": r["holder"],
             "shares": r.get("shares"), "ratio": r.get("ratio"), "rank": r.get("rank")}
            for r in top
        ],
        "total_records": len(records),
        "local_rows": len(local),
        "errors": data.get("errors") or [],
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": "上交所/深交所定期报告前十大流通股东（东方财富汇总）",
    }
    _put("analyze|%d" % quarters, result)
    return result


def write_template(data_dir: str) -> str:
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, CSV_NAME)
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write("# 汇金操作记录本地补充（可选）\n")
            fh.write("# 列：date,code,name,holder,action,shares,ratio,note\n")
            fh.write("# date 用披露期，如 2026-09-30；action 写 增持/减持/新进/退出/公告\n")
            fh.write("date,code,name,holder,action,shares,ratio,note\n")
    return path
