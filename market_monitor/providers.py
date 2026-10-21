"""行情数据源适配层。

每个 fetch_* 返回 {asset_id: quote_dict}，quote_dict 字段：
    price, prev_close, open, high, low, change, pct, volume, amount, ts, source

历史数据统一返回 [[date, open, high, low, close, volume], ...]（升序）。
"""
from __future__ import annotations

import csv
import io
import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .config import Asset
from .net import http

SINA_URL = "https://hq.sinajs.cn/list="
SINA_HEADERS = {"Referer": "https://finance.sina.com.cn"}
TENCENT_URL = "https://qt.gtimg.cn/q="
CNBC_URL = "https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol"

_RE_LINE = re.compile(r'var\s+hq_str_([^=]+)="([^"]*)"')
_RE_TENCENT_LINE = re.compile(r'v_([^=]+)="([^"]*)"')
_RE_JSONP = re.compile(r"\((\[.*\])\)\s*;?\s*$", re.S)
_RE_DATE_CN = re.compile(r"^\d{4}[-/]\d{2}[-/]\d{2}$")
_RE_TIME = re.compile(r"^\d{2}:\d{2}:\d{2}$")


# ────────────────────────────────────────────────────────────── 工具函数
def _f(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("%", "").replace("+", "")
    if not text or text in ("-", "--", "null", "None"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _norm_ts(*parts: Optional[str]) -> Optional[str]:
    """把各源的时间片段拼成 'YYYY-MM-DD HH:MM:SS'。"""
    date_part = time_part = None
    for p in parts:
        if not p:
            continue
        p = str(p).strip()
        if _RE_DATE_CN.match(p):
            date_part = p.replace("/", "-")
        elif _RE_TIME.match(p):
            time_part = p
        elif len(p) >= 16 and p[4] in "-/" and " " in p:
            return p.replace("/", "-")[:19]
        elif len(p) == 14 and p.isdigit():  # 腾讯紧凑格式 20261021154042
            return f"{p[0:4]}-{p[4:6]}-{p[6:8]} {p[8:10]}:{p[10:12]}:{p[12:14]}"
        elif len(p) == 8 and p.isdigit():   # 20261021
            date_part = f"{p[0:4]}-{p[4:6]}-{p[6:8]}"
    if date_part and time_part:
        return f"{date_part} {time_part}"
    if date_part:
        return f"{date_part} 00:00:00"
    return None


def _ts_from_fields(fields: Sequence[str]) -> Optional[str]:
    """从字段里倒着找日期与时间（港股字段位置会变，逐项扫描更稳）。"""
    date_part = None
    time_part = None
    for raw in reversed(list(fields)):
        text = str(raw).strip()
        if not text:
            continue
        if date_part is None and _RE_DATE_CN.match(text):
            date_part = text
        elif time_part is None and _RE_TIME.match(text):
            time_part = text
        if date_part and time_part:
            break
    return _norm_ts(date_part, time_part)


def _finalize(q: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    price = q.get("price")
    if price is None:
        return None
    prev = q.get("prev_close")
    change = q.get("change")
    pct = q.get("pct")
    if change is None and prev not in (None, 0):
        change = price - prev
    if pct is None and prev not in (None, 0) and change is not None:
        pct = change / prev * 100.0
    q["change"] = change
    q["pct"] = pct
    if not q.get("ts"):
        q["ts"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return q


def _chunks(items: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _jsonp(text: str) -> Any:
    text = text.strip()
    if text.startswith("/*"):
        idx = text.find("*/")
        if idx > 0:
            text = text[idx + 2 :]
    m = _RE_JSONP.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


_RE_CNBC_TS = re.compile(r"(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})(?:\.\d+)?([+-]\d{2}:?\d{2})")


def _cnbc_ts(raw: str) -> Optional[str]:
    """CNBC 时间带美东偏移，统一换算成北京时间。"""
    if not raw:
        return None
    m = _RE_CNBC_TS.search(str(raw))
    if not m:
        return str(raw).replace("T", " ")[:19] or None
    offset = m.group(3).replace(":", "")
    try:
        dt = datetime.strptime(f"{m.group(1)} {m.group(2)} {offset}", "%Y-%m-%d %H:%M:%S %z")
    except ValueError:
        return str(raw).replace("T", " ")[:19] or None
    return dt.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")


# ────────────────────────────────────────────────────────────── 新浪实时
def _parse_sina(sina_code: str, raw: str) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    f = raw.split(",")
    if len(f) < 6:
        return None

    # A 股 / ETF：名称,开,昨收,现价,高,低,...,成交量,成交额,...,日期,时间
    if sina_code.startswith(("sh", "sz", "bj")):
        if len(f) < 32:
            return None
        return _finalize({
            "price": _f(f[3]), "prev_close": _f(f[2]), "open": _f(f[1]),
            "high": _f(f[4]), "low": _f(f[5]),
            "volume": _f(f[8]), "amount": _f(f[9]),
            "ts": _norm_ts(f[30], f[31]), "name": f[0],
        })

    # 港股：代码,名称,开,昨收,高,低,现价,涨跌,涨跌幅,...,成交额,成交量,...,日期,时间
    if sina_code.startswith("rt_hk"):
        return _finalize({
            "price": _f(f[6]), "prev_close": _f(f[3]), "open": _f(f[2]),
            "high": _f(f[4]), "low": _f(f[5]),
            "change": _f(f[7]), "pct": _f(f[8]),
            "amount": _f(f[11]), "volume": _f(f[12]),
            "ts": _ts_from_fields(f), "name": f[1],
        })

    # 美股：名称,现价,涨跌幅,时间,涨跌额,开,高,低,52周高,52周低,成交量
    if sina_code.startswith("gb_"):
        return _finalize({
            "price": _f(f[1]), "pct": _f(f[2]), "change": _f(f[4]),
            "open": _f(f[5]), "high": _f(f[6]), "low": _f(f[7]),
            "volume": _f(f[10]), "ts": _norm_ts(f[3]), "name": f[0],
        })

    # 汇率 / 美元指数：时间,买,卖,?,?,昨收,高,低,现价,名称,...,日期
    if sina_code.startswith("fx_") or sina_code == "DINIW":
        if not _RE_TIME.match(f[0].strip()):
            return None
        price = _f(f[8]) or _f(f[1])
        prev = _f(f[5])
        ts = _norm_ts(f[0], f[10] if len(f) > 10 else None)
        if ts is None:
            ts = _norm_ts(datetime.now().strftime("%Y-%m-%d"), f[0])
        return _finalize({
            "price": price, "prev_close": prev,
            "high": _f(f[6]), "low": _f(f[7]),
            "ts": ts, "name": f[9] if len(f) > 9 else "",
        })

    # 国际期货：现价,,买,卖,高,低,时间,昨收,开,...,日期,名称
    if sina_code.startswith("hf_"):
        return _finalize({
            "price": _f(f[0]), "prev_close": _f(f[7]), "open": _f(f[8]),
            "high": _f(f[4]), "low": _f(f[5]),
            "ts": _norm_ts(f[12], f[6]), "name": f[13] if len(f) > 13 else "",
        })

    # 国内期货：两种版式
    if sina_code.startswith("nf_"):
        if _f(f[0]) is None:  # 商品：名称,时间HHMMSS,开,高,低,,买,卖,现价,,昨结,...,持仓,成交量,交易所,品种,日期
            if len(f) < 18:
                return None
            hhmmss = str(f[1]).strip().zfill(6)
            return _finalize({
                "price": _f(f[8]), "prev_close": _f(f[10]), "open": _f(f[2]),
                "high": _f(f[3]), "low": _f(f[4]),
                "volume": _f(f[14]), "ts": _norm_ts(f[17], f"{hhmmss[0:2]}:{hhmmss[2:4]}:{hhmmss[4:6]}"),
                "name": f[0],
            })
        # 股指：开,高,低,现价,量,额,持仓,...,昨收,...,日期,时间,...,名称
        return _finalize({
            "price": _f(f[3]), "prev_close": _f(f[13]), "open": _f(f[0]),
            "high": _f(f[1]), "low": _f(f[2]),
            "volume": _f(f[4]), "amount": _f(f[5]),
            "ts": _norm_ts(f[36], f[37]), "name": f[49] if len(f) > 49 else "",
        })

    return None


def fetch_sina(assets: Sequence[Asset]) -> Dict[str, Dict[str, Any]]:
    items = [a for a in assets if a.sina]
    if not items:
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for chunk in _chunks(items, 50):
        url = SINA_URL + ",".join(a.sina for a in chunk)  # type: ignore[arg-type]
        try:
            text = http.get_text(url, headers=SINA_HEADERS, encoding="gbk", timeout=6, min_interval=0.15)
        except Exception:
            continue
        mapping = {ln.group(1): ln.group(2) for ln in _RE_LINE.finditer(text)}
        for a in chunk:
            q = _parse_sina(a.sina or "", mapping.get(a.sina or "", ""))  # type: ignore[arg-type]
            if q:
                q["source"] = "sina"
                out[a.id] = q
    return out


# ────────────────────────────────────────────────────────────── 腾讯实时
def _parse_tencent(code: str, raw: str) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    f = raw.split("~")
    if len(f) < 14:
        return None

    # 国际期货：现价,涨跌幅,买,卖,高,低,时间,昨收,开,...,日期,名称
    if code.startswith("hf_") and len(f) < 25:
        return _finalize({
            "price": _f(f[0]), "pct": _f(f[1]), "prev_close": _f(f[7]), "open": _f(f[8]),
            "high": _f(f[4]), "low": _f(f[5]),
            "ts": _norm_ts(f[12], f[6]), "name": f[13] if len(f) > 13 else "",
        })

    if len(f) < 35:
        return None
    price = _f(f[3])
    prev = _f(f[4])
    return _finalize({
        "price": price, "prev_close": prev, "open": _f(f[5]),
        "high": _f(f[33]), "low": _f(f[34]),
        "change": _f(f[31]), "pct": _f(f[32]),
        "volume": _f(f[36]) or _f(f[6]), "amount": _f(f[37]),
        "ts": _norm_ts(f[30]), "name": f[1],
    })


def fetch_tencent(assets: Sequence[Asset]) -> Dict[str, Dict[str, Any]]:
    items = [a for a in assets if a.tencent]
    if not items:
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for chunk in _chunks(items, 50):
        url = TENCENT_URL + ",".join(a.tencent for a in chunk)  # type: ignore[arg-type]
        try:
            text = http.get_text(url, encoding="gbk", timeout=6, min_interval=0.15)
        except Exception:
            continue
        mapping = {ln.group(1): ln.group(2) for ln in _RE_TENCENT_LINE.finditer(text)}
        for a in chunk:
            q = _parse_tencent(a.tencent or "", mapping.get(a.tencent or "", ""))  # type: ignore[arg-type]
            if q:
                q["source"] = "tencent"
                out[a.id] = q
    return out


# ────────────────────────────────────────────────────────────── CNBC 债券
def fetch_cnbc(assets: Sequence[Asset]) -> Dict[str, Dict[str, Any]]:
    items = [a for a in assets if a.cnbc]
    if not items:
        return {}
    symbols = "|".join(a.cnbc for a in items)  # type: ignore[misc]
    params = {
        "symbols": symbols,
        "requestMethod": "itv",
        "noform": "1",
        "partnerId": "2",
        "fund": "1",
        "exthrs": "1",
        "output": "json",
        "events": "1",
    }
    try:
        data = http.get_json(CNBC_URL, params=params, timeout=10, min_interval=0.3)
    except Exception:
        return {}
    quotes = (data or {}).get("FormattedQuoteResult", {}).get("FormattedQuote", [])
    if isinstance(quotes, dict):
        quotes = [quotes]
    by_symbol = {a.cnbc: a for a in items}
    out: Dict[str, Dict[str, Any]] = {}
    for row in quotes:
        asset = by_symbol.get(row.get("symbol"))
        if not asset:
            continue
        price = _f(row.get("last"))
        if price is None:
            continue
        change = _f(row.get("change"))
        prev = price - change if change is not None else None
        ts = _cnbc_ts(row.get("last_time") or "")
        q = _finalize({
            "price": price, "change": change, "pct": _f(row.get("change_pct")),
            "prev_close": prev, "ts": ts or None, "name": row.get("name", ""),
        })
        if q:
            q["source"] = "cnbc"
            out[asset.id] = q
    return out


# ────────────────────────────────────────────────────────────── 历史数据
_TX_KLINE_ENDPOINTS = (
    "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get",
    "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
    "https://web.ifzq.gtimg.cn/appstock/app/kline/kline",
)


def _parse_kline_node(data: Any, symbol: str, period: str) -> List[List[Any]]:
    node = ((data or {}).get("data") or {}).get(symbol) or {}
    if not isinstance(node, dict):
        return []
    rows = node.get(period) or node.get("qfq" + period) or []
    bars: List[List[Any]] = []
    for r in rows:
        if not isinstance(r, (list, tuple)) or len(r) < 6:
            continue
        # 顺序：日期, 开, 收, 高, 低, 成交量
        bars.append([r[0], _f(r[1]), _f(r[3]), _f(r[4]), _f(r[2]), _f(r[5])])
    return bars


def _tencent_kline(symbol: str, period: str, count: int) -> List[List[Any]]:
    """腾讯 K 线：接口会因限流返回 501，按端点顺序逐个尝试。"""
    for base in _TX_KLINE_ENDPOINTS:
        url = f"{base}?param={symbol},{period},,,{count},qfq"
        try:
            data = http.get_json(url, headers={"Referer": "https://gu.qq.com/"}, timeout=10, min_interval=0.2)
        except Exception:
            continue
        bars = _parse_kline_node(data, symbol, period)
        if bars:
            return bars
    return []


def _sina_cn_kline(symbol: str, count: int = 800) -> List[List[Any]]:
    """新浪 A 股/ETF 日线，作为腾讯限流时的兜底。"""
    url = (
        "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        f"CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=no&datalen={count}"
    )
    rows = http.get_json(url, headers=SINA_HEADERS, timeout=15, min_interval=0.2)
    bars: List[List[Any]] = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        bars.append([r.get("day"), _f(r.get("open")), _f(r.get("high")),
                     _f(r.get("low")), _f(r.get("close")), _f(r.get("volume"))])
    return bars


def _sina_us_daily(symbol: str) -> List[List[Any]]:
    url = (
        "https://stock.finance.sina.com.cn/usstock/api/jsonp.php/"
        f"var%20_{symbol}=/US_MinKService.getDailyK?symbol={symbol}&___qn=3"
    )
    text = http.get_text(url, headers=SINA_HEADERS, timeout=25, min_interval=0.3)
    rows = _jsonp(text) or []
    return [
        [r.get("d"), _f(r.get("o")), _f(r.get("h")), _f(r.get("l")), _f(r.get("c")), _f(r.get("v"))]
        for r in rows if r.get("d")
    ]


def _sina_inner_daily(symbol: str) -> List[List[Any]]:
    url = (
        "https://stock2.finance.sina.com.cn/futures/api/jsonp.php/"
        f"var%20_{symbol}=/InnerFuturesNewService.getDailyKLine?symbol={symbol}"
    )
    text = http.get_text(url, headers=SINA_HEADERS, timeout=20, min_interval=0.3)
    rows = _jsonp(text) or []
    return [
        [r.get("d"), _f(r.get("o")), _f(r.get("h")), _f(r.get("l")), _f(r.get("c")), _f(r.get("v"))]
        for r in rows if r.get("d")
    ]


def _sina_global_daily(symbol: str) -> List[List[Any]]:
    url = (
        "https://stock2.finance.sina.com.cn/futures/api/jsonp.php/"
        f"var%20_{symbol}=/GlobalFuturesService.getGlobalFuturesDailyKLine?symbol={symbol}"
    )
    text = http.get_text(url, headers=SINA_HEADERS, timeout=20, min_interval=0.3)
    rows = _jsonp(text) or []
    return [
        [r.get("date"), _f(r.get("open")), _f(r.get("high")), _f(r.get("low")), _f(r.get("close")), _f(r.get("volume"))]
        for r in rows if r.get("date")
    ]


_UST_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
    "daily-treasury-rates.csv/{year}/all?type=daily_treasury_yield_curve"
    "&field_tdr_date_value={year}&page&_format=csv"
)

# 财政部期限 -> CNBC 符号（财政部接口不可用时兜底）
_UST_TO_CNBC = {"2 Yr": "US2Y", "5 Yr": "US5Y", "10 Yr": "US10Y", "30 Yr": "US30Y"}

_CNBC_CHART_URL = "https://ts-api.cnbc.com/harmony/app/charts/{range}.json"

# 东方财富「中美国债收益率」数据集：中国 2/5/10/30 年 + 10Y-2Y 利差
EM_YIELD_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
EM_YIELD_CACHE: Dict[str, Any] = {"ts": 0.0, "series": {}}
EM_YIELD_FIELDS = ("EMM00588704", "EMM00166462", "EMM00166466", "EMM00166469")


def _em_cn_yield_series(force: bool = False) -> Dict[str, List[List[Any]]]:
    """抓取中债收益率曲线全历史（东财，1990 年至今），结果缓存 6 小时。"""
    now = time.time()
    if not force and EM_YIELD_CACHE["ts"] and now - EM_YIELD_CACHE["ts"] < 6 * 3600:
        return EM_YIELD_CACHE["series"]

    rows: List[Dict[str, Any]] = []
    page = 1
    while page <= 25:
        try:
            data = http.get_json(
                EM_YIELD_URL,
                params={
                    "reportName": "RPTA_WEB_TREASURYYIELD",
                    "columns": "ALL",
                    "pageSize": "500",
                    "pageNumber": str(page),
                    "sortColumns": "SOLAR_DATE",
                    "sortTypes": "-1",
                },
                timeout=25,
                min_interval=0.25,
            )
        except Exception:
            break
        result = (data or {}).get("result") or {}
        batch = result.get("data") or []
        if not batch:
            break
        rows.extend(batch)
        total = result.get("count") or 0
        if len(rows) >= total or len(batch) < 500:
            break
        page += 1

    series: Dict[str, List[List[Any]]] = {}
    for row in rows:
        day = str(row.get("SOLAR_DATE") or "")[:10]
        if not day:
            continue
        for field in EM_YIELD_FIELDS:
            value = _f(row.get(field))
            if value is None:
                continue
            series.setdefault(field, []).append([day, value, value, value, value, None])
    for field in series:
        series[field].sort(key=lambda r: r[0])
    EM_YIELD_CACHE["ts"] = now
    EM_YIELD_CACHE["series"] = series
    return series


def fetch_em_yields(assets: Sequence[Asset]) -> Dict[str, Dict[str, Any]]:
    """中债收益率的最新一日报价（收盘估值，非盘中实时）。"""
    series = _em_cn_yield_series()
    out: Dict[str, Dict[str, Any]] = {}
    for asset in assets:
        bars = series.get(asset.em or "")
        if not bars:
            continue
        last = bars[-1]
        prev = bars[-2] if len(bars) > 1 else None
        quote = _finalize({
            "price": last[4],
            "prev_close": prev[4] if prev else None,
            "change": (last[4] - prev[4]) if prev else None,
            "ts": f"{last[0]} 17:30:00",
            "source": "eastmoney",
        })
        if quote:
            out[asset.id] = quote
    return out


def _cnbc_chart_history(symbol: str, rng: str = "1Y") -> List[List[Any]]:
    """CNBC 图表历史：覆盖面广（债券收益率、美元指数、VIX、离岸人民币等）。

    range 取 1Y / 5Y / 10Y；接口按范围自动决定颗粒度与条数。
    """
    try:
        data = http.get_json(
            _CNBC_CHART_URL.format(range=rng),
            params={"symbol": symbol, "interval": "1D"},
            timeout=20,
            min_interval=0.25,
        )
    except Exception:
        return []
    rows = ((data or {}).get("barData") or {}).get("priceBars") or []
    bars: List[List[Any]] = []
    for r in rows:
        raw = str(r.get("tradeTime") or "")
        if len(raw) < 8:
            continue
        day = f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"
        close = _f(r.get("close"))
        if close is None:
            continue
        bars.append([day, _f(r.get("open")), _f(r.get("high")), _f(r.get("low")),
                     close, _f(r.get("volume"))])
    # 同一交易日可能有多条，保留最后一条
    dedup: Dict[str, List[Any]] = {}
    for b in bars:
        dedup[b[0]] = b
    return [dedup[k] for k in sorted(dedup)]


def _ustreasury(tenor: str) -> List[List[Any]]:
    """美国财政部官方日度国债收益率曲线（如 '10 Yr'、'2 Yr'），返回百分数。"""
    bars: List[List[Any]] = []
    year_now = date.today().year
    for year in range(year_now - 9, year_now + 1):
        try:
            text = http.get_text(_UST_URL.format(year=year), timeout=25, min_interval=0.4)
        except Exception:
            continue
        for row in csv.DictReader(io.StringIO(text)):
            raw = (row.get("Date") or "").strip()
            value = _f(row.get(tenor))
            if not raw or value is None:
                continue
            try:
                day = datetime.strptime(raw, "%m/%d/%Y").date().isoformat()
            except ValueError:
                continue
            bars.append([day, value, value, value, value, None])
    bars.sort(key=lambda r: r[0])
    return bars


def _frankfurter(pair: str, start: str = "2005-01-01") -> List[List[Any]]:
    base, quote = pair.split("/")
    url = f"https://api.frankfurter.app/{start}..{date.today().isoformat()}"
    data = http.get_json(url, params={"from": base, "to": quote}, timeout=20, min_interval=0.3)
    rates = (data or {}).get("rates", {})
    bars: List[List[Any]] = []
    for day, mapping in sorted(rates.items()):
        value = _f(mapping.get(quote))
        if value:
            bars.append([day, value, value, value, value, None])
    return bars


def fetch_history(asset: Asset, interval: str = "day") -> List[List[Any]]:
    """按资产配置抓取历史 K 线；interval ∈ {day, week, month}。"""
    if not asset.hist:
        return []
    source, symbol = asset.hist
    if source == "tencent":
        count = {"day": 800, "week": 300, "month": 120}.get(interval, 800)
        bars = _tencent_kline(symbol, interval, count)
        if not bars and interval == "day" and symbol[:2] in ("sh", "sz", "bj"):
            bars = _sina_cn_kline(symbol, count)  # A 股/ETF 双源兜底
        return bars
    if source == "sina_us":
        return _sina_us_daily(symbol) if interval == "day" else []
    if source == "sina_inner":
        return _sina_inner_daily(symbol) if interval == "day" else []
    if source == "sina_global":
        return _sina_global_daily(symbol) if interval == "day" else []
    if source == "frankfurter":
        return _frankfurter(symbol) if interval == "day" else []
    if source == "ustreasury":
        if interval != "day":
            return []
        bars = _ustreasury(symbol)
        if not bars and symbol in _UST_TO_CNBC:
            bars = _cnbc_chart_history(_UST_TO_CNBC[symbol], "10Y")
        return bars
    if source == "cnbc_chart":
        if interval == "day":
            return _cnbc_chart_history(symbol, "1Y")   # 约 2 年日线
        if interval == "week":
            return _cnbc_chart_history(symbol, "10Y")  # 约 15 年周线
        return []
    if source == "em_cn_yield":
        return _em_cn_yield_series().get(symbol, []) if interval == "day" else []
    return []


def probe_asset(asset: Asset) -> Dict[str, Any]:
    """自检：抓一次实时与历史，返回结果摘要。"""
    out: Dict[str, Any] = {"id": asset.id, "name": asset.name}
    if asset.sina:
        out["sina"] = fetch_sina([asset]).get(asset.id)
    if asset.tencent:
        out["tencent"] = fetch_tencent([asset]).get(asset.id)
    if asset.cnbc:
        out["cnbc"] = fetch_cnbc([asset]).get(asset.id)
    if asset.hist:
        try:
            bars = fetch_history(asset, "day")
            out["hist"] = {"n": len(bars), "first": bars[0][0] if bars else None,
                           "last": bars[-1][0] if bars else None}
        except Exception as exc:  # noqa: BLE001
            out["hist"] = {"error": str(exc)[:120]}
    return out
