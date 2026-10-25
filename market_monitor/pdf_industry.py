"""从证监会《上市公司行业分类结果》PDF 生成行业映射。

PDF 表格列：上市公司代码 / 简称 / 门类代码 / 门类简称 / 次类代码 / 次类简称 / 大类代码 / 大类简称
映射到 industry_map.csv：code,name,level1=门类,level2=次类(无则大类),level3=大类
"""
from __future__ import annotations

import csv
import os
from typing import Any, Dict, List, Optional

from .industry_map import FILENAME

PAGE_TITLE = "上市公司行业分类结果"


def _clean(text: Optional[str]) -> str:
    return (text or "").replace("\n", "").replace("\r", "").strip()


def parse_pdf(path: str) -> List[Dict[str, str]]:
    try:
        import pdfplumber  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("需要 pdfplumber：pip install pdfplumber") from exc

    rows: List[Dict[str, str]] = []
    seen = set()
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                for raw in table:
                    if not raw or len(raw) < 8:
                        continue
                    code = _clean(raw[0])
                    if not code.isdigit() or len(code) != 6:
                        continue
                    if code in seen:
                        continue
                    seen.add(code)
                    l1 = _clean(raw[3])
                    l2 = _clean(raw[5])
                    l3 = _clean(raw[7])
                    if not l1:
                        continue
                    if not l2:
                        l2, l3 = l3, ""
                    rows.append({
                        "code": code,
                        "name": _clean(raw[1]),
                        "level1": l1,
                        "level2": l2,
                        "level3": l3,
                    })
    return rows


def write_mapping(rows: List[Dict[str, str]], data_dir: str) -> str:
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, FILENAME)
    backup = path + ".bak"
    if os.path.exists(path):
        try:
            os.replace(path, backup)
        except Exception:
            pass
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write("# 由证监会《上市公司行业分类结果》PDF 生成\n")
        fh.write("# 列：code,name,level1(门类),level2(次类),level3(大类)\n")
        writer = csv.DictWriter(fh, fieldnames=["code", "name", "level1", "level2", "level3"])
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
    return path


def summary(rows: List[Dict[str, str]]) -> Dict[str, Any]:
    l1 = {r["level1"] for r in rows}
    l2 = {(r["level1"], r["level2"]) for r in rows}
    l3 = {(r["level1"], r["level2"], r["level3"]) for r in rows if r["level3"]}
    return {"stocks": len(rows), "level1": len(l1), "level2": len(l2), "level3": len(l3)}
