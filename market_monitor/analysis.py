"""关联性分析：滚动窗口下的多种相关性指标。

所有指标都落在 [-1, 1]，可直接画在同一张图里对比：
    pearson    皮尔逊线性相关
    spearman   斯皮尔曼秩相关
    kendall    Kendall τ-b 秩相关（含并列修正）
    downside   下行相关：只取窗口内双方同时下跌的交易日
    volatility 波动相关：|收益率| 的相关性，衡量波动共振
    leadlag    滞后相关：corr(A_t, B_{t-1}) 与 corr(B_t, A_{t-1}) 的平均
"""
from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

METRICS: List[Tuple[str, str, str]] = [
    ("pearson", "Pearson 线性相关", "窗口内日收益率的皮尔逊相关系数"),
    ("spearman", "Spearman 秩相关", "按秩次计算，抗异常值"),
    ("kendall", "Kendall τ 秩相关", "一致对/不一致对，含并列修正"),
    ("downside", "下行相关（同跌）", "只统计双方同时下跌的交易日"),
    ("volatility", "波动相关（|收益|）", "绝对收益的相关性，衡量波动共振"),
    ("leadlag", "滞后相关（±1 日）", "领先/滞后一日的对称相关"),
]

COMPOSITE_KEY = "composite"
COMPOSITE_LABEL = "综合关联指数"
COMPOSITE_DESC = "上面各指标按权重加权平均（缺失指标自动重新分配权重）"

# 默认权重：线性相关为主，秩相关与下行相关次之
DEFAULT_WEIGHTS: Dict[str, float] = {
    "pearson": 0.30,
    "spearman": 0.20,
    "kendall": 0.15,
    "downside": 0.15,
    "volatility": 0.10,
    "leadlag": 0.10,
}


def _windows(x: np.ndarray, w: int) -> np.ndarray:
    return sliding_window_view(np.asarray(x, dtype=float), w)


def _pearson_rows(a: np.ndarray, b: np.ndarray, min_n: int = 3) -> np.ndarray:
    """按行计算皮尔逊相关，a/b 形状 (n, w)。"""
    if a.shape[0] == 0:
        return np.array([])
    am = a.mean(axis=1, keepdims=True)
    bm = b.mean(axis=1, keepdims=True)
    av = a - am
    bv = b - bm
    num = (av * bv).sum(axis=1)
    den = np.sqrt((av * av).sum(axis=1) * (bv * bv).sum(axis=1))
    out = np.full(a.shape[0], np.nan)
    ok = den > 0
    out[ok] = num[ok] / den[ok]
    return out


def _rank_rows(a: np.ndarray) -> np.ndarray:
    """窗口内秩次（1..w）。并列值按出现顺序处理，对收益率序列足够精确。"""
    sorter = a.argsort(axis=1)
    ranks = np.empty(a.shape, dtype=float)
    rows = np.arange(a.shape[0])[:, None]
    ranks[rows, sorter] = np.arange(1, a.shape[1] + 1)
    return ranks


def _kendall_tau(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    n, w = a.shape
    out = np.full(n, np.nan)
    if n == 0:
        return out
    a32 = a.astype(np.float32, copy=False)
    b32 = b.astype(np.float32, copy=False)
    # 控制单次内存占用；bool.view(int8) 零拷贝，整数比较比浮点符号快得多
    chunk = max(1, int(4_000_000 / max(w * w, 1)))
    for start in range(0, n, chunk):
        sl = slice(start, min(n, start + chunk))
        x = a32[sl]
        y = b32[sl]
        sa = (x[:, :, None] > x[:, None, :]).view(np.int8) - (x[:, :, None] < x[:, None, :]).view(np.int8)
        sb = (y[:, :, None] > y[:, None, :]).view(np.int8) - (y[:, :, None] < y[:, None, :]).view(np.int8)
        s = (sa * sb).sum(axis=(1, 2), dtype=np.int32) / 2.0
        n0 = w * (w - 1) / 2.0
        n1 = ((sa == 0).sum(axis=(1, 2)) - w) / 2.0
        n2 = ((sb == 0).sum(axis=(1, 2)) - w) / 2.0
        den = np.sqrt(np.maximum((n0 - n1) * (n0 - n2), 0.0))
        with np.errstate(invalid="ignore", divide="ignore"):
            out[sl] = np.where(den > 0, s / den, np.nan)
    return out


def _downside(a: np.ndarray, b: np.ndarray, min_n: int = 12) -> np.ndarray:
    """双方同日下跌样本上的相关；样本不足 min_n 时为 NaN。"""
    n, w = a.shape
    out = np.full(n, np.nan)
    if n == 0:
        return out
    mask = (a < 0) & (b < 0)
    count = mask.sum(axis=1)
    am = np.where(mask, a, np.nan)
    bm = np.where(mask, b, np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        ma = np.nanmean(am, axis=1, keepdims=True)
        mb = np.nanmean(bm, axis=1, keepdims=True)
    av = np.where(mask, a - ma, 0.0)
    bv = np.where(mask, b - mb, 0.0)
    num = (av * bv).sum(axis=1)
    den = np.sqrt((av * av).sum(axis=1) * (bv * bv).sum(axis=1))
    ok = (count >= min_n) & (den > 0)
    out[ok] = num[ok] / den[ok]
    return out


def rolling_metrics(ra: np.ndarray, rb: np.ndarray, window: int) -> Dict[str, np.ndarray]:
    """输入两条对齐的日收益率序列，返回每个指标一条长度相同的曲线。"""
    ra = np.asarray(ra, dtype=float)
    rb = np.asarray(rb, dtype=float)
    n = len(ra)
    w = int(window)
    empty = n - w + 1
    if empty <= 0:
        return {key: np.array([]) for key, _, _ in METRICS}

    a = _windows(ra, w)
    b = _windows(rb, w)

    out: Dict[str, np.ndarray] = {}
    out["pearson"] = _pearson_rows(a, b)
    out["spearman"] = _pearson_rows(_rank_rows(a), _rank_rows(b))
    out["kendall"] = _kendall_tau(a, b)
    out["downside"] = _downside(a, b)
    out["volatility"] = _pearson_rows(np.abs(a), np.abs(b))

    # 领先/滞后：两条方向的平均，长度比主指标少 1，前面补 NaN 对齐
    c1 = _pearson_rows(_windows(ra[1:], w), _windows(rb[:-1], w))
    c2 = _pearson_rows(_windows(rb[1:], w), _windows(ra[:-1], w))
    lag = np.nanmean(np.vstack([c1, c2]), axis=0)
    out["leadlag"] = np.concatenate([[np.nan], lag])[:empty]
    return out


def average_series(series_list: Sequence[np.ndarray]) -> np.ndarray:
    """多标的时的两两平均：逐点做 nan 平均（全缺失点返回 NaN）。"""
    if not series_list:
        return np.array([])
    stack = np.vstack(series_list)
    valid = ~np.isnan(stack)
    counts = valid.sum(axis=0)
    totals = np.where(valid, stack, 0.0).sum(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = totals / np.maximum(counts, 1)
    return np.where(counts > 0, mean, np.nan)


def normalize_weights(weights: Dict[str, float]) -> Dict[str, float]:
    """把权重归一化到和为 1；全为 0 时退化为等权。"""
    raw = {k: max(0.0, float(weights.get(k, 0.0))) for k, _, _ in METRICS}
    total = sum(raw.values())
    if total <= 0:
        return {k: 1.0 / len(METRICS) for k, _, _ in METRICS}
    return {k: v / total for k, v in raw.items()}


def composite_series(series_map: Dict[str, np.ndarray], weights: Dict[str, float]) -> np.ndarray:
    """按权重把各指标合成为一条曲线；某点缺失的指标自动重新分配权重。"""
    keys = [k for k, _, _ in METRICS]
    if not series_map:
        return np.array([])
    length = len(series_map[keys[0]])
    if length == 0:
        return np.array([])
    w = np.array([normalize_weights(weights)[k] for k in keys], dtype=float)[:, None]
    stack = np.vstack([np.asarray(series_map[k], dtype=float) for k in keys])
    mask = ~np.isnan(stack)
    weighted = np.where(mask, stack, 0.0) * w * mask
    denom = (w * mask).sum(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        out = np.where(denom > 0, weighted.sum(axis=0) / denom, np.nan)
    return out


def money_flow(bars: Sequence[Sequence[Any]]) -> List[List[Any]]:
    """按 OHLCV 估算每日资金流强度（成交额占比，单位无关）。

    收盘越靠近当日最高价，视为越多成交是「流入」；越靠近最低价则相反。
    返回 [[date, inflow_pct, outflow_pct, net_pct], ...]，三者单位都是 %。
    """
    rows: List[List[Any]] = []
    for b in bars:
        if len(b) < 6:
            continue
        date, o, h, l, c = b[0], b[1], b[2], b[3], b[4]
        if None in (o, h, l, c):
            continue
        if h <= l:  # 一字板 / 无波动：中性
            rows.append([date, 50.0, 50.0, 0.0])
            continue
        inflow = (c - l) / (h - l) * 100.0
        outflow = 100.0 - inflow
        rows.append([date, round(inflow, 3), round(outflow, 3), round(inflow - outflow, 3)])
    return rows


def summarize_flow(rows: Sequence[Sequence[Any]]) -> Dict[str, Any]:
    """资金流摘要：最近一日、区间累计、平均流入/流出占比。"""
    if not rows:
        return {"latest": None, "cumulative": None, "inflow": None, "outflow": None}
    last = rows[-1]
    return {
        "latest": round(float(last[3]), 3),
        "cumulative": round(sum(float(r[3]) for r in rows), 2),
        "inflow": round(sum(float(r[1]) for r in rows) / len(rows), 2),
        "outflow": round(sum(float(r[2]) for r in rows) / len(rows), 2),
    }


def summary(values: np.ndarray) -> Dict[str, float]:
    """区间统计：最新值、均值、极值。"""
    arr = np.asarray(values, dtype=float)
    arr = arr[~np.isnan(arr)]
    if arr.size == 0:
        return {"latest": None, "mean": None, "min": None, "max": None}
    return {
        "latest": round(float(arr[-1]), 4),
        "mean": round(float(arr.mean()), 4),
        "min": round(float(arr.min()), 4),
        "max": round(float(arr.max()), 4),
    }
