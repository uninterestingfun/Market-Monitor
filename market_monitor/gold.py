"""主要经济体央行黄金储备与买卖记录。

数据来源：
    世界银行 FI.RES.TOTL.CD（总储备）与 FI.RES.XGLD.CD（不含黄金储备）
    黄金价格：本地 COMEX 黄金日线（hf.GC）年均价，缺失时用内置年度均价兜底
吨数 = 黄金美元价值 / 当年均价 * 31.1034768 / 1e6（金衡盎司 -> 吨）
年度增减 = 当年吨数 - 上年吨数，正数为净买入（增持），负数为净卖出（减持）。
"""
from __future__ import annotations

import csv
import os
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
_DATA_DIR: Optional[str] = None
LOCAL_DIRNAME = "gold_reserves"


def set_data_dir(path: str) -> None:
    """由服务端注入数据目录（通常是 <安装目录>/data）。"""
    global _DATA_DIR
    _DATA_DIR = path


def _local_dir() -> Optional[str]:
    if not _DATA_DIR:
        return None
    return os.path.join(_DATA_DIR, LOCAL_DIRNAME)


def _num(value: Any) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace("t", "").replace("吨", "")
    if not text or text in ("-", "--", "N/A"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _load_local(code: str) -> Optional[Dict[str, Any]]:
    """读取 data/gold_reserves/<CODE>.csv，返回最新值、上一次值与来源。"""
    base = _local_dir()
    if not base:
        return None
    path = os.path.join(base, f"{code}.csv")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            lines = [ln for ln in fh if ln.strip() and not ln.lstrip().startswith("#")]
        rows = []
        for row in csv.DictReader(lines):
            day = (row.get("date") or "").strip()
            tonnes = _num(row.get("tonnes") or row.get("吨") or row.get("value"))
            if not day or tonnes is None:
                continue
            rows.append((day, tonnes, (row.get("source") or "").strip(),
                         _num(row.get("pct_reserves") or row.get("share"))))
        if not rows:
            return None
        rows.sort(key=lambda x: x[0])
        day, tonnes, source, share = rows[-1]
        prev = rows[-2][1] if len(rows) >= 2 else None
        return {
            "tonnes": tonnes,
            "prev": prev,
            "as_of": day,
            "source": source or f"本地 CSV（{code}.csv）",
            "share": share,
            "local": True,
            "points": len(rows),
        }
    except Exception:
        return None


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

    # 本地 CSV 覆盖（如世界黄金协会月度数据）；没有世行数据的国家也能补进来
    by_code = {r["code"]: r for r in rows}
    for code, name in COUNTRIES:
        loc = _load_local(code)
        if not loc:
            continue
        row = by_code.get(code)
        if row is None:
            row = {
                "code": code, "name": name, "year": loc["as_of"][:4],
                "tonnes": 0.0, "usd": None, "share": None,
                "change": None, "change_pct": None, "action": "—", "history": [],
            }
            rows.append(row)
            by_code[code] = row
        row["tonnes"] = round(loc["tonnes"], 1)
        row["year"] = loc["as_of"][:4]
        row["as_of"] = loc["as_of"]
        row["source"] = loc["source"]
        row["local"] = True
        if loc.get("share") is not None:
            row["share"] = round(loc["share"], 1)
        if loc.get("prev") is not None and loc["prev"]:
            change = row["tonnes"] - loc["prev"]
            row["change"] = round(change, 1)
            row["change_pct"] = round(change / loc["prev"] * 100, 2)
            row["action"] = "增持" if change > 0.5 else "减持" if change < -0.5 else "持平"
        else:
            row["change"] = None
            row["change_pct"] = None
            row["action"] = "—"

    for r in rows:
        r.setdefault("local", False)

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
        "local_count": sum(1 for r in rows if r.get("local")),
        "source": "世界银行总储备/不含黄金储备 + COMEX 金价换算；本地 CSV 优先",
    }
    _put("snapshot", payload)
    return payload


def warm(prices: Optional[Dict[str, float]] = None) -> None:
    try:
        snapshot(prices)
    except Exception:
        pass


TEMPLATE = """# {name} 黄金储备（从世界黄金协会 / 该国央行导出后粘贴到这里）
#
# 列说明：
#   date          数据日期，YYYY-MM-DD（月度数据建议用月末）
#   tonnes        黄金储备，吨
#   pct_reserves  黄金占外汇储备比例（可选）
#   source        来源说明（显示在界面上）
#
# 规则：
#   - 按 date 排序，最后一行是当前值，倒数第二行用于算同比变化
#   - 以 # 开头的行会被忽略
date,tonnes,pct_reserves,source
2025-09-30,,,世界黄金协会
"""


def write_templates(data_dir: str) -> int:
    """在 data/gold_reserves/ 下为 GDP 前 50 经济体各生成一个 CSV 模板。"""
    base = os.path.join(data_dir, LOCAL_DIRNAME)
    os.makedirs(base, exist_ok=True)
    count = 0
    for code, name in COUNTRIES:
        path = os.path.join(base, f"{code}.csv")
        if os.path.exists(path):
            continue
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(TEMPLATE.format(name=name))
        count += 1
    guide = os.path.join(base, "README.txt")
    if not os.path.exists(guide):
        with open(guide, "w", encoding="utf-8", newline="") as fh:
            fh.write(
                "把各国央行/世界黄金协会的黄金储备数据按 CSV 放进来，文件名用 ISO3 代码（USA.csv、CHN.csv…）。\n"
                "程序会优先使用这里的数字，覆盖世界银行换算结果；界面上标记「本」。\n\n"
                "格式：date,tonnes,pct_reserves,source\n"
                "  至少两行才能算出同比买卖：最后一行是当前值，上一行是上期值。\n\n"
                "示例（中国，月度）：\n"
                "date,tonnes,pct_reserves,source\n"
                "2025-08-31,2302.1,7.4,世界黄金协会\n"
                "2025-09-30,2306.3,7.5,世界黄金协会\n"
            )
    return count
