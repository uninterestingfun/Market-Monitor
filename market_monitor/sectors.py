"""行业板块数据：新浪行业分类、成分股（含总市值）与行业 ETF 匹配。"""
from __future__ import annotations

import json
import threading
import time
from typing import Any, Dict, List, Optional

from .net import http

SINA_HEADERS = {"Referer": "https://finance.sina.com.cn"}
HY_URL = "https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php"
NODE_URL = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"

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
        if value in (None, "", "-", "--"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


# 行业名 -> ETF 名称关键词（用于在 ETF 列表里找对应的行业 ETF）
ETF_ALIASES: Dict[str, List[str]] = {
    "银行": ["银行"],
    "证券": ["证券", "券商"],
    "保险": ["保险"],
    "半导体": ["半导体", "芯片"],
    "元器件": ["电子", "半导体", "芯片"],
    "电子器件": ["电子", "芯片", "半导体"],
    "电子信息": ["科技", "信息", "电子"],
    "医药": ["医药", "医疗"],
    "生物制药": ["生物", "医药", "创新药"],
    "医疗保健": ["医疗", "医药"],
    "医疗器械": ["医疗"],
    "食品": ["食品饮料", "食品"],
    "酿酒行业": ["酒", "白酒", "食品饮料"],
    "有色金属": ["有色", "金属"],
    "煤炭": ["煤炭"],
    "钢铁": ["钢铁"],
    "石油": ["能源", "石油"],
    "电力": ["电力", "公用"],
    "汽车": ["汽车"],
    "新能源": ["新能源", "光伏"],
    "军工": ["军工", "国防"],
    "飞机制造": ["军工", "航空", "国防"],
    "船舶制造": ["军工", "船舶"],
    "房地产": ["地产", "房地产"],
    "建筑": ["基建", "建筑"],
    "建材": ["建材"],
    "化工": ["化工"],
    "家电": ["家电"],
    "电器行业": ["家电", "电器"],
    "农林牧渔": ["农业", "养殖"],
    "旅游": ["旅游", "消费"],
    "传媒": ["传媒", "游戏"],
    "通信": ["通信", "5G"],
    "计算机": ["计算机", "软件", "科技"],
    "软件": ["软件", "计算机"],
    "互联网": ["互联网", "科技"],
    "机械行业": ["机械"],
    "纺织": ["消费"],
}


def industries() -> List[Dict[str, Any]]:
    """新浪行业板块列表（49 个），按涨跌幅降序。"""
    cached = _get("industries", 600)
    if cached is not None:
        return cached
    text = http.get_text(HY_URL, headers=SINA_HEADERS, timeout=15, min_interval=0.2)
    body = text.split("=", 1)[1].strip().rstrip(";")
    data = json.loads(body)
    items: List[Dict[str, Any]] = []
    for node, raw in data.items():
        parts = str(raw).split(",")
        if len(parts) < 6:
            continue
        items.append({
            "node": node,
            "name": parts[1],
            "count": int(_f(parts[2]) or 0),
            "pct": round(_f(parts[5]) or 0.0, 2),
            "lead": parts[12] if len(parts) > 12 else "",
        })
    items.sort(key=lambda x: x["pct"], reverse=True)
    _put("industries", items)
    return items


def members(node: str, limit: int = 50) -> List[Dict[str, Any]]:
    """行业成分股，按总市值降序取前 limit 只。"""
    key = f"members:{node}:{limit}"
    cached = _get(key, 120)
    if cached is not None:
        return cached
    rows = http.get_json(
        NODE_URL,
        params={"page": 1, "num": limit, "sort": "mktcap", "asc": 0, "node": node},
        headers=SINA_HEADERS, timeout=20, min_interval=0.2,
    ) or []
    out: List[Dict[str, Any]] = []
    for r in rows:
        cap = _f(r.get("mktcap"))
        out.append({
            "code": r.get("symbol") or r.get("code"),
            "name": r.get("name"),
            "price": _f(r.get("trade")),
            "pct": _f(r.get("changepercent")),
            "mktcap": round((cap or 0.0) * 1e4, 2),  # 万元 -> 元
        })
    _put(key, out)
    return out


def _etf_list() -> List[Dict[str, Any]]:
    cached = _get("etfs", 7200)
    if cached is not None:
        return cached
    out: List[Dict[str, Any]] = []
    for page in range(1, 13):
        try:
            rows = http.get_json(
                NODE_URL,
                params={"page": page, "num": 100, "sort": "symbol", "asc": 1, "node": "etf_hq_fund"},
                headers=SINA_HEADERS, timeout=20, min_interval=0.25,
            ) or []
        except Exception:
            break
        if not rows:
            break
        for r in rows:
            out.append({
                "code": r.get("symbol"),
                "name": r.get("name") or "",
                "pct": _f(r.get("changepercent")),
                "amount": _f(r.get("amount")) or 0.0,
            })
        if len(rows) < 100:
            break
    if out:
        _put("etfs", out)
    return out


def warm() -> None:
    """后台预热 ETF 列表，避免首次打开板块页等待。"""
    try:
        _etf_list()
    except Exception:
        pass


def match_etf(industry: str) -> Optional[Dict[str, Any]]:
    """按行业名在同名 ETF 里挑成交额最大的一只。"""
    keys = ETF_ALIASES.get(industry, [industry])
    best: Optional[Dict[str, Any]] = None
    for etf in _etf_list():
        name = etf["name"]
        if not any(k in name for k in keys):
            continue
        if best is None or etf["amount"] > best["amount"]:
            best = etf
    if not best:
        return None
    return {"code": best["code"], "name": best["name"], "pct": best["pct"]}


def board(node: str, limit: int = 50) -> Dict[str, Any]:
    """行业详情：名称、涨跌幅、成分股、对应 ETF。"""
    info = next((i for i in industries() if i["node"] == node), None)
    if not info:
        return {"node": node, "members": [], "error": "unknown industry"}
    rows = members(node, limit)
    return {
        "node": node,
        "name": info["name"],
        "count": info["count"],
        "pct": info["pct"],
        "members": rows,
        "etf": match_etf(info["name"]),
        "total_cap": round(sum(m["mktcap"] for m in rows), 2),
    }
