"""数据来源登记表：名称 + 官方地址，用于页面展示与跳转。"""
from __future__ import annotations

from typing import Any, Dict

# key 与行情 source 字段、各视图的数据来源保持一致
MAP: Dict[str, Dict[str, str]] = {
    "sina": {"name": "新浪财经", "url": "https://finance.sina.com.cn"},
    "tencent": {"name": "腾讯行情", "url": "https://gu.qq.com"},
    "cnbc": {"name": "CNBC", "url": "https://www.cnbc.com/quotes/"},
    "eastmoney": {"name": "东方财富", "url": "https://quote.eastmoney.com"},
    "derived": {"name": "本地计算", "url": ""},
    "treasury": {"name": "美国财政部 FiscalData", "url": "https://fiscaldata.treasury.gov"},
    "worldbank": {"name": "世界银行", "url": "https://data.worldbank.org"},
    "nasdaq": {"name": "纳斯达克", "url": "https://www.nasdaq.com/market-activity/stocks"},
    "sge": {"name": "上海黄金交易所", "url": "https://www.sge.com.cn"},
    "lbma": {"name": "LBMA（伦敦金）", "url": "https://www.lbma.org.uk/prices-and-data"},
    "cme": {"name": "CME / COMEX", "url": "https://www.cmegroup.com/markets/metals/precious/gold.html"},
    "bcb": {"name": "巴西央行", "url": "https://www.bcb.gov.br"},
    "cboe": {"name": "CBOE", "url": "https://www.cboe.com"},
    "frankfurter": {"name": "欧洲央行汇率", "url": "https://www.frankfurter.app"},
}

# 页面底部按这个顺序展示
ORDER = ["sina", "tencent", "cnbc", "eastmoney", "treasury", "worldbank", "nasdaq", "sge", "lbma", "cme", "bcb", "cboe", "frankfurter"]


def for_client() -> Dict[str, Any]:
    return {"map": MAP, "order": ORDER}
