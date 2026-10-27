"""关联分析 · LightGBM 概率预测。

输入：用户选定的 N 个指标在 t-1 及之前 T 个周期的历史数据（日线收益率序列）。
输出：目标指标 A 在 t 时刻相对 t-1 的波动幅度落在各个区间的概率。

模型：LightGBM 多分类（缺失时退回 numpy 多项逻辑回归，保证打包环境也能用）。
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

INTERVAL = "day"
_LOCK = threading.Lock()
_CACHE: Dict[str, Tuple[Any, float]] = {}


def _get(key: str, ttl: float) -> Optional[Any]:
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and time.time() - hit[1] < ttl:
            return hit[0]
    return None


def _put(key: str, value: Any) -> None:
    with _LOCK:
        _CACHE[key] = (value, time.time())


def load_closes(asset_ids: Sequence[str], limit: int = 3000) -> Dict[str, Dict[str, float]]:
    """从本地行情库读取日线收盘价：{asset: {date: close}}。"""
    db = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "market.db")
    if not os.path.exists(db):
        return {}
    out: Dict[str, Dict[str, float]] = {}
    con = sqlite3.connect(db)
    try:
        cur = con.cursor()
        for aid in asset_ids:
            rows = cur.execute(
                "SELECT ts, close FROM bars WHERE asset_id = ? AND interval = ? ORDER BY ts DESC LIMIT ?",
                (aid, INTERVAL, limit),
            ).fetchall()
            if rows:
                out[aid] = {ts: float(close) for ts, close in rows if close}
    finally:
        con.close()
    return out


def _align(series: Dict[str, Dict[str, float]], asset_ids: Sequence[str]) -> Tuple[List[str], np.ndarray]:
    """取所有指标都有数据的交易日，返回日期列表和 [n, n_assets] 收盘价矩阵。"""
    common: Optional[set] = None
    for aid in asset_ids:
        keys = set(series.get(aid, {}).keys())
        common = keys if common is None else (common & keys)
    if not common:
        return [], np.zeros((0, 0))
    dates = sorted(common)
    mat = np.array([[series[aid][d] for aid in asset_ids] for d in dates], dtype=float)
    return dates, mat


def build_dataset(asset_ids: Sequence[str], window: int = 20) -> Dict[str, Any]:
    """构造特征矩阵：每个指标 t-1..t-window 的收益率，目标为 A 的 t 时刻收益率。"""
    window = max(3, min(int(window or 20), 120))
    series = load_closes(asset_ids)
    missing = [a for a in asset_ids if a not in series]
    if missing:
        return {"ok": False, "error": f"本地没有这些指标的日线数据：{', '.join(missing[:4])}"}

    dates, closes = _align(series, asset_ids)
    if len(dates) < window + 30:
        return {"ok": False, "error": f"对齐后只有 {len(dates)} 个交易日，不足以训练（至少需要 {window + 30} 个）"}

    rets = np.zeros_like(closes)
    rets[1:] = closes[1:] / closes[:-1] - 1.0
    rets = np.clip(rets, -0.2, 0.2)

    n_assets = len(asset_ids)
    names: List[str] = []
    for aid in asset_ids:
        for lag in range(1, window + 1):
            names.append(f"{aid}·t-{lag}")
    # 每个指标的滚动波动率，作为补充特征
    for aid in asset_ids:
        names.append(f"{aid}·波动率{window}")

    rows: List[List[float]] = []
    targets: List[float] = []
    target_dates: List[str] = []
    for t in range(window + 1, len(dates)):
        vec: List[float] = []
        for j in range(n_assets):
            for lag in range(1, window + 1):
                vec.append(float(rets[t - lag, j]))
        for j in range(n_assets):
            vec.append(float(np.std(rets[t - window: t, j])))
        rows.append(vec)
        targets.append(float(rets[t, 0]))  # 指标 A 的 t 时刻收益率
        target_dates.append(dates[t])

    X = np.asarray(rows, dtype=float)
    y = np.asarray(targets, dtype=float)
    return {
        "ok": True,
        "X": X,
        "y": y,
        "dates": target_dates,
        "features": names,
        "assets": list(asset_ids),
        "window": window,
        "last_date": target_dates[-1],
    }


def _edges(y: np.ndarray, buckets: int, mode: str) -> np.ndarray:
    """分箱边界：quantile 按分位数（样本更均衡），fixed 按固定百分比。"""
    buckets = max(3, min(int(buckets or 6), 10))
    if mode == "fixed":
        base = [-0.03, -0.015, -0.005, 0.005, 0.015, 0.03]
        if buckets == len(base) + 1:
            return np.array(base)
        q = np.linspace(0, 1, buckets + 1)[1:-1]
        return np.quantile(y, q)
    q = np.linspace(0, 1, buckets + 1)[1:-1]
    return np.quantile(y, q)


def _labels(edges: np.ndarray) -> List[str]:
    bounds = [-np.inf, *edges.tolist(), np.inf]
    out = []
    for lo, hi in zip(bounds[:-1], bounds[1:]):
        if not np.isfinite(lo):
            out.append(f"低于 {hi * 100:.2f}%")
        elif not np.isfinite(hi):
            out.append(f"高于 {lo * 100:.2f}%")
        else:
            out.append(f"{lo * 100:.2f}% ~ {hi * 100:.2f}%")
    return out


def _logreg_fallback(X: np.ndarray, y: np.ndarray, k: int) -> np.ndarray:
    """numpy 多项逻辑回归，作为 LightGBM 不可用时的兜底。"""
    n, d = X.shape
    mu, sd = X.mean(0), X.std(0) + 1e-9
    Z = (X - mu) / sd
    Z = np.hstack([Z, np.ones((n, 1))])
    W = np.zeros((d + 1, k))
    Y = np.eye(k)[y]
    for _ in range(300):
        logits = Z @ W
        logits -= logits.max(1, keepdims=True)
        P = np.exp(logits)
        P /= P.sum(1, keepdims=True)
        W -= 0.5 * (Z.T @ (P - Y) / n + 1e-3 * W)
    return W, (mu, sd)


def _predict_logreg(W, stats, X: np.ndarray) -> np.ndarray:
    mu, sd = stats
    Z = (X - mu) / (sd + 1e-9)
    Z = np.hstack([Z, np.ones((len(Z), 1))])
    logits = Z @ W
    logits -= logits.max(1, keepdims=True)
    P = np.exp(logits)
    return P / P.sum(1, keepdims=True)


def predict(
    asset_ids: Sequence[str],
    window: int = 20,
    buckets: int = 6,
    mode: str = "quantile",
    test_ratio: float = 0.2,
) -> Dict[str, Any]:
    """训练并输出下一个周期各波动区间的概率。"""
    if len(asset_ids) < 2:
        return {"ok": False, "error": "至少要选择 2 个指标（第一个作为预测目标）"}

    cache_key = f"ml|{'|'.join(asset_ids)}|{window}|{buckets}|{mode}"
    cached = _get(cache_key, 300)
    if cached is not None:
        return cached

    ds = build_dataset(asset_ids, window)
    if not ds.get("ok"):
        return ds

    X: np.ndarray = ds["X"]
    y: np.ndarray = ds["y"]
    n = len(y)
    split = max(30, int(n * (1 - max(0.1, min(test_ratio, 0.4)))))
    split = min(split, n - 10)

    edges = _edges(y[:split], buckets, mode)
    labels = _labels(edges)
    classes = np.digitize(y, edges)
    k = len(labels)

    X_train, y_train = X[:split], classes[:split]
    X_test, y_test = X[split:], classes[split:]

    engine = "lightgbm"
    engine_error = ""
    importance: List[Dict[str, Any]] = []
    proba: Optional[np.ndarray] = None
    acc = 0.0
    try:
        import lightgbm as lgb  # type: ignore

        # 用原生 API（不依赖 scikit-learn）
        dtrain = lgb.Dataset(X_train, label=y_train, free_raw_data=True)
        params = {
            "objective": "multiclass",
            "num_class": k,
            "metric": "multi_logloss",
            "learning_rate": 0.05,
            "num_leaves": 15,
            "max_depth": 5,
            "min_data_in_leaf": 20,
            "feature_fraction": 0.85,
            "bagging_fraction": 0.9,
            "bagging_freq": 1,
            "lambda_l2": 1.0,
            "verbosity": -1,
            "num_threads": 2,
            "seed": 42,
        }
        booster = lgb.train(params, dtrain, num_boost_round=260)
        proba = booster.predict(X[-1:])[0]
        if len(X_test):
            pred = booster.predict(X_test).argmax(1)
            acc = float((pred == y_test).mean())
        gains = booster.feature_importance(importance_type="gain")
        top = np.argsort(gains)[::-1][:12]
        total = float(gains.sum()) or 1.0
        importance = [
            {"name": ds["features"][i], "gain": round(float(gains[i]) / total * 100, 2)}
            for i in top
        ]
    except Exception as exc:
        engine = "logreg"
        engine_error = str(exc).splitlines()[0][:120] if str(exc) else type(exc).__name__
        W, stats = _logreg_fallback(X_train, y_train, k)
        proba = _predict_logreg(W, stats, X[-1:])[0]
        if len(X_test):
            pred = _predict_logreg(W, stats, X_test).argmax(1)
            acc = float((pred == y_test).mean())

    # 概率收缩：与训练集类别先验混合，避免树模型输出过度自信
    prior = np.bincount(y_train, minlength=k).astype(float)
    prior = prior / max(1.0, prior.sum())
    if proba is not None:
        proba = 0.75 * np.asarray(proba, dtype=float) + 0.25 * prior
        proba = proba / max(1e-9, proba.sum())

    base = float(np.bincount(y_train, minlength=k).max() / max(1, len(y_train)))
    result = {
        "ok": True,
        "engine": engine,
        "engine_error": engine_error,
        "target": asset_ids[0],
        "assets": list(asset_ids),
        "window": ds["window"],
        "labels": labels,
        "probs": [round(float(p) * 100, 2) for p in proba],
        "prior": [round(float(p) * 100, 2) for p in prior],
        "accuracy": round(acc * 100, 1),
        "baseline": round(base * 100, 1),
        "samples": n,
        "train": len(X_train),
        "test": len(X_test),
        "from": ds["dates"][0],
        "to": ds["last_date"],
        "features": len(ds["features"]),
        "importance": importance,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _put(cache_key, result)
    return result
