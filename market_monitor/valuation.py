"""估值工具：个股估值卡、市场 PE 代理、股债性价比、金银比。"""
from __future__ import annotations

import csv
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from .net import http

F10_API = "https://datacenter-web.eastmoney.com/api/data/v1/get"
F10_REPORT = "RPT_F10_FINANCE_MAINFINADATA"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"}

# 市场 PE 代理篮子：覆盖金融、消费、科技、能源、工业，按市值加权
MARKET_BASKET = [
    "600519.SH", "601398.SH", "601318.SH", "600036.SH", "601857.SH",
    "600028.SH", "601088.SH", "000333.SZ", "000858.SZ", "600276.SH",
    "601012.SH", "600030.SH",
]

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
        return float(value)
    except (TypeError, ValueError):
        return None


def _secucode(code: str) -> str:
    c = (code or "").lower()
    if c.endswith(".sh") or c.endswith(".sz"):
        return c.upper()
    if c[:2] in ("sh", "sz"):
        return c[2:] + "." + c[:2].upper()
    return c + ".SH"


def financials(code: str) -> List[Dict[str, Any]]:
    """最近若干期主要财务指标。"""
    sec = _secucode(code)
    cached = _get(f"fin|{sec}", 6 * 3600)
    if cached is not None:
        return cached
    params = {
        "reportName": F10_REPORT,
        "columns": "SECUCODE,REPORT_DATE,REPORT_TYPE,EPSJB,BPS,ROEJQ,PARENTNETPROFIT,TOTALOPERATEREVE,PARENTNETPROFITTZ",
        "pageSize": 8,
        "pageNumber": 1,
        "filter": f'(SECUCODE="{sec}")',
        "sortColumns": "REPORT_DATE",
        "sortTypes": "-1",
    }
    try:
        data = http.get_json(F10_API, params=params, headers=HEADERS, timeout=25) or {}
        rows = ((data.get("result") or {}).get("data")) or []
    except Exception:
        rows = []
    _put(f"fin|{sec}", rows)
    return rows


def _ttm(rows: List[Dict[str, Any]], field: str) -> Optional[float]:
    """用「最新累计 + 上年年报 − 上年同期累计」还原 TTM。"""
    if not rows:
        return None
    latest = rows[0]
    latest_date = str(latest.get("REPORT_DATE") or "")[:10]
    try:
        year, month = int(latest_date[:4]), int(latest_date[5:7])
    except Exception:
        return _f(latest.get(field))
    cur = _f(latest.get(field))
    if cur is None:
        return None
    if month == 12:
        return cur
    annual = prev_same = None
    for r in rows[1:]:
        d = str(r.get("REPORT_DATE") or "")[:10]
        if d[:4] == str(year - 1) and d[5:7] == "12":
            annual = _f(r.get(field))
        if d[:4] == str(year - 1) and d[5:7] == f"{month:02d}":
            prev_same = _f(r.get(field))
    if annual is None or prev_same is None:
        return cur
    return cur + annual - prev_same


def stock(code: str, price: Optional[float] = None, cap: Optional[float] = None) -> Dict[str, Any]:
    """个股估值卡：PE / PB / PS / PEG / ROE。"""
    rows = financials(code)
    if not rows:
        return {"ok": False, "error": "未获取到该标的的财务数据（ETF / 指数无财报）"}
    latest = rows[0]
    eps_ttm = _ttm(rows, "EPSJB")
    np_ttm = _ttm(rows, "PARENTNETPROFIT")
    rev_ttm = _ttm(rows, "TOTALOPERATEREVE")
    if cap is None and price and eps_ttm and np_ttm:
        try:
            cap = price * (np_ttm / eps_ttm)
        except Exception:
            cap = None
    bps = _f(latest.get("BPS"))
    roe = _f(latest.get("ROEJQ"))
    growth = _f(latest.get("PARENTNETPROFITTZ"))

    pe = (price / eps_ttm) if (price and eps_ttm) else None
    pb = (price / bps) if (price and bps) else None
    ps = (cap / rev_ttm) if (cap and rev_ttm) else None
    peg = (pe / growth) if (pe and growth and growth > 0) else None

    return {
        "ok": True,
        "report": str(latest.get("REPORT_TYPE") or ""),
        "report_date": str(latest.get("REPORT_DATE") or "")[:10],
        "eps_ttm": round(eps_ttm, 3) if eps_ttm else None,
        "bps": round(bps, 2) if bps else None,
        "roe": round(roe, 2) if roe else None,
        "growth": round(growth, 2) if growth else None,
        "pe": round(pe, 1) if pe and pe > 0 else None,
        "pb": round(pb, 2) if pb and pb > 0 else None,
        "ps": round(ps, 2) if ps and ps > 0 else None,
        "peg": round(peg, 2) if peg and peg > 0 else None,
        "net_profit_ttm": np_ttm,
    }


def market_pe(prices: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """用权重股篮子估算市场 PE（Σ市值 / ΣTTM 净利润），并每日存档。"""
    prices = dict(prices or {})
    # 篮子里的个股通常不在行情列表里，缺价时单独抓一次
    need = []
    for sec in MARKET_BASKET:
        code = sec.split(".")[0]
        key = ("sh" if sec.endswith(".SH") else "sz") + code
        if not prices.get(key):
            need.append(key)
    if need:
        try:
            from . import watchlist

            fetched = watchlist.fetch_quotes(need)
            for k, v in fetched.items():
                if v.get("price"):
                    prices[k] = v["price"]
        except Exception:
            pass

    total_cap = 0.0
    total_np = 0.0
    used = 0
    for sec in MARKET_BASKET:
        rows = financials(sec)
        if not rows:
            continue
        np_ttm = _ttm(rows, "PARENTNETPROFIT")
        code = sec.split(".")[0]
        prefix = "sh" if sec.endswith(".SH") else "sz"
        price = prices.get(prefix + code)
        shares = None
        eps = _ttm(rows, "EPSJB")
        if price and eps and eps > 0:
            # 用 EPS 反推股本：市值 ≈ 股价 × 股本；股本 ≈ 净利润/EPS
            shares = (np_ttm / eps) if np_ttm and eps else None
        if not (np_ttm and shares and price):
            continue
        total_cap += price * shares
        total_np += np_ttm
        used += 1
    if not (total_cap and total_np):
        return {"ok": False, "error": "无法估算市场 PE"}
    pe = total_cap / total_np
    payload = {"ok": True, "pe": round(pe, 2), "samples": used,
               "earnings_yield": round(100.0 / pe, 2)}
    _archive(pe)
    return payload


def _archive(pe: float) -> None:
    if not _DATA_DIR:
        return
    os.makedirs(_DATA_DIR, exist_ok=True)
    path = os.path.join(_DATA_DIR, "market_pe.csv")
    today = time.strftime("%Y-%m-%d")
    rows: List[Tuple[str, float]] = []
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8-sig", newline="") as fh:
                for r in csv.DictReader(fh):
                    if r.get("date") and r.get("pe"):
                        rows.append((r["date"], float(r["pe"])))
        except Exception:
            rows = []
    if not rows or rows[-1][0] != today:
        rows.append((today, pe))
        with open(path, "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["date", "pe"])
            for d, v in rows[-500:]:
                w.writerow([d, round(v, 2)])


def pe_history() -> List[Tuple[str, float]]:
    if not _DATA_DIR:
        return []
    path = os.path.join(_DATA_DIR, "market_pe.csv")
    if not os.path.exists(path):
        return []
    out: List[Tuple[str, float]] = []
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            for r in csv.DictReader(fh):
                if r.get("date") and r.get("pe"):
                    out.append((r["date"], float(r["pe"])))
    except Exception:
        return []
    return out


def percentile(values: List[float], current: float) -> Optional[float]:
    if not values or current is None:
        return None
    below = sum(1 for v in values if v <= current)
    return round(below / len(values) * 100, 1)


def erp(pe: Optional[float], yield_10y: Optional[float]) -> Optional[float]:
    if not pe or not yield_10y:
        return None
    return round(100.0 / pe - yield_10y, 2)


def gold_silver(bars_gold: List[List[Any]], bars_silver: List[List[Any]]) -> List[List[Any]]:
    """金银比序列：同一交易日 gold/silver。"""
    gold = {str(b[0])[:10]: b[4] for b in bars_gold if len(b) > 4 and b[4]}
    silver = {str(b[0])[:10]: b[4] for b in bars_silver if len(b) > 4 and b[4]}
    out: List[List[Any]] = []
    for d in sorted(set(gold) & set(silver)):
        s = silver[d]
        if s:
            out.append([d, round(gold[d] / s, 2)])
    return out


# ────────────────────── 指数估值带（低估 / 合理 / 高估） ──────────────────────
# 官方指数 PE 接口不可用，这里用「代表成分加权 PE」近似，并用可编辑的估值带给出位置。
# 阈值来自长期经验区间，可在 data/valuation/index_bands.json 覆盖（随数据积累可改成分位数）。
INDEX_BANDS: List[Dict[str, Any]] = [
    {
        "key": "hs300", "name": "沪深300", "asset": "sh000300",
        "members": ["600519.SH", "601398.SH", "601318.SH", "600036.SH", "601857.SH",
                    "600028.SH", "601088.SH", "000333.SZ", "000858.SZ", "600276.SH",
                    "601012.SH", "600030.SH"],
        "bands": {"low": 11.0, "fair": 13.0, "high": 15.0},
    },
    {
        "key": "sz50", "name": "上证50", "asset": "sh000016",
        "members": ["600519.SH", "601398.SH", "601318.SH", "600036.SH", "601857.SH",
                    "600028.SH", "601088.SH", "600030.SH", "601166.SH", "600887.SH",
                    "601899.SH", "600309.SH"],
        "bands": {"low": 9.0, "fair": 11.0, "high": 13.5},
    },
    {
        "key": "zz500", "name": "中证500", "asset": "sh000905",
        "members": ["002415.SZ", "300760.SZ", "002460.SZ", "600438.SH", "002466.SZ",
                    "300124.SZ", "002714.SZ", "600809.SH", "603259.SH", "002050.SZ",
                    "600415.SH", "000625.SZ"],
        "bands": {"low": 20.0, "fair": 26.0, "high": 34.0},
    },
    {
        "key": "kc50", "name": "科创50", "asset": "sh000688",
        "members": ["688981.SH", "688111.SH", "688036.SH", "688012.SH", "688169.SH",
                    "688008.SH", "688396.SH", "688599.SH", "688187.SH", "688303.SH",
                    "688180.SH", "688223.SH"],
        "bands": {"low": 35.0, "fair": 50.0, "high": 70.0},
    },
    {
        "key": "cyb", "name": "创业板指", "asset": "sz399006",
        "members": ["300750.SZ", "300059.SZ", "300760.SZ", "300124.SZ", "300274.SZ",
                    "300308.SZ", "300014.SZ", "300999.SZ", "300122.SZ", "300142.SZ",
                    "300661.SZ", "300496.SZ"],
        "bands": {"low": 28.0, "fair": 38.0, "high": 55.0},
    },
    {
        "key": "pharma", "name": "医药（行业篮子）", "asset": "",
        "members": ["600276.SH", "603259.SH", "300760.SZ", "600196.SH", "000538.SZ",
                    "600436.SH", "300122.SZ", "000661.SZ", "300142.SZ", "600085.SH",
                    "002007.SZ", "000963.SZ"],
        "bands": {"low": 22.0, "fair": 28.0, "high": 36.0},
    },
]


def _load_band_overrides() -> Dict[str, Any]:
    """读取用户覆盖的估值带：data/valuation/index_bands.json。"""
    if not _DATA_DIR:
        return {}
    path = os.path.join(_DATA_DIR, "index_bands.json")
    if not os.path.exists(path):
        try:
            os.makedirs(_DATA_DIR, exist_ok=True)
            import json as _json

            sample = {b["key"]: {"low": b["bands"]["low"], "fair": b["bands"]["fair"],
                                 "high": b["bands"]["high"]} for b in INDEX_BANDS}
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write("# 各指数估值带（PE-TTM），可自行调整；保存为合法 JSON 即可\n")
                fh.write(_json.dumps(sample, ensure_ascii=False, indent=2))
        except Exception:
            return {}
        return {}
    try:
        import json as _json

        with open(path, "r", encoding="utf-8-sig") as fh:
            text = "".join(ln for ln in fh if not ln.lstrip().startswith("#"))
        data = _json.loads(text or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def index_valuations(indices: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """逐个指数计算加权 PE 与所处估值区间。"""
    cached = _get("index_val", 6 * 3600)
    if cached is not None:
        return cached
    overrides = _load_band_overrides()
    out: List[Dict[str, Any]] = []
    for cfg in (indices or INDEX_BANDS):
        members = cfg["members"]
        total_cap = 0.0
        total_np = 0.0
        used = 0
        codes = []
        for sec in members:
            code = sec.split(".")[0]
            codes.append(("sh" if sec.endswith(".SH") else "sz") + code)
        try:
            from . import watchlist

            quotes = watchlist.fetch_quotes(codes)
        except Exception:
            quotes = {}
        for sec, key in zip(members, codes):
            price = (quotes.get(key) or {}).get("price")
            rows = financials(sec)
            if not (price and rows):
                continue
            eps = _ttm(rows, "EPSJB")
            np_ttm = _ttm(rows, "PARENTNETPROFIT")
            if not (eps and np_ttm and eps > 0):
                continue
            shares = np_ttm / eps
            total_cap += price * shares
            total_np += np_ttm
            used += 1
        pe = round(total_cap / total_np, 2) if (total_cap and total_np) else None
        bands = dict(cfg["bands"])
        if cfg["key"] in overrides and isinstance(overrides[cfg["key"]], dict):
            bands.update({k: float(v) for k, v in overrides[cfg["key"]].items()
                          if k in ("low", "fair", "high") and v})
        zone = None
        if pe:
            if pe <= bands["low"]:
                zone = "低估"
            elif pe >= bands["high"]:
                zone = "高估"
            else:
                zone = "合理"
        out.append({
            "key": cfg["key"],
            "name": cfg["name"],
            "pe": pe,
            "samples": used,
            "bands": bands,
            "zone": zone,
            "note": "代表成分加权 PE（非官方指数 PE）",
        })
    _put("index_val", out)
    return out
