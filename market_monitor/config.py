"""资产目录：定义监测范围、各数据源代码映射与历史数据来源。

所有代码均经实测验证可用（2026-10 环境）：
  * 新浪  hq.sinajs.cn    —— A股/港股/美股/期货/汇率/美元指数 实时快照（GBK）
  * 腾讯  qt.gtimg.cn     —— 美股/港股/ETF 实时快照 + K 线（web.ifzq.gtimg.cn）
  * CNBC  quote.cnbc.com  —— 美债/中债收益率
  * 新浪期货接口          —— 国内/国际期货日线
  * frankfurter.app       —— 汇率历史（欧洲央行日频）
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class Asset:
    id: str
    name: str
    group: str
    kind: str = "index"                      # index | stock | etf | future | bond | fx | spread
    sina: Optional[str] = None               # 新浪代码
    tencent: Optional[str] = None            # 腾讯代码
    cnbc: Optional[str] = None               # CNBC 代码（债券收益率）
    em: Optional[str] = None                 # 东方财富字段（中债收益率曲线）
    hist: Optional[Tuple[str, str]] = None   # (来源, 代码)
    prefer: str = "sina"                     # 实时行情优先源：sina / tencent
    decimals: int = 2
    unit: str = ""
    tag: str = ""                            # 卡片右上角标签：CN / HK / US
    hero: bool = False                       # 是否进入顶部大卡与跑马灯
    derived: Optional[Tuple[str, str]] = None  # 派生指标（A - B）

    @property
    def is_derived(self) -> bool:
        return self.derived is not None

    @property
    def currency(self) -> Optional[str]:
        """计价货币：CNY / USD / HKD；利率与汇率没有货币单位。"""
        if self.group in ("利率", "汇率"):
            return None
        if self.group == "港股":
            return "HKD"
        if self.group == "美股":
            return "USD"
        if self.group == "ETF":
            return "USD" if self.tag == "US" else "CNY"
        if self.group == "期货":
            return "USD" if self.tag == "INT" else "CNY"
        return "CNY"


def A(
    id: str,
    name: str,
    group: str,
    kind: str = "index",
    sina: Optional[str] = None,
    tencent: Optional[str] = None,
    cnbc: Optional[str] = None,
    em: Optional[str] = None,
    hist: Optional[Tuple[str, str]] = None,
    prefer: str = "sina",
    decimals: int = 2,
    unit: str = "",
    tag: str = "",
    hero: bool = False,
    derived: Optional[Tuple[str, str]] = None,
) -> Asset:
    return Asset(
        id=id, name=name, group=group, kind=kind, sina=sina, tencent=tencent,
        cnbc=cnbc, em=em, hist=hist, prefer=prefer, decimals=decimals, unit=unit,
        tag=tag, hero=hero, derived=derived,
    )


GROUPS: List[str] = ["A股", "港股", "美股", "ETF", "期货", "利率", "汇率"]

GROUP_ICON: Dict[str, str] = {
    "A股": "CN", "港股": "HK", "美股": "US",
    "ETF": "ETP", "期货": "FUT", "利率": "RATE", "汇率": "FX",
}

ASSETS: List[Asset] = [
    # ── A 股核心指数 ────────────────────────────────────────────────
    A("sh000001", "上证指数", "A股", sina="sh000001", tencent="sh000001",
      hist=("tencent", "sh000001"), tag="CN", hero=True),
    A("sz399001", "深证成指", "A股", sina="sz399001", tencent="sz399001",
      hist=("tencent", "sz399001"), tag="CN"),
    A("sz399006", "创业板指", "A股", sina="sz399006", tencent="sz399006",
      hist=("tencent", "sz399006"), tag="CN"),
    A("sh000688", "科创50", "A股", sina="sh000688", tencent="sh000688",
      hist=("tencent", "sh000688"), tag="CN"),
    A("sh000300", "沪深300", "A股", sina="sh000300", tencent="sh000300",
      hist=("tencent", "sh000300"), tag="CN"),
    A("sh000905", "中证500", "A股", sina="sh000905", tencent="sh000905",
      hist=("tencent", "sh000905"), tag="CN"),
    A("sh000852", "中证1000", "A股", sina="sh000852", tencent="sh000852",
      hist=("tencent", "sh000852"), tag="CN"),
    A("sh000016", "上证50", "A股", sina="sh000016", tencent="sh000016",
      hist=("tencent", "sh000016"), tag="CN"),
    A("bj899050", "北证50", "A股", sina="bj899050", tencent="bj899050",
      hist=("tencent", "bj899050"), tag="CN"),

    # ── 港股 ────────────────────────────────────────────────────────
    A("hk.HSI", "恒生指数", "港股", sina="rt_hkHSI", tencent="hkHSI",
      hist=("tencent", "hkHSI"), tag="HK", hero=True),
    A("hk.HSTECH", "恒生科技", "港股", sina="rt_hkHSTECH", tencent="hkHSTECH",
      hist=("tencent", "hkHSTECH"), tag="HK"),
    A("hk.HSCEI", "国企指数", "港股", sina="rt_hkHSCEI", tencent="hkHSCEI",
      hist=("tencent", "hkHSCEI"), tag="HK"),
    A("hk.00700", "腾讯控股", "港股", kind="stock", sina="rt_hk00700", tencent="hk00700",
      hist=("tencent", "hk00700"), tag="HK"),

    # ── 美股 ────────────────────────────────────────────────────────
    A("us.DJI", "道琼斯", "美股", sina="gb_$dji", tencent="us.DJI",
      hist=("tencent", "us.DJI"), prefer="tencent", tag="US"),
    A("us.IXIC", "纳斯达克", "美股", sina="gb_ixic", tencent="us.IXIC",
      hist=("tencent", "us.IXIC"), prefer="tencent", tag="US", hero=True),
    A("us.INX", "标普500", "美股", sina="gb_inx", tencent="us.INX",
      hist=("tencent", "us.INX"), prefer="tencent", tag="US", hero=True),
    A("us.VIX", "VIX 波动率", "美股", kind="index", sina="gb_$vix", tencent="us.VIX", cnbc=".VIX",
      hist=("cnbc_chart", ".VIX"), prefer="tencent", tag="US"),
    A("us.AAPL", "苹果", "美股", kind="stock", sina="gb_aapl", tencent="usAAPL",
      hist=("sina_us", "AAPL"), prefer="tencent", tag="US"),
    A("us.NVDA", "英伟达", "美股", kind="stock", sina="gb_nvda", tencent="usNVDA",
      hist=("sina_us", "NVDA"), prefer="tencent", tag="US"),
    A("us.MSFT", "微软", "美股", kind="stock", sina="gb_msft", tencent="usMSFT",
      hist=("sina_us", "MSFT"), prefer="tencent", tag="US"),
    A("us.TSLA", "特斯拉", "美股", kind="stock", sina="gb_tsla", tencent="usTSLA",
      hist=("sina_us", "TSLA"), prefer="tencent", tag="US"),
    A("us.GOOGL", "谷歌", "美股", kind="stock", sina="gb_googl", tencent="usGOOGL",
      hist=("sina_us", "GOOGL"), prefer="tencent", tag="US"),
    A("us.AMZN", "亚马逊", "美股", kind="stock", sina="gb_amzn", tencent="usAMZN",
      hist=("sina_us", "AMZN"), prefer="tencent", tag="US"),
    A("us.META", "Meta", "美股", kind="stock", sina="gb_meta", tencent="usMETA",
      hist=("sina_us", "META"), prefer="tencent", tag="US"),

    # ── 核心 ETF ────────────────────────────────────────────────────
    A("sh510300", "沪深300ETF", "ETF", kind="etf", sina="sh510300", tencent="sh510300",
      hist=("tencent", "sh510300"), decimals=3, tag="CN"),
    A("sh510500", "中证500ETF", "ETF", kind="etf", sina="sh510500", tencent="sh510500",
      hist=("tencent", "sh510500"), decimals=3, tag="CN"),
    A("sh588000", "科创50ETF", "ETF", kind="etf", sina="sh588000", tencent="sh588000",
      hist=("tencent", "sh588000"), decimals=3, tag="CN"),
    A("sz159915", "创业板ETF", "ETF", kind="etf", sina="sz159915", tencent="sz159915",
      hist=("tencent", "sz159915"), decimals=3, tag="CN"),
    A("sh512880", "证券ETF", "ETF", kind="etf", sina="sh512880", tencent="sh512880",
      hist=("tencent", "sh512880"), decimals=3, tag="CN"),
    A("sh518880", "黄金ETF", "ETF", kind="etf", sina="sh518880", tencent="sh518880",
      hist=("tencent", "sh518880"), decimals=3, tag="CN"),
    A("sh513100", "纳指ETF", "ETF", kind="etf", sina="sh513100", tencent="sh513100",
      hist=("tencent", "sh513100"), decimals=3, tag="CN"),
    A("sh513500", "标普500ETF", "ETF", kind="etf", sina="sh513500", tencent="sh513500",
      hist=("tencent", "sh513500"), decimals=3, tag="CN"),
    A("sh513180", "恒生科技ETF", "ETF", kind="etf", sina="sh513180", tencent="sh513180",
      hist=("tencent", "sh513180"), decimals=3, tag="CN"),
    A("us.SPY", "SPY 标普500ETF", "ETF", kind="etf", sina="gb_spy", tencent="usSPY",
      hist=("sina_us", "SPY"), prefer="tencent", tag="US"),
    A("us.QQQ", "QQQ 纳指100ETF", "ETF", kind="etf", sina="gb_qqq", tencent="usQQQ",
      hist=("sina_us", "QQQ"), prefer="tencent", tag="US"),
    A("us.TLT", "TLT 20年美债ETF", "ETF", kind="etf", sina="gb_tlt", tencent="usTLT",
      hist=("sina_us", "TLT"), prefer="tencent", tag="US"),
    A("sh511260", "十年国债ETF", "ETF", kind="etf", sina="sh511260", tencent="sh511260",
      hist=("tencent", "sh511260"), decimals=3, tag="CN"),
    A("sh511010", "国债ETF（5年）", "ETF", kind="etf", sina="sh511010", tencent="sh511010",
      hist=("tencent", "sh511010"), decimals=3, tag="CN"),
    A("sh511090", "30年国债ETF", "ETF", kind="etf", sina="sh511090", tencent="sh511090",
      hist=("tencent", "sh511090"), decimals=3, tag="CN"),

    # ── 期货 ────────────────────────────────────────────────────────
    A("nf.IF0", "沪深300股指", "期货", kind="future", sina="nf_IF0", hist=("sina_inner", "IF0"), tag="CN"),
    A("nf.IH0", "上证50股指", "期货", kind="future", sina="nf_IH0", hist=("sina_inner", "IH0"), tag="CN"),
    A("nf.IC0", "中证500股指", "期货", kind="future", sina="nf_IC0", hist=("sina_inner", "IC0"), tag="CN"),
    A("nf.IM0", "中证1000股指", "期货", kind="future", sina="nf_IM0", hist=("sina_inner", "IM0"), tag="CN"),
    A("nf.RB0", "螺纹钢", "期货", kind="future", sina="nf_RB0", hist=("sina_inner", "RB0"), tag="CN"),
    A("nf.CU0", "沪铜", "期货", kind="future", sina="nf_CU0", hist=("sina_inner", "CU0"), tag="CN"),
    A("nf.AU0", "沪金", "期货", kind="future", sina="nf_AU0", hist=("sina_inner", "AU0"), tag="CN"),
    A("nf.SC0", "上海原油", "期货", kind="future", sina="nf_SC0", hist=("sina_inner", "SC0"), tag="CN"),
    A("hf.CL", "WTI 原油", "期货", kind="future", sina="hf_CL", tencent="hf_CL",
      hist=("sina_global", "CL"), tag="INT"),
    A("hf.GC", "COMEX 黄金", "期货", kind="future", sina="hf_GC", tencent="hf_GC",
      hist=("sina_global", "GC"), tag="INT"),
    A("hf.SI", "COMEX 白银", "期货", kind="future", sina="hf_SI", tencent="hf_SI",
      hist=("sina_global", "SI"), tag="INT"),
    A("hf.HG", "COMEX 铜", "期货", kind="future", sina="hf_HG", tencent="hf_HG",
      hist=("sina_global", "HG"), tag="INT"),
    A("hf.NG", "NYMEX 天然气", "期货", kind="future", sina="hf_NG", tencent="hf_NG",
      hist=("sina_global", "NG"), tag="INT"),
    A("hf.CHA50", "富时中国A50", "期货", kind="future", sina="hf_CHA50CFD", tencent="hf_CHA50CFD",
      hist=("sina_global", "CHA50CFD"), tag="INT"),
    A("nf.T0", "10年国债期货", "期货", kind="future", sina="nf_T0",
      hist=("sina_inner", "T0"), tag="CN"),
    A("nf.TF0", "5年国债期货", "期货", kind="future", sina="nf_TF0",
      hist=("sina_inner", "TF0"), tag="CN"),
    A("nf.TS0", "2年国债期货", "期货", kind="future", sina="nf_TS0",
      hist=("sina_inner", "TS0"), tag="CN"),

    # ── 利率 / 国债收益率 ───────────────────────────────────────────
    A("bond.US10Y", "美债 10Y", "利率", kind="bond", cnbc="US10Y", decimals=3, unit="%",
      hist=("ustreasury", "10 Yr"), tag="US", hero=True),
    A("bond.US2Y", "美债 2Y", "利率", kind="bond", cnbc="US2Y", decimals=3, unit="%",
      hist=("ustreasury", "2 Yr"), tag="US"),
    A("bond.US5Y", "美债 5Y", "利率", kind="bond", cnbc="US5Y", decimals=3, unit="%",
      hist=("ustreasury", "5 Yr"), tag="US"),
    A("bond.US30Y", "美债 30Y", "利率", kind="bond", cnbc="US30Y", decimals=3, unit="%",
      hist=("ustreasury", "30 Yr"), tag="US"),
    A("bond.CN2Y", "中债 2Y", "利率", kind="bond", cnbc="CN2Y", em="EMM00588704", decimals=3, unit="%",
      hist=("em_cn_yield", "EMM00588704"), tag="CN"),
    A("bond.CN5Y", "中债 5Y", "利率", kind="bond", em="EMM00166462", decimals=3, unit="%",
      hist=("em_cn_yield", "EMM00166462"), tag="CN"),
    A("bond.CN10Y", "中债 10Y", "利率", kind="bond", cnbc="CN10Y", em="EMM00166466", decimals=3, unit="%",
      hist=("em_cn_yield", "EMM00166466"), tag="CN"),
    A("bond.CN30Y", "中债 30Y", "利率", kind="bond", em="EMM00166469", decimals=3, unit="%",
      hist=("em_cn_yield", "EMM00166469"), tag="CN"),
    A("spread.10Y2Y", "美债 10Y-2Y", "利率", kind="spread", decimals=3, unit="%",
      derived=("bond.US10Y", "bond.US2Y"), tag="US"),
    A("spread.CN10Y2Y", "中债 10Y-2Y", "利率", kind="spread", decimals=3, unit="%",
      derived=("bond.CN10Y", "bond.CN2Y"), tag="CN"),

    # ── 汇率 ────────────────────────────────────────────────────────
    A("fx.USDCNY", "美元/人民币", "汇率", kind="fx", sina="fx_susdcny",
      hist=("frankfurter", "USD/CNY"), decimals=4, tag="CNY"),
    A("fx.USDCNH", "美元/离岸人民币", "汇率", kind="fx", sina="fx_susdcnh",
      hist=("cnbc_chart", "CNH="), decimals=4, tag="CNH"),
    A("fx.USDJPY", "美元/日元", "汇率", kind="fx", sina="fx_susdjpy",
      hist=("frankfurter", "USD/JPY"), decimals=2, tag="JPY"),
    A("fx.EURCNY", "欧元/人民币", "汇率", kind="fx", sina="fx_seurcny",
      hist=("frankfurter", "EUR/CNY"), decimals=4, tag="EUR"),
    A("fx.GBPCNY", "英镑/人民币", "汇率", kind="fx", sina="fx_sgbpcny",
      hist=("frankfurter", "GBP/CNY"), decimals=4, tag="GBP"),
    A("fx.USDHKD", "美元/港元", "汇率", kind="fx", sina="fx_susdhkd",
      hist=("frankfurter", "USD/HKD"), decimals=4, tag="HKD"),
    A("fx.AUDCNY", "澳元/人民币", "汇率", kind="fx", sina="fx_saudcny",
      hist=("frankfurter", "AUD/CNY"), decimals=4, tag="AUD"),
    A("fx.DXY", "美元指数", "汇率", kind="fx", sina="DINIW",
      hist=("cnbc_chart", ".DXY"), decimals=3, tag="DXY", hero=True),
]

BY_ID: Dict[str, Asset] = {a.id: a for a in ASSETS}
HERO_IDS: List[str] = [a.id for a in ASSETS if a.hero]


def assets_of(group: str) -> List[Asset]:
    return [a for a in ASSETS if a.group == group]


def validate_catalog() -> List[str]:
    """返回目录中的一致性告警（重复 id、缺失数据源、未知分组）。"""
    warns: List[str] = []
    seen = set()
    for a in ASSETS:
        if a.id in seen:
            warns.append(f"重复 id: {a.id}")
        seen.add(a.id)
        if a.group not in GROUPS:
            warns.append(f"未知分组: {a.id} -> {a.group}")
        if not any([a.sina, a.tencent, a.cnbc, a.em, a.derived]):
            warns.append(f"无数据源代码: {a.id}")
        if a.hist and a.hist[0] not in ("tencent", "sina_us", "sina_inner", "sina_global",
                                        "frankfurter", "ustreasury", "cnbc_chart",
                                        "em_cn_yield", "snapshot"):
            warns.append(f"未知历史源: {a.id} -> {a.hist[0]}")
        if a.derived and any(x not in {b.id for b in ASSETS} for x in a.derived):
            warns.append(f"派生指标引用缺失: {a.id}")
    return warns
