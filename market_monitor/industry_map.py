"""自定义行业分类（中证 / 中信等授权口径）。

中证指数、中信证券的行业归属属于授权数据，没有公开接口；
这里提供导入机制：把导出的映射表放到 data/industry_map.csv，程序据此建树，
并用腾讯行情批量取价格与总市值，实时计算各层级涨跌幅。

CSV 列：code,name,level1,level2,level3
    code   股票代码，可写 600000 或 sh600000
    name   股票名称（仅展示用，可留空）
    level1 一级行业（必填）
    level2 二级行业（可选）
    level3 三级行业（可选）
"""
from __future__ import annotations

import csv
import os
import threading
import time
from typing import Any, Dict, List, Optional

from .net import http

FILENAME = "industry_map.csv"
TENCENT = "https://qt.gtimg.cn/q="
HEADERS = {"Referer": "https://gu.qq.com/"}

_LOCK = threading.Lock()
_CACHE: Dict[str, tuple] = {}
_DATA_DIR: Optional[str] = None


def set_data_dir(path: str) -> None:
    global _DATA_DIR
    _DATA_DIR = path


def map_path() -> Optional[str]:
    if not _DATA_DIR:
        return None
    return os.path.join(_DATA_DIR, FILENAME)


def _get(key: str, ttl: float) -> Optional[Any]:
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and time.time() - hit[1] < ttl:
            return hit[0]
    return None


def _put(key: str, value: Any) -> None:
    with _LOCK:
        _CACHE[key] = (value, time.time())


def _norm_code(code: str) -> str:
    c = (code or "").strip().lower()
    if not c:
        return ""
    if c[:2] in ("sh", "sz", "bj"):
        return c
    if c.startswith(("6", "9")):
        return "sh" + c
    if c.startswith(("0", "3")):
        return "sz" + c
    if c.startswith(("4", "8")):
        return "bj" + c
    return c


def load_rows() -> Optional[List[Dict[str, str]]]:
    """读取映射文件；不存在时返回 None。"""
    path = map_path()
    if not path or not os.path.exists(path):
        return None
    cached = _get("rows", 600)
    if cached is not None:
        return cached
    rows: List[Dict[str, str]] = []
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            lines = [ln for ln in fh if ln.strip() and not ln.lstrip().startswith("#")]
        for raw in csv.DictReader(lines):
            code = _norm_code(raw.get("code") or raw.get("代码") or "")
            l1 = (raw.get("level1") or raw.get("一级") or "").strip()
            if not code or not l1:
                continue
            rows.append({
                "code": code,
                "name": (raw.get("name") or raw.get("名称") or "").strip(),
                "level1": l1,
                "level2": (raw.get("level2") or raw.get("二级") or "").strip(),
                "level3": (raw.get("level3") or raw.get("三级") or "").strip(),
            })
    except Exception:
        return None
    _put("rows", rows)
    return rows


def _fetch_quotes(codes: List[str]) -> Dict[str, Dict[str, Any]]:
    cached = _get("quotes", 300)
    if cached is not None:
        return cached
    out: Dict[str, Dict[str, Any]] = {}
    for i in range(0, len(codes), 60):
        batch = codes[i:i + 60]
        try:
            text = http.get_text(TENCENT + ",".join(batch), headers=HEADERS, timeout=15, encoding="gbk")
        except Exception:
            continue
        for line in text.split(";"):
            if "~" not in line:
                continue
            f = line.split("~")
            if len(f) < 46:
                continue
            code = (f[2] or "").strip()
            if not code:
                continue
            prefix = "sh" if line.split("=")[0].endswith("sh" + code) else ("sz" if line.split("=")[0].endswith("sz" + code) else "bj")
            try:
                price = float(f[3]) if f[3] else 0.0
                pct = float(f[32]) if f[32] else 0.0
                cap = float(f[45]) * 1e8 if f[45] else 0.0
            except ValueError:
                continue
            if price <= 0 or cap <= 0:
                continue
            out[prefix + code] = {"name": f[1], "price": price, "pct": pct, "mktcap": cap}
    if out:
        _put("quotes", out)
    return out


def build_tree() -> Dict[str, Any]:
    """把映射文件 + 实时行情聚合成三级树。"""
    rows = load_rows()
    catalog = read_catalog()
    if rows is None:
        return {"ok": False, "error": "missing", "path": map_path(),
                "catalog_rows": len(catalog), "catalog_path": find_catalog()}
    if not rows:
        return {"ok": False, "error": "unfilled" if catalog else "empty", "path": map_path(),
                "catalog_rows": len(catalog), "catalog_path": find_catalog()}

    codes = sorted({r["code"] for r in rows})
    quotes = _fetch_quotes(codes)

    tree: Dict[str, Any] = {}
    leaves_flat: List[Dict[str, Any]] = []
    for r in rows:
        q = quotes.get(r["code"])
        if not q:
            continue
        l1, l2, l3 = r["level1"], r["level2"] or "（未细分）", r["level3"]
        g = tree.setdefault(l1, {"node": f"csi:{l1}", "name": l1, "count": 0, "cap": 0.0, "weighted": 0.0, "children": {}})
        c = g["children"].setdefault(l2, {"node": f"csi:{l1}/{l2}", "name": l2, "count": 0, "cap": 0.0, "weighted": 0.0, "children": {}})
        node = c
        if l3:
            s = c["children"].setdefault(l3, {"node": f"csi:{l1}/{l2}/{l3}", "name": l3, "count": 0, "cap": 0.0, "weighted": 0.0, "members": []})
            s["count"] += 1
            s["cap"] += q["mktcap"]
            s["weighted"] += q["pct"] * q["mktcap"]
            s["members"].append({"code": r["code"], "name": q["name"], "pct": round(q["pct"], 2), "mktcap": round(q["mktcap"], 2)})
            node = s
        c["count"] += 1
        c["cap"] += q["mktcap"]
        c["weighted"] += q["pct"] * q["mktcap"]
        g["count"] += 1
        g["cap"] += q["mktcap"]
        g["weighted"] += q["pct"] * q["mktcap"]

    def finalize(node: Dict[str, Any]) -> Dict[str, Any]:
        node["pct"] = round(node["weighted"] / node["cap"], 2) if node["cap"] else 0.0
        node.pop("weighted", None)
        if "children" in node:
            kids = [finalize(v) for v in node["children"].values()]
            kids.sort(key=lambda x: x["count"], reverse=True)
            node["children"] = kids
        if "members" in node:
            node["members"].sort(key=lambda m: m["mktcap"], reverse=True)
        return node

    groups = [finalize(g) for g in tree.values()]
    groups.sort(key=lambda g: g["cap"], reverse=True)

    leaves = []
    for g in groups:
        for c in g["children"]:
            leaves.append({"node": c["node"], "name": c["name"], "count": c["count"], "pct": c["pct"]})
    leaves.sort(key=lambda x: x["pct"], reverse=True)

    payload = {
        "ok": True,
        "scheme": "csi",
        "tree": groups,
        "industries": leaves,
        "covered": sum(g["count"] for g in groups),
        "total": len(rows),
        "path": map_path(),
    }
    _put("tree", payload)
    return payload


def board(node: str) -> Optional[Dict[str, Any]]:
    """三级树节点 -> 成分股。"""
    if not node.startswith("csi:"):
        return None
    key = node[4:]
    parts = key.split("/")
    l1 = parts[0]
    l2 = parts[1] if len(parts) > 1 else None
    l3 = parts[2] if len(parts) > 2 else None

    data = build_tree()
    if not data.get("ok"):
        return None
    for g in data["tree"]:
        if g["name"] != l1:
            continue
        if not l2:
            members = []
            for c in g["children"]:
                if c.get("children"):
                    for s in c["children"]:
                        members.extend(s.get("members", []))
                else:
                    members.extend(c.get("members", []))
            members.sort(key=lambda m: m["mktcap"], reverse=True)
            return {"node": node, "name": l1, "count": len(members), "pct": g["pct"],
                    "members": members[:50], "etf": None,
                    "total_cap": round(sum(m["mktcap"] for m in members), 2)}
        for c in g["children"]:
            if c["name"] != l2:
                continue
            if not l3:
                if c.get("children"):
                    members = [m for s in c["children"] for m in s.get("members", [])]
                else:
                    members = c.get("members", [])
                members.sort(key=lambda m: m["mktcap"], reverse=True)
                return {"node": node, "name": l2, "count": len(members), "pct": c["pct"],
                        "members": members[:50], "etf": None,
                        "total_cap": round(sum(m["mktcap"] for m in members), 2)}
            for s in c.get("children", []):
                if s["name"] == l3:
                    return {"node": node, "name": l3, "count": s["count"], "pct": s["pct"],
                            "members": s.get("members", [])[:50], "etf": None,
                            "total_cap": round(sum(m["mktcap"] for m in s.get("members", [])), 2)}
    return None


TEMPLATE = """# 自定义行业分类映射（中证 / 中信等授权口径）
# 列：code,name,level1,level2,level3
#   code  可写 600000 或 sh600000
#   level1 必填；level2/level3 可留空（留空则归入「（未细分）」）
# 从 Wind / Choice / 中证指数官网导出后，按此格式整理即可。
code,name,level1,level2,level3
# 示例（去掉行首 # 即可使用）：
# 600000,浦发银行,金融,银行,股份制银行
# 600036,招商银行,金融,银行,股份制银行
# 601318,中国平安,金融,保险,寿险
# 600519,贵州茅台,消费,食品饮料,白酒
"""


def write_template(data_dir: str) -> str:
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, FILENAME)
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(TEMPLATE)
    return path


# ────────────────── 中证分类目录（xlsx，无第三方依赖） ──────────────────
CATALOG_NAMES = ["中证行业分类架构.xlsx", "中证行业分类.xlsx", "csi_industry.xlsx"]
_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _read_xlsx_rows(path: str) -> List[List[str]]:
    """用标准库解析 xlsx 首个工作表（不依赖 openpyxl）。"""
    import zipfile
    import xml.etree.ElementTree as ET

    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        shared: List[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.iter(_NS + "si"):
                shared.append("".join(t.text or "" for t in si.iter(_NS + "t")))
        sheet_name = "xl/worksheets/sheet1.xml"
        if sheet_name not in names:
            sheets = [n for n in names if n.startswith("xl/worksheets/sheet")]
            if not sheets:
                return []
            sheet_name = sheets[0]
        root = ET.fromstring(z.read(sheet_name))
        rows: List[List[str]] = []
        for row in root.iter(_NS + "row"):
            vals: List[str] = []
            for c in row:
                t = c.get("t")
                v = c.find(_NS + "v")
                if v is None:
                    is_ = c.find(_NS + "is")
                    vals.append("".join(x.text or "" for x in is_.iter(_NS + "t")) if is_ is not None else "")
                elif t == "s" and shared:
                    try:
                        vals.append(shared[int(v.text)])
                    except (ValueError, IndexError):
                        vals.append("")
                else:
                    vals.append(v.text or "")
            rows.append(vals)
        return rows


def find_catalog() -> Optional[str]:
    base = _DATA_DIR
    if not base or not os.path.isdir(base):
        return None
    for name in CATALOG_NAMES:
        path = os.path.join(base, name)
        if os.path.exists(path):
            return path
    for name in os.listdir(base):
        if name.lower().endswith(".xlsx") and "行业" in name:
            return os.path.join(base, name)
    return None


def read_catalog(path: Optional[str] = None) -> List[Dict[str, str]]:
    """读取中证行业分类目录（四级），返回 [{l1,l2,l3,l4}]（自动向下填充）。"""
    target = path or find_catalog()
    if not target or not os.path.exists(target):
        return []
    cached = _get("catalog", 3600)
    if cached is not None:
        return cached
    try:
        rows = _read_xlsx_rows(target)
    except Exception:
        return []
    if not rows:
        return []
    header = [str(x).strip() for x in rows[0]]
    idx = {}
    for i, h in enumerate(header):
        if "一级" in h and "代码" in h:
            idx["l1c"] = i
        elif "一级" in h:
            idx["l1"] = i
        elif "二级" in h and "代码" in h:
            idx["l2c"] = i
        elif "二级" in h:
            idx["l2"] = i
        elif "三级" in h and "代码" in h:
            idx["l3c"] = i
        elif "三级" in h:
            idx["l3"] = i
    if "l1" not in idx:
        return []

    out: List[Dict[str, str]] = []
    cur = {"l1": "", "l2": "", "l3": ""}
    for r in rows[1:]:
        def cell(key: str) -> str:
            i = idx.get(key)
            return (r[i] if i is not None and i < len(r) else "").strip()

        l1 = cell("l1") or cur["l1"]
        l2 = cell("l2") or (cur["l2"] if l1 == cur["l1"] else "")
        l3 = cell("l3") or (cur["l3"] if l1 == cur["l1"] and l2 == cur["l2"] else "")
        if l3 and (not cur["l3"] or l3 != cur["l3"] or l2 != cur["l2"]):
            cur = {"l1": l1, "l2": l2 or l1, "l3": l3}
            out.append({"l1": cur["l1"], "l2": cur["l2"], "l3": cur["l3"]})
    _put("catalog", out)
    return out


def write_catalog_template(data_dir: str) -> Dict[str, Any]:
    """用中证分类目录生成待填写的映射 CSV（stock 列留空，由用户填写）。"""
    catalog = read_catalog()
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, FILENAME)
    back = path + ".bak"
    if os.path.exists(path):
        try:
            os.replace(path, back)
        except Exception:
            pass
    lines = [
        "# 由 data/中证行业分类架构.xlsx 生成：请把每行的 code（股票代码）填上",
        "# 同一三级行业有多只股票时，复制该行并填不同 code；name 可留空",
        "code,name,level1,level2,level3",
    ]
    for item in catalog:
        lines.append(f",,{item['l1']},{item['l2']},{item['l3']}")
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write("\n".join(lines) + "\n")
    return {"path": path, "rows": len(catalog), "backup": back if os.path.exists(back) else None}
