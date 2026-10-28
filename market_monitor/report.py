"""每日报告：把当天各市场的主要变化整理成一篇可读的文字报告。"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from . import config, sectors

# 报告里引用的关键标的（缺失时自动跳过）
INDEX_CN = [
    ("sh000001", "上证指数"),
    ("sz399001", "深证成指"),
    ("sz399006", "创业板指"),
    ("sh000688", "科创 50"),
    ("sh000300", "沪深 300"),
]
INDEX_HK = [("hk.HSI", "恒生指数"), ("hk.HSCEI", "国企指数"), ("hk.HSTECH", "恒生科技")]
INDEX_US = [("us.DJI", "道琼斯"), ("us.IXIC", "纳斯达克"), ("us.INX", "标普 500"), ("us.VIX", "VIX 波动率")]
BONDS = [
    ("bond.US10Y", "美债 10Y"),
    ("bond.US2Y", "美债 2Y"),
    ("spread.10Y2Y", "美债 10Y-2Y 利差"),
    ("bond.CN10Y", "中国 10Y"),
    ("bond.CN2Y", "中国 2Y"),
    ("spread.CN10Y2Y", "中国 10Y-2Y 利差"),
]
COMMODITY = [
    ("hf.GC", "COMEX 黄金"),
    ("hf.SI", "COMEX 白银"),
    ("hf.CL", "WTI 原油"),
    ("hf.HG", "COMEX 铜"),
    ("nf.RB0", "螺纹钢"),
]
FX = [("fx.DXY", "美元指数"), ("fx.USDCNY", "美元/人民币"), ("fx.USDJPY", "美元/日元"), ("fx.EURCNY", "欧元/人民币")]

_CROSS_BORDER = ("港股", "香港", "恒生", "恒指", "纳指", "纳斯达克", "标普", "日经", "美国", "德国", "法国", "中概", "亚太", "全球", "海外", "港", "HK", "中韩")


def _pct(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return f"{value:+.2f}%"


def _fmt_price(value: Optional[float], digits: int = 2) -> str:
    if value is None:
        return "—"
    if abs(value) >= 10000:
        return f"{value:,.0f}"
    return f"{value:,.{digits}f}"


def _money(value: Optional[float]) -> str:
    if not value:
        return "—"
    if abs(value) >= 1e12:
        return f"{value / 1e12:.2f} 万亿"
    if abs(value) >= 1e8:
        return f"{value / 1e8:.0f} 亿"
    return f"{value:,.0f}"


def _direction(pct: Optional[float]) -> str:
    if pct is None:
        return "持平"
    if pct >= 2:
        return "大涨"
    if pct >= 0.5:
        return "上涨"
    if pct > -0.5:
        return "窄幅震荡"
    if pct > -2:
        return "下跌"
    return "大跌"


def _quote_lines(quotes: Dict[str, Dict[str, Any]], items: List[tuple], digits: int = 2) -> List[str]:
    out: List[str] = []
    for aid, label in items:
        q = quotes.get(aid) or {}
        price = q.get("price")
        pct = q.get("pct")
        if price is None and pct is None:
            continue
        if aid.startswith("spread."):
            out.append(f"{label} {_fmt_price(price, digits)}")
        else:
            out.append(f"{label} {_fmt_price(price, digits)}（{_pct(pct)}）")
    return out



def _breadth(quotes: Dict[str, Dict[str, Any]]) -> str:
    """已配置标的里的涨跌家数，粗略反映市场温度。"""
    up = down = flat = 0
    for q in quotes.values():
        pct = q.get("pct")
        if not isinstance(pct, (int, float)):
            continue
        if pct > 0.05:
            up += 1
        elif pct < -0.05:
            down += 1
        else:
            flat += 1
    if not (up or down):
        return ""
    return f"本程序跟踪的 {up + down + flat} 个标的中，{up} 个上涨、{down} 个下跌、{flat} 个基本持平。"


def build(service: Any) -> Dict[str, Any]:
    """生成当日报告：返回 {date, generated_at, title, lead, sections, text}。"""
    quotes = service.quote_map()
    today = time.strftime("%Y-%m-%d")
    stamp = time.strftime("%Y-%m-%d %H:%M")

    cn = _quote_lines(quotes, INDEX_CN)
    hk = _quote_lines(quotes, INDEX_HK)
    us = _quote_lines(quotes, INDEX_US)
    bonds = _quote_lines(quotes, BONDS)
    comm = _quote_lines(quotes, COMMODITY)
    fx = _quote_lines(quotes, FX, digits=3)

    # A 股涨跌家数与成交额（按已选中的指数口径给出代表性描述）
    sh = quotes.get("sh000001") or {}
    sh_pct = sh.get("pct")
    sh_amount = sh.get("amount")

    # 行业强弱
    gainers: List[str] = []
    losers: List[str] = []
    try:
        inds = [i for i in sectors.industries() if i.get("count")]
        inds.sort(key=lambda x: x.get("pct") or 0, reverse=True)
        gainers = [f"{i['name']} {_pct(i.get('pct'))}" for i in inds[:3]]
        losers = [f"{i['name']} {_pct(i.get('pct'))}" for i in inds[-3:][::-1]]
        sector_note = f"全部 {len(inds)} 个新浪行业中，{sum(1 for i in inds if (i.get('pct') or 0) > 0)} 个上涨。"
    except Exception:
        sector_note = ""

    # 市场资金流
    flow_note = ""
    try:
        flow = service.market_flow(limit=0)
        groups = flow.get("groups") or []
        latest = []
        for g in groups:
            name = g.get("group")
            val = g.get("latest")
            if name and isinstance(val, (int, float)):
                latest.append((name, float(val)))
        latest.sort(key=lambda x: x[1], reverse=True)
        if latest:
            top = "、".join(f"{n} {v:+.1f}" for n, v in latest[:3])
            bottom = "、".join(f"{n} {v:+.1f}" for n, v in latest[-2:])
            flow_note = f"按净流入占比，资金相对偏向 {top}；相对偏弱的是 {bottom}（单位：%，正值表示净流入）。"
    except Exception:
        pass

    sections: List[Dict[str, Any]] = []

    # 概览
    lead_parts = []
    if sh_pct is not None:
        lead_parts.append(f"A 股{_direction(sh_pct)}（上证 {_pct(sh_pct)}）")
    for aid, label in (("hk.HSI", "恒生"), ("us.INX", "标普")):
        q = quotes.get(aid) or {}
        if q.get("pct") is not None:
            lead_parts.append(f"{label}{_direction(q['pct'])}（{_pct(q['pct'])}）")
    lead = "，".join(lead_parts) + "。" if lead_parts else "今日行情数据尚未就绪。"

    overview = []
    if sh.get("price") is not None:
        overview.append(f"上证指数收于 {_fmt_price(sh.get('price'))} 点，成交额 {_money(sh_amount)}。")
    if sh_pct is not None:
        stats = _breadth(quotes)
        if stats:
            overview.append(stats)
    sections.append({"h": "一、总体概览", "p": overview or ["今日行情数据尚未就绪。"]})

    if cn:
        sections.append({"h": "二、A 股", "p": [
            "、".join(cn) + "。",
            (f"行业层面：领涨 {('、'.join(gainers))}；领跌 {('、'.join(losers))}。{sector_note}"
             if gainers else ""),
        ]})

    if hk:
        sections.append({"h": "三、港股", "p": ["、".join(hk) + "。"]})
    if us:
        sections.append({"h": "四、美股", "p": ["、".join(us) + "。"]})
    if bonds:
        sections.append({"h": "五、利率与债券", "p": ["、".join(bonds) + "。"]})
    if comm:
        sections.append({"h": "六、商品与黄金", "p": ["、".join(comm) + "。"]})
    if fx:
        sections.append({"h": "七、汇率", "p": ["、".join(fx) + "。"]})
    if flow_note:
        sections.append({"h": "八、资金流向", "p": [flow_note]})

    watch: List[str] = []
    vix = (quotes.get("us.VIX") or {}).get("pct")
    if isinstance(vix, (int, float)) and vix > 10:
        watch.append("VIX 明显抬升，风险偏好回落，注意仓位。")
    if isinstance(sh_pct, (int, float)) and abs(sh_pct) >= 2:
        watch.append("A 股单日波动超过 2%，次日情绪容易惯性延续。")
    if not watch:
        watch.append("主要市场波动温和，暂无突出风险信号。")
    sections.append({"h": "九、关注点", "p": watch})

    lines = [f"# 资本市场日报 · {today}", ""]
    for sec in sections:
        lines.append(f"## {sec['h']}")
        for para in sec["p"]:
            if para:
                lines.append(para)
        lines.append("")

    lines.append("---")
    lines.append("数据来自公开渠道，由程序自动汇总，仅供参考，不构成投资建议。")

    return {
        "ok": True,
        "date": today,
        "generated_at": stamp,
        "title": f"资本市场日报 · {today}",
        "lead": lead,
        "sections": [{"h": s["h"], "p": [x for x in s["p"] if x]} for s in sections],
        "text": "\n".join(lines),
        "coverage": {
            "cn": len(cn), "hk": len(hk), "us": len(us),
            "bonds": len(bonds), "commodity": len(comm), "fx": len(fx),
        },
    }
