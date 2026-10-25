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
    # 国民经济行业分类（GB/T 4754）名称
    "货币金融服务": ["银行"],
    "资本市场服务": ["证券", "券商"],
    "保险业": ["保险"],
    "其他金融业": ["金融", "证券"],
    "酒、饮料和精制茶制造业": ["酒", "食品饮料"],
    "医药制造业": ["医药", "医疗"],
    "汽车制造业": ["汽车"],
    "计算机、通信和其他电子设备制造业": ["电子", "芯片", "半导体"],
    "电气机械和器材制造业": ["新能源", "电力设备", "光伏"],
    "有色金属冶炼和压延加工业": ["有色", "金属"],
    "化学原料和化学制品制造业": ["化工"],
    "农副食品加工业": ["食品饮料", "农业"],
    "食品制造业": ["食品饮料"],
    "纺织服装、服饰业": ["消费"],
    "软件和信息技术服务业": ["软件", "计算机"],
    "房地产业": ["地产", "房地产"],
    "电力、热力生产和供应业": ["电力", "公用"],
    "铁路、船舶、航空航天和其他运输设备制造业": ["军工", "国防"],
}



# ────────────────────────────── 金融细分 ──────────────────────────────
FIN_NODE = "new_jrhy"
FIN_GROUPS = [
    ("fin.bank", "银行"),
    ("fin.broker", "证券"),
    ("fin.insurance", "保险"),
    ("fin.other", "多元金融"),
]

# 名称里带行情前缀（除权除息标记）时先剥掉
_PREFIX_RE = __import__("re").compile(r"^(XD|XR|DR|N|C)")

# 少量名称里看不出行业的个股，单独归类
_NAME_OVERRIDE = {
    "申万宏源": "fin.broker",
    "太平洋": "fin.broker",
    "锦龙股份": "fin.broker",
    "湘财股份": "fin.broker",
    "哈投股份": "fin.broker",
    "中国平安": "fin.insurance",
    "中国人寿": "fin.insurance",
    "中国人保": "fin.insurance",
    "中国太保": "fin.insurance",
    "新华保险": "fin.insurance",
    "天茂集团": "fin.insurance",
    "渤海租赁": "fin.other",
    "中航产融": "fin.other",
    "中油资本": "fin.other",
    "五矿资本": "fin.other",
    "建元信托": "fin.other",
    "陕国投Ａ": "fin.other",
    "陕国投A": "fin.other",
    "爱建集团": "fin.other",
    "香溢融通": "fin.other",
    "退市西水": "fin.other",
}


def _classify(name: str) -> str:
    clean = _PREFIX_RE.sub("", name or "")
    if clean in _NAME_OVERRIDE:
        return _NAME_OVERRIDE[clean]
    if "银行" in clean:
        return "fin.bank"
    if "证券" in clean or "券商" in clean:
        return "fin.broker"
    if any(k in clean for k in ("保险", "人寿", "人保", "太保")):
        return "fin.insurance"
    # 新浪会把长名称截断（如 XD中国太 -> 中国太保、XD华泰证 -> 华泰证券）
    if clean.startswith("中国太"):
        return "fin.insurance"
    if clean.endswith("证"):
        return "fin.broker"
    if any(k in clean for k in ("信托", "租赁", "期货", "基金", "资管", "金控")):
        return "fin.other"
    return "fin.other"


def finance_groups() -> Dict[str, Dict[str, Any]]:
    """把新浪「金融行业」按银行/证券/保险/多元金融拆分。"""
    cached = _get("fin_groups", 600)
    if cached is not None:
        return cached

    rows = []
    for page in (1, 2, 3):
        try:
            batch = http.get_json(
                NODE_URL,
                params={"page": page, "num": 100, "sort": "mktcap", "asc": 0, "node": FIN_NODE},
                headers=SINA_HEADERS, timeout=20, min_interval=0.2,
            ) or []
        except Exception:
            break
        if not batch:
            break
        for r in batch:
            cap = _f(r.get("mktcap"))
            rows.append({
                "code": r.get("symbol") or r.get("code"),
                "name": r.get("name"),
                "price": _f(r.get("trade")),
                "pct": _f(r.get("changepercent")),
                "mktcap": round((cap or 0.0) * 1e4, 2),
                "group": _classify(r.get("name") or ""),
            })
        if len(batch) < 100:
            break

    groups: Dict[str, Dict[str, Any]] = {}
    for node, name in FIN_GROUPS:
        members = [r for r in rows if r["group"] == node]
        members.sort(key=lambda r: r["mktcap"], reverse=True)
        cap = sum(m["mktcap"] for m in members)
        pct = (sum((m["pct"] or 0) * m["mktcap"] for m in members) / cap) if cap else 0.0
        groups[node] = {
            "node": node,
            "name": name,
            "count": len(members),
            "pct": round(pct, 2),
            "members": [{k: m[k] for k in ("code", "name", "price", "pct", "mktcap")} for m in members],
            "total_cap": round(cap, 2),
            "virtual": True,
        }
    _put("fin_groups", groups)
    return groups



# ─────────────────── 国民经济行业分类（GB/T 4754，两级） ───────────────────
FLJK_URL = "https://vip.stock.finance.sina.com.cn/q/view/newFLJK.php?param=sw2"

GROUP_NAMES = {
    "ZA": "农、林、牧、渔业",
    "ZB": "采矿业",
    "ZC": "制造业",
    "ZD": "电力、热力、燃气及水生产和供应业",
    "ZE": "建筑业",
    "ZF": "批发和零售业",
    "ZG": "交通运输、仓储和邮政业",
    "ZH": "住宿和餐饮业",
    "ZI": "信息传输、软件和信息技术服务业",
    "ZJ": "金融业",
    "ZK": "房地产业",
    "ZL": "租赁和商务服务业",
    "ZM": "科学研究和技术服务业",
    "ZN": "水利、环境和公共设施管理业",
    "ZO": "居民服务、修理和其他服务业",
    "ZP": "教育",
    "ZQ": "卫生和社会工作",
    "ZR": "文化、体育和娱乐业",
    "ZS": "公共管理、社会保障和社会组织",
}


def industry_tree() -> List[Dict[str, Any]]:
    """19 个门类 + 84 个大类的两级行业树。"""
    cached = _get("tree", 600)
    if cached is not None:
        return cached

    text = http.get_text(FLJK_URL, headers=SINA_HEADERS, timeout=15, min_interval=0.2, encoding="gbk")
    body = text.split("=", 1)[1].strip().rstrip(";")
    data = json.loads(body)

    leaves: List[Dict[str, Any]] = []
    for code, raw in data.items():
        parts = str(raw).split(",")
        if len(parts) < 8:
            continue
        try:
            count = int(parts[2])
        except ValueError:
            count = 0
        leaves.append({
            "node": code,
            "name": parts[1],
            "count": count,
            "pct": round(_f(parts[5]) or 0.0, 2),
            "amount": _f(parts[7]) or 0.0,
            "lead": parts[12] if len(parts) > 12 else "",
        })

    groups: Dict[str, Dict[str, Any]] = {}
    for leaf in leaves:
        prefix = leaf["node"].split("_")[1][:2] if "_" in leaf["node"] else ""
        g = groups.setdefault(prefix, {
            "node": f"grp.{prefix}",
            "name": GROUP_NAMES.get(prefix, prefix),
            "count": 0,
            "pct": 0.0,
            "amount": 0.0,
            "children": [],
        })
        g["children"].append(leaf)
        g["count"] += leaf["count"]
        g["amount"] += leaf["amount"]
        g["pct"] += leaf["pct"] * leaf["amount"]

    tree = []
    for g in groups.values():
        g["pct"] = round(g["pct"] / g["amount"], 2) if g["amount"] else 0.0
        g["children"].sort(key=lambda x: x["pct"], reverse=True)
        tree.append(g)
    tree.sort(key=lambda x: x["pct"], reverse=True)

    _put("tree", tree)
    _put("leaves", leaves)
    return tree


def leaves() -> List[Dict[str, Any]]:
    industry_tree()
    return _get("leaves", 600) or []


def industries() -> List[Dict[str, Any]]:
    """国民经济行业分类的 84 个大类，按涨跌幅降序。"""
    try:
        rows = leaves()
        if rows:
            items = [dict(r) for r in rows]
            items.sort(key=lambda x: x["pct"], reverse=True)
            return items
    except Exception:
        pass
    return _sina_industries()


def _sina_industries() -> List[Dict[str, Any]]:
    """旧版新浪行业分类（49 个），仅作兜底。"""
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
    # 把笼统的「金融行业」补上四个细分板块
    try:
        fins = finance_groups()
        extra = []
        for node, name in FIN_GROUPS:
            g = fins.get(node) or {}
            if not g.get("count"):
                continue
            extra.append({"node": node, "name": f"金融·{name}", "count": g["count"],
                          "pct": g["pct"], "lead": "", "virtual": True})
        if extra:
            items = [i for i in items if i["node"] != FIN_NODE] + extra
    except Exception:
        pass

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



# ────────────────────────── 三级细分（规则） ──────────────────────────
# 新浪没有公开的三级行业清单（sw1/sw2/sw3 返回同一份 84 条），
# 这里用「名称规则 + 名单」做近似细分，只在用户打开「三级细分」时计算。
L3_RULES: Dict[str, List[tuple]] = {
    "hangye_ZJ66": [
        ("国有大型银行", ["工商银行", "建设银行", "农业银行", "中国银行", "交通银行", "邮储银行"]),
        ("股份制银行", ["招商银行", "浦发银行", "中信银行", "兴业银行", "民生银行", "光大银行",
                     "华夏银行", "平安银行", "浙商银行", "渤海银行", "恒丰银行"]),
        ("城商行", ["北京银行", "上海银行", "江苏银行", "南京银行", "宁波银行", "杭州银行", "成都银行",
                 "长沙银行", "苏州银行", "郑州银行", "西安银行", "青岛银行", "贵阳银行", "重庆银行",
                 "兰州银行", "厦门银行", "齐鲁银行", "徽商银行", "盛京银行", "哈尔滨银行"]),
        ("农商行", ["渝农商行", "沪农商行", "常熟银行", "紫金银行", "无锡银行", "张家港行",
                 "苏农银行", "瑞丰银行", "江阴银行"]),
    ],
    "hangye_ZJ67": [
        ("证券", ["证券"]),
        ("基金", ["基金"]),
        ("期货", ["期货"]),
        ("多元金融", ["信托", "租赁", "创投", "资本", "投资"]),
    ],
    "hangye_ZJ68": [
        ("寿险", ["人寿", "平安", "太保", "新华", "友邦"]),
        ("财险", ["财险", "人保", "太平"]),
    ],
    "hangye_ZC15": [
        ("白酒", ["白酒"]),
        ("啤酒", ["啤酒"]),
        ("葡萄酒", ["葡萄酒"]),
        ("其他饮料", ["饮料", "茶", "乳"]),
    ],
    "hangye_ZC27": [
        ("中药", ["中药", "药业", "制药"]),
        ("生物制品", ["生物", "疫苗", "血制品"]),
        ("化学制药", ["化学", "医药"]),
        ("医疗器械", ["器械", "医疗"]),
    ],
    "hangye_ZC36": [
        ("整车", ["汽车", "客车", "重汽"]),
        ("零部件", ["零部件", "配件", "轮胎", "座椅"]),
    ],
    "hangye_ZI65": [
        ("软件", ["软件", "信息", "数据"]),
        ("信息安全", ["安全", "密码"]),
        ("云服务", ["云", "计算"]),
    ],
    "hangye_ZC39": [
        ("半导体", ["半导体", "芯片", "微电子", "集成电路"]),
        ("消费电子", ["电子", "科技", "精密"]),
        ("通信设备", ["通信", "网络", "光"]),
    ],
}


def _l3_members(node: str) -> List[Dict[str, Any]]:
    """取出某个大类的成分股并按规则分三级。"""
    rules = L3_RULES.get(node)
    if not rules:
        return []
    rows = members(node, 100)
    buckets: List[Dict[str, Any]] = []
    used = set()
    for name, keys in rules:
        picked = []
        for r in rows:
            code = r.get("code")
            if code in used:
                continue
            stock = r.get("name") or ""
            if stock in keys or any(k in stock for k in keys):
                picked.append(r)
                used.add(code)
        if picked:
            cap = sum(m["mktcap"] or 0 for m in picked)
            pct = sum((m["pct"] or 0) * (m["mktcap"] or 0) for m in picked) / cap if cap else 0.0
            buckets.append({
                "node": f"{node}:{name}",
                "name": name,
                "count": len(picked),
                "pct": round(pct, 2),
                "members": picked,
                "virtual": True,
            })
    rest = [r for r in rows if r.get("code") not in used]
    if rest:
        cap = sum(m["mktcap"] or 0 for m in rest)
        pct = sum((m["pct"] or 0) * (m["mktcap"] or 0) for m in rest) / cap if cap else 0.0
        buckets.append({
            "node": f"{node}:其他",
            "name": "其他",
            "count": len(rest),
            "pct": round(pct, 2),
            "members": rest,
            "virtual": True,
        })
    buckets.sort(key=lambda x: x["count"], reverse=True)
    return buckets


def tree_with_detail() -> List[Dict[str, Any]]:
    """带三级的行业树（仅在用户开启三级细分时调用）。"""
    cached = _get("tree_detail", 600)
    if cached is not None:
        return cached
    tree = [dict(g) for g in industry_tree()]
    for group in tree:
        kids = []
        for leaf in group["children"]:
            item = dict(leaf)
            subs = _l3_members(leaf["node"])
            if subs:
                item["children"] = [
                    {"node": b["node"], "name": b["name"], "count": b["count"], "pct": b["pct"]}
                    for b in subs
                ]
            kids.append(item)
        group["children"] = kids
    _put("tree_detail", tree)
    return tree


def board_detail(node: str) -> Optional[Dict[str, Any]]:
    """三级节点（形如 hangye_ZJ66:股份制银行）的成分股。"""
    if ":" not in node:
        return None
    parent, name = node.split(":", 1)
    for bucket in _l3_members(parent):
        if bucket["name"] == name:
            return {
                "node": node,
                "name": name,
                "count": bucket["count"],
                "pct": bucket["pct"],
                "members": bucket["members"][:50],
                "etf": match_etf(name),
                "total_cap": round(sum(m["mktcap"] or 0 for m in bucket["members"]), 2),
            }
    return None


def board(node: str, limit: int = 50) -> Dict[str, Any]:
    """行业详情：名称、涨跌幅、成分股、对应 ETF。"""
    detail = board_detail(node)
    if detail is not None:
        return detail

    # 门类节点（grp.ZX）：聚合其下所有二级行业的成分股
    if node.startswith("grp."):
        prefix = node[4:]
        kids = [leaf for leaf in leaves() if leaf["node"].split("_")[1][:2] == prefix]
        merged: Dict[str, Dict[str, Any]] = {}
        group_name = GROUP_NAMES.get(prefix, prefix)
        for leaf in kids:
            try:
                rows = members(leaf["node"], 100)
            except Exception:
                rows = []
            for r in rows:
                code = r.get("code")
                if code and code not in merged:
                    merged[code] = r
        items = sorted(merged.values(), key=lambda r: r.get("mktcap") or 0, reverse=True)
        cap = sum(r.get("mktcap") or 0 for r in items)
        pct = sum((r.get("pct") or 0) * (r.get("mktcap") or 0) for r in items) / cap if cap else 0.0
        return {
            "node": node,
            "name": group_name,
            "count": len(items),
            "pct": round(pct, 2),
            "members": items[:limit],
            "etf": None,
            "total_cap": round(cap, 2),
            "aggregated": len(kids),
        }

    if node.startswith("fin."):
        g = (finance_groups() or {}).get(node) or {}
        if not g:
            return {"node": node, "members": [], "error": "unknown industry"}
        return {
            "node": node,
            "name": g["name"],
            "count": g["count"],
            "pct": g["pct"],
            "members": g["members"][:limit],
            "etf": match_etf(g["name"]),
            "total_cap": g["total_cap"],
        }
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
