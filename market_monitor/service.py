"""行情编排服务：并发抓取、合并降级、快照积累、历史缓存与批量下载。"""
from __future__ import annotations

import csv
import os
import threading
import time
from collections import OrderedDict
from datetime import date, datetime, timedelta
from itertools import combinations
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from . import config
from .analysis import (
    COMPOSITE_DESC, COMPOSITE_KEY, COMPOSITE_LABEL, DEFAULT_WEIGHTS,
    METRICS, average_series, composite_series, normalize_weights,
    rolling_metrics, summary,
)
from .config import Asset
from .net import http
from .providers import (
    fetch_cnbc, fetch_em_yields, fetch_history, fetch_intraday, fetch_sina, fetch_tencent,
)
from .store import Store


# ────────────────────────────────────────────────────────────── 交易时段
def _nth_sunday(year: int, month: int, n: int) -> date:
    d = date(year, month, 1)
    offset = (6 - d.weekday()) % 7  # 到第一个周日
    return d + timedelta(days=offset + 7 * (n - 1))


def _us_dst(d: date) -> bool:
    year = d.year
    return _nth_sunday(year, 3, 2) <= d < _nth_sunday(year, 11, 1)


def _hm(dt: datetime) -> int:
    return dt.hour * 100 + dt.minute


def resample_bars(bars: Sequence[Sequence[Any]], interval: str) -> List[List[Any]]:
    """把日线聚合为周线 / 月线（供只有日线数据源的标的补全长周期）。"""
    if interval == "day" or not bars:
        return [list(b) for b in bars]
    out: List[List[Any]] = []
    index: Dict[Any, int] = {}
    for b in bars:
        ts = str(b[0]) if b[0] else ""
        if len(ts) < 10:
            continue
        try:
            d = date.fromisoformat(ts[:10])
        except ValueError:
            continue
        iso = d.isocalendar()
        key = (iso[0], iso[1]) if interval == "week" else (d.year, d.month)
        if key not in index:
            index[key] = len(out)
            out.append([ts, b[1], b[2], b[3], b[4], (b[5] or 0) if len(b) > 5 else 0])
        else:
            cur = out[index[key]]
            cur[0] = ts
            if b[2] is not None:
                cur[2] = b[2] if cur[2] is None else max(cur[2], b[2])
            if b[3] is not None:
                cur[3] = b[3] if cur[3] is None else min(cur[3], b[3])
            cur[4] = b[4]
            if len(b) > 5:
                cur[5] = (cur[5] or 0) + (b[5] or 0)
    return out


def session_status(group: str, now: datetime) -> Dict[str, str]:
    """返回 {state: open|closed|pre|post|lunch, label: 中文}。"""
    weekday = now.weekday() < 5
    hm = _hm(now)

    if group == "A股":
        if not weekday:
            return {"state": "closed", "label": "休市"}
        if 915 <= hm < 930:
            return {"state": "pre", "label": "集合竞价"}
        if 930 <= hm < 1130 or 1300 <= hm < 1500:
            return {"state": "open", "label": "交易中"}
        if 1130 <= hm < 1300:
            return {"state": "lunch", "label": "午间休市"}
        return {"state": "closed", "label": "已收盘"}

    if group == "港股":
        if not weekday:
            return {"state": "closed", "label": "休市"}
        if 900 <= hm < 930:
            return {"state": "pre", "label": "盘前"}
        if 930 <= hm < 1200 or 1300 <= hm < 1600:
            return {"state": "open", "label": "交易中"}
        if 1200 <= hm < 1300:
            return {"state": "lunch", "label": "午间休市"}
        return {"state": "closed", "label": "已收盘"}

    if group in ("美股", "利率"):
        offset = 12 if _us_dst(now.date()) else 13
        et = now - timedelta(hours=offset)
        if et.weekday() >= 5:
            return {"state": "closed", "label": "休市"}
        ehm = _hm(et)
        if 400 <= ehm < 930:
            return {"state": "pre", "label": "盘前"}
        if 930 <= ehm < 1600:
            return {"state": "open", "label": "交易中"}
        if 1600 <= ehm < 2000:
            return {"state": "post", "label": "盘后"}
        return {"state": "closed", "label": "已收盘"}

    if group == "期货":
        if not weekday:
            return {"state": "closed", "label": "休市"}
        if 900 <= hm < 1500 or hm >= 2100 or hm < 230:
            return {"state": "open", "label": "交易中"}
        return {"state": "closed", "label": "休市"}

    if group == "汇率":
        return {"state": "open" if weekday else "closed", "label": "报价中" if weekday else "休市"}

    return {"state": "closed", "label": "休市"}


# ────────────────────────────────────────────────────────────── 服务主体
class MarketService:
    def __init__(self, store: Store, refresh_seconds: float = 4.0, bond_seconds: float = 20.0,
                 em_seconds: float = 1800.0) -> None:
        self.store = store
        self.refresh_seconds = refresh_seconds
        self.bond_seconds = bond_seconds
        self.em_seconds = em_seconds
        self.quotes: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self.updated_at: Optional[str] = None
        self.refreshed_ts: float = 0.0
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_ok: Dict[str, float] = {}
        self._spark_cache: Dict[str, tuple] = {}
        self._hist_cache: Dict[str, tuple] = {}
        self._last_bond = 0.0
        self._last_em = 0.0
        self._last_snapshot = 0.0
        self._errors: List[Dict[str, Any]] = []
        self._analysis_cache: Dict[str, tuple] = {}
        self._intraday_cache: Dict[str, tuple] = {}
        self.last_ping: float = 0.0
        self.download_state: Dict[str, Any] = {
            "running": False, "done": 0, "total": 0, "current": "", "errors": [],
            "started_at": None, "finished_at": None,
        }

    # ------------------------------------------------------------ 生命周期
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._first_run = self.store.stats().get("bars", 0) == 0
        self._thread = threading.Thread(target=self._loop, name="market-refresh", daemon=True)
        self._thread.start()
        threading.Thread(target=self._bootstrap_history, name="bootstrap-history", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def ping(self) -> None:
        """前端心跳：桌面版据此判断窗口是否还开着。"""
        self.last_ping = time.time()

    def _loop(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self.refresh()
            except Exception as exc:  # noqa: BLE001 - 后台线程不能死
                self._note_error("refresh", exc)
            elapsed = time.monotonic() - started
            self._stop.wait(max(0.5, self.refresh_seconds - elapsed))

    def _bootstrap_history(self) -> None:
        """首次启动时补齐顶部大卡的历史，让图表立刻有内容；空库时自动全量下载。"""
        time.sleep(1.5)
        for asset in config.ASSETS:
            if self._stop.is_set():
                return
            if not asset.hero:
                continue
            try:
                self.history(asset.id, "day")
            except Exception:
                pass
        if getattr(self, "_first_run", False) and not self._stop.is_set():
            # 首次运行：后台把全部标的历史补到本地
            self.start_download(("day", "week", "month"))

    def _note_error(self, scope: str, exc: Exception) -> None:
        with self._lock:
            self._errors.append({"scope": scope, "error": str(exc)[:200], "ts": datetime.now().isoformat(timespec="seconds")})
            self._errors = self._errors[-20:]

    # ------------------------------------------------------------ 抓取
    def refresh(self, include_bonds: bool = True) -> None:
        assets = config.ASSETS
        sina_assets = [a for a in assets if a.sina]
        tencent_assets = [a for a in assets if a.tencent]

        combined: Dict[str, Dict[str, Any]] = {}
        sina_q: Dict[str, Dict[str, Any]] = {}
        tencent_q: Dict[str, Dict[str, Any]] = {}
        try:
            sina_q = fetch_sina(sina_assets)
            combined.update(sina_q)
        except Exception as exc:  # noqa: BLE001
            self._note_error("sina", exc)
        try:
            tencent_q = fetch_tencent(tencent_assets)
            combined.update(tencent_q)
        except Exception as exc:  # noqa: BLE001
            self._note_error("tencent", exc)

        # 按资产配置的优先源取数，缺失时用另一源补位
        for asset in assets:
            if asset.is_derived or asset.cnbc:
                continue
            if asset.prefer == "tencent":
                quote = tencent_q.get(asset.id) or sina_q.get(asset.id)
            else:
                quote = sina_q.get(asset.id) or tencent_q.get(asset.id)
            if quote is not None:
                self._apply(asset, quote)

        if include_bonds and time.time() - self._last_bond >= self.bond_seconds:
            self._last_bond = time.time()
            # 中债收益率是日频估值，半小时刷一次；CNBC 覆盖它的盘中更新
            if time.time() - self._last_em >= self.em_seconds:
                self._last_em = time.time()
                try:
                    self._apply_em(fetch_em_yields([a for a in assets if a.em]))
                except Exception as exc:  # noqa: BLE001
                    self._note_error("eastmoney", exc)
            try:
                self._apply_bonds(fetch_cnbc([a for a in assets if a.cnbc]))
            except Exception as exc:  # noqa: BLE001
                self._note_error("cnbc", exc)

        self._apply_derived()
        with self._lock:
            self.refreshed_ts = time.time()
            self.updated_at = datetime.now().isoformat(timespec="seconds")

        if time.time() - self._last_snapshot >= 60:
            self._last_snapshot = time.time()
            try:
                self._persist_snapshots()
            except Exception as exc:  # noqa: BLE001
                self._note_error("snapshot", exc)

    def _apply(self, asset: Asset, quote: Dict[str, Any]) -> None:
        with self._lock:
            self._last_ok[asset.id] = time.time()
            self.quotes[asset.id] = {**quote, "id": asset.id, "name": asset.name,
                                     "group": asset.group, "kind": asset.kind}

    def _apply_bonds(self, quotes: Dict[str, Dict[str, Any]]) -> None:
        for asset in config.ASSETS:
            if asset.cnbc and asset.id in quotes:
                self._apply(asset, quotes[asset.id])

    def _apply_em(self, quotes: Dict[str, Dict[str, Any]]) -> None:
        """东财中债收益率只填补空缺，不覆盖 CNBC 的盘中报价。"""
        with self._lock:
            existing = dict(self.quotes)
        for asset in config.ASSETS:
            if not asset.em or asset.id not in quotes:
                continue
            if asset.cnbc and existing.get(asset.id, {}).get("source") == "cnbc":
                continue
            self._apply(asset, quotes[asset.id])

    def _apply_derived(self) -> None:
        for asset in config.ASSETS:
            if not asset.derived:
                continue
            a, b = asset.derived
            with self._lock:
                qa, qb = self.quotes.get(a), self.quotes.get(b)
            if not qa or not qb or qa.get("price") is None or qb.get("price") is None:
                continue
            price = round(qa["price"] - qb["price"], 6)
            change = None
            if qa.get("change") is not None and qb.get("change") is not None:
                change = round(qa["change"] - qb["change"], 6)
            self._apply(asset, {
                "price": price, "change": change,
                "pct": None,  # 利差以 bp 表述，不提供百分比涨跌
                "prev_close": None, "ts": qa.get("ts"), "source": "derived",
            })

    def _persist_snapshots(self) -> None:
        today = date.today().isoformat()
        rows_by_asset: Dict[str, List[List[Any]]] = {}
        with self._lock:
            quotes = dict(self.quotes)
        for asset in config.ASSETS:
            if not asset.hist or asset.hist[0] != "snapshot":
                continue
            q = quotes.get(asset.id)
            if not q or q.get("price") is None:
                continue
            p = q["price"]
            rows_by_asset[asset.id] = [[today, p, p, p, p, None]]
        for asset_id, rows in rows_by_asset.items():
            self.store.upsert_bars(asset_id, "day", rows)
            self._spark_cache.pop(asset_id, None)

    # ------------------------------------------------------------ 对外快照
    def _spark(self, asset_id: str, price: Optional[float]) -> List[float]:
        cached = self._spark_cache.get(asset_id)
        now = time.time()
        if not cached or now - cached[0] > 300:
            closes = self.store.recent_closes(asset_id, 40)
            self._spark_cache[asset_id] = (now, closes)
            cached = (now, closes)
        series = list(cached[1])
        if price is not None:
            if series and abs(series[-1] - price) < 1e-9:
                pass
            else:
                series = (series + [price])[-40:]
        # 数据点太少时不画迷你图，避免出现两点斜线造成误读
        if len(series) < 5:
            return []
        return series

    def snapshot(self) -> Dict[str, Any]:
        now = datetime.now()
        with self._lock:
            quotes = dict(self.quotes)
            updated_at = self.updated_at
            refreshed = self.refreshed_ts
            last_ok = dict(self._last_ok)
            errors = list(self._errors)
        ts_now = time.time()

        groups: List[Dict[str, Any]] = []
        for group in config.GROUPS:
            items: List[Dict[str, Any]] = []
            for asset in config.assets_of(group):
                q = quotes.get(asset.id)
                if not q:
                    items.append({
                        "id": asset.id, "name": asset.name, "group": asset.group, "kind": asset.kind,
                        "price": None, "change": None, "pct": None, "tag": asset.tag,
                        "decimals": asset.decimals, "unit": asset.unit, "spark": [], "stale": True,
                        "source": None, "ts": None, "missing": True,
                    })
                    continue
                ok_age = ts_now - last_ok.get(asset.id, 0)
                items.append({
                    "id": asset.id,
                    "name": asset.name,
                    "group": asset.group,
                    "kind": asset.kind,
                    "price": q.get("price"),
                    "change": q.get("change"),
                    "pct": q.get("pct"),
                    "open": q.get("open"),
                    "high": q.get("high"),
                    "low": q.get("low"),
                    "prev_close": q.get("prev_close"),
                    "volume": q.get("volume"),
                    "amount": q.get("amount"),
                    "ts": q.get("ts"),
                    "source": q.get("source"),
                    "tag": asset.tag,
                    "decimals": asset.decimals,
                    "unit": asset.unit,
                    "spark": self._spark(asset.id, q.get("price")),
                    "stale": ok_age > 90,
                    "missing": False,
                })
            groups.append({"name": group, "icon": config.GROUP_ICON.get(group, ""), "assets": items})

        sessions = {g: session_status(g, now) for g in config.GROUPS}
        return {
            "updated_at": updated_at,
            "refreshed_at": datetime.fromtimestamp(refreshed).isoformat(timespec="seconds") if refreshed else None,
            "server_time": now.isoformat(timespec="seconds"),
            "sessions": sessions,
            "groups": groups,
            "hero": config.HERO_IDS,
            "health": http.health(),
            "store": self.store.stats(),
            "download": dict(self.download_state),
            "errors": errors,
            "refresh_seconds": self.refresh_seconds,
        }

    # ------------------------------------------------------------ 历史
    def history(self, asset_id: str, interval: str = "day", limit: int = 0) -> List[List[Any]]:
        asset = config.BY_ID.get(asset_id)
        if not asset:
            return []
        if asset.derived:
            return self._derived_history(asset, interval, limit)
        if asset.hist and asset.hist[0] == "snapshot":
            return self.store.get_bars(asset_id, "day", limit)
        if not asset.hist:
            return []
        key = f"{asset_id}:{interval}"
        now = time.time()
        cached = self._hist_cache.get(key)
        if cached and now - cached[0] < 600:
            bars = cached[1]
        else:
            bars: List[List[Any]] = []
            stored = self.store.get_bars(asset_id, interval)
            if stored and self._is_fresh(str(stored[-1][0]), interval):
                # 本地数据仍然新鲜：直接用，避免每次开图都打接口
                bars = stored
                self._hist_cache[key] = (now, bars)
            else:
                try:
                    bars = fetch_history(asset, interval)
                except Exception as exc:  # noqa: BLE001 - 远端失败不应影响界面
                    self._note_error(f"history:{asset_id}", exc)
                if bars:
                    self.store.upsert_bars(asset_id, interval, bars)
                    self._hist_cache[key] = (now, bars)
                    self._spark_cache.pop(asset_id, None)
                else:
                    # 远端不可用（限流/断网）时回落到本地已下载的数据
                    bars = stored
                    if cached and not bars:
                        bars = cached[1]
        if not bars and interval == "month":
            # 月线优先由（可能更长的）周线聚合，其次退回日线
            bars = resample_bars(self.history(asset_id, "week", 0), "month")
        if not bars and interval in ("week", "month"):
            # 只提供日线的数据源：本地聚合出周线 / 月线
            bars = resample_bars(self.history(asset_id, "day", 0), interval)
        return bars[-limit:] if limit else bars

    def _is_fresh(self, last_ts: str, interval: str) -> bool:
        """本地最后一根 K 线是否够新；过期才回源拉取。"""
        try:
            last = date.fromisoformat(str(last_ts)[:10])
        except ValueError:
            return False
        max_age = {"day": 4, "week": 10, "month": 45}.get(interval, 4)
        return (date.today() - last).days <= max_age

    def _derived_history(self, asset: Asset, interval: str, limit: int) -> List[List[Any]]:
        """派生指标（如 10Y-2Y 利差）由两条收益率曲线按日期对齐相减得到。"""
        if not asset.derived:
            return []
        left, right = asset.derived
        la = {row[0]: row[4] for row in self.history(left, "day", 0) if row[4] is not None}
        lb = {row[0]: row[4] for row in self.history(right, "day", 0) if row[4] is not None}
        bars: List[List[Any]] = []
        for day in sorted(set(la) & set(lb)):
            value = round(la[day] - lb[day], 6)
            bars.append([day, value, value, value, value, None])
        if interval != "day":
            bars = resample_bars(bars, interval)
        return bars[-limit:] if limit else bars

    def history_payload(self, asset_id: str, interval: str = "day", limit: int = 0) -> Dict[str, Any]:
        asset = config.BY_ID.get(asset_id)
        if not asset:
            return {"error": "unknown asset"}
        bars = self.history(asset_id, interval, limit)
        source = "derived" if asset.derived else (asset.hist[0] if asset.hist else None)
        return {
            "id": asset.id,
            "name": asset.name,
            "kind": asset.kind,
            "unit": asset.unit,
            "decimals": asset.decimals,
            "interval": interval,
            "bars": bars,
            "source": source,
            "perf": self.period_performance(asset),
        }

    def period_performance(self, asset: Asset) -> List[Dict[str, Any]]:
        """区间表现始终按完整日线计算，不受当前图表窗口限制。"""
        daily = self.history(asset.id, "day", 0)
        if len(daily) < 2:
            return []
        last = daily[-1]
        if not last[4]:
            return []
        try:
            last_day = date.fromisoformat(str(last[0])[:10])
        except ValueError:
            return []
        is_yield = asset.kind in ("bond", "spread")
        out: List[Dict[str, Any]] = []
        for label, days in (("1周", 7), ("1月", 30), ("3月", 90), ("6月", 180), ("1年", 365)):
            target = (last_day - timedelta(days=days)).isoformat()
            base = next((b for b in reversed(daily) if str(b[0])[:10] <= target and b[4]), None)
            if base is None:
                out.append({"label": label, "value": None})
                continue
            if is_yield:
                # 收益率与利差用 bp 表述
                out.append({"label": label, "value": round((last[4] - base[4]) * 100, 2)})
            else:
                out.append({"label": label, "value": round((last[4] / base[4] - 1) * 100, 3)})
        return out

    # ------------------------------------------------------------ 分时
    def intraday(self, asset_id: str) -> Dict[str, Any]:
        """当日分时（分钟级）；债券等无盘中数据的标的返回空序列。"""
        asset = config.BY_ID.get(asset_id)
        if not asset:
            return {"error": "unknown asset"}
        now = time.time()
        cached = self._intraday_cache.get(asset_id)
        if cached and now - cached[0] < 30:
            return cached[1]
        raw = fetch_intraday(asset) or {}
        with self._lock:
            quote = dict(self.quotes.get(asset_id) or {})
        payload = {
            "id": asset.id,
            "name": asset.name,
            "date": raw.get("date"),
            "points": raw.get("points") or [],
            "prev_close": quote.get("prev_close"),
            "price": quote.get("price"),
            "decimals": asset.decimals,
            "unit": asset.unit,
        }
        self._intraday_cache[asset_id] = (now, payload)
        return payload

    # ------------------------------------------------------------ 关联分析
    ANALYSIS_POINTS = {"1Y": 250, "2Y": 500, "3Y": 750, "ALL": 0}
    MAX_ANALYSIS_POINTS = 1600

    def analysis(self, ids: Sequence[str], window: int = 60, rng: str = "1Y",
                 weights: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
        """多标的关联分析：滚动窗口下 6 种相关性指标 + 加权综合指标。"""
        assets = [config.BY_ID[i] for i in dict.fromkeys(ids) if i in config.BY_ID][:8]
        if len(assets) < 2:
            return {"error": "请至少选择两个标的"}
        try:
            window = max(5, min(int(window), 250))
        except (TypeError, ValueError):
            window = 60
        if rng not in self.ANALYSIS_POINTS:
            rng = "1Y"
        weight_map = normalize_weights(weights or DEFAULT_WEIGHTS)
        weight_key = ",".join(f"{k}:{weight_map[k]:.3f}" for k, _, _ in METRICS)

        cache_key = f"{','.join(a.id for a in assets)}|{window}|{rng}|{weight_key}"
        cached = self._analysis_cache.get(cache_key)
        now = time.time()
        if cached and now - cached[0] < 300:
            return cached[1]

        closes: Dict[str, Dict[str, float]] = {}
        for asset in assets:
            bars = self.history(asset.id, "day", 0)
            if len(bars) >= 60:
                closes[asset.id] = {str(b[0])[:10]: float(b[4]) for b in bars if b[4] is not None}
        assets = [a for a in assets if a.id in closes]
        if len(assets) < 2:
            return {"error": "所选标的历史数据不足（至少需要 60 个交易日）"}

        common = sorted(set.intersection(*[set(v) for v in closes.values()]))
        if len(common) < window + 10:
            return {"error": f"共同交易日不足（仅 {len(common)} 天），请缩短窗口或换标的"}

        if len(common) > self.MAX_ANALYSIS_POINTS + window:
            common = common[-(self.MAX_ANALYSIS_POINTS + window):]

        matrix = {a.id: np.array([closes[a.id][d] for d in common], dtype=float) for a in assets}
        rets = {aid: np.diff(np.log(np.maximum(m, 1e-12))) for aid, m in matrix.items()}

        pairs = list(combinations(assets, 2))
        per_metric: Dict[str, List[np.ndarray]] = {k: [] for k, _, _ in METRICS}
        pair_rows: List[Dict[str, Any]] = []
        for left, right in pairs:
            result = rolling_metrics(rets[left.id], rets[right.id], window)
            for key, _, _ in METRICS:
                per_metric[key].append(result[key])
            row: Dict[str, Any] = {"a": left.id, "b": right.id,
                                   "a_name": left.name, "b_name": right.name}
            for key, _, _ in METRICS:
                series = result[key]
                valid = series[~np.isnan(series)] if series.size else np.array([])
                row[key] = round(float(valid[-1]), 4) if valid.size else None
            pair_rows.append(row)

        dates = common[window:]
        total = len(dates)
        limit = self.ANALYSIS_POINTS[rng]
        start = max(0, total - limit) if limit else 0
        display_dates = dates[start:]

        metrics_out: List[Dict[str, Any]] = []
        summaries: List[Dict[str, Any]] = []
        averaged = {key: average_series(per_metric[key]) for key, _, _ in METRICS}
        for key, _, _ in METRICS:
            if averaged[key].size < total:  # 理论上不会发生，防御性补齐
                averaged[key] = np.concatenate([np.full(total - averaged[key].size, np.nan), averaged[key]])

        # 综合指标：各指标按权重合成，放在最前作为统一指标
        composite = composite_series(averaged, weight_map)
        metrics_out.append({
            "key": COMPOSITE_KEY,
            "label": COMPOSITE_LABEL,
            "desc": COMPOSITE_DESC,
            "composite": True,
            "values": [None if np.isnan(v) else round(float(v), 4) for v in composite[start:]],
        })
        summaries.append({"key": COMPOSITE_KEY, "label": COMPOSITE_LABEL, "composite": True,
                          **summary(composite[start:])})

        for key, label, desc in METRICS:
            values = averaged[key][start:]
            metrics_out.append({
                "key": key,
                "label": label,
                "desc": desc,
                "values": [None if np.isnan(v) else round(float(v), 4) for v in values],
            })
            summaries.append({"key": key, "label": label, **summary(values)})

        scatter = None
        if len(assets) == 2:
            ra = rets[assets[0].id][-250:]
            rb = rets[assets[1].id][-250:]
            scatter = {
                "a": assets[0].id, "b": assets[1].id,
                "a_name": assets[0].name, "b_name": assets[1].name,
                "points": [[round(float(x), 6), round(float(y), 6)] for x, y in zip(ra, rb)],
            }

        payload = {
            "ids": [a.id for a in assets],
            "names": {a.id: a.name for a in assets},
            "tags": {a.id: a.tag for a in assets},
            "window": window,
            "range": rng,
            "dates": display_dates,
            "metrics": metrics_out,
            "summary": summaries,
            "pairs": pair_rows,
            "scatter": scatter,
            "weights": weight_map,
            "n_common": len(common),
            "period": [common[0], common[-1]],
        }
        self._analysis_cache[cache_key] = (now, payload)
        return payload

    # ------------------------------------------------------------ 批量下载
    def start_download(self, intervals: Sequence[str] = ("day",)) -> bool:
        with self._lock:
            if self.download_state["running"]:
                return False
            targets = [a for a in config.ASSETS if a.hist]
            self.download_state = {
                "running": True, "done": 0, "total": len(targets), "current": "",
                "errors": [], "started_at": datetime.now().isoformat(timespec="seconds"),
                "finished_at": None,
            }
        threading.Thread(target=self._download_worker, args=(list(intervals),), name="download", daemon=True).start()
        return True

    def _download_worker(self, intervals: Sequence[str]) -> None:
        export_dir = os.path.join(os.path.dirname(self.store.path), "exports")
        targets = [a for a in config.ASSETS if a.hist]
        for asset in targets:
            if self._stop.is_set():
                break
            with self._lock:
                self.download_state["current"] = f"{asset.name} ({asset.id})"
            try:
                if asset.hist and asset.hist[0] == "snapshot":
                    self._persist_snapshots()
                else:
                    for interval in intervals:
                        bars = fetch_history(asset, interval)
                        if bars:
                            self.store.upsert_bars(asset.id, interval, bars)
                    for interval in intervals:
                        self.store.export_csv(asset.id, interval, export_dir)
            except Exception as exc:  # noqa: BLE001
                with self._lock:
                    self.download_state["errors"].append({"id": asset.id, "error": str(exc)[:160]})
            finally:
                with self._lock:
                    self.download_state["done"] += 1
                self._hist_cache = {k: v for k, v in self._hist_cache.items() if not k.startswith(asset.id)}

        # 阶段二：派生指标落地 + 用日线补全周/月线，保证每个标的都有完整周期
        with self._lock:
            self.download_state["current"] = "补全派生指标与周/月线"
        try:
            self._finalize_history(export_dir)
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self.download_state["errors"].append({"id": "finalize", "error": str(exc)[:160]})

        with self._lock:
            self.download_state["running"] = False
            self.download_state["current"] = ""
            self.download_state["finished_at"] = datetime.now().isoformat(timespec="seconds")

    def _finalize_history(self, export_dir: str) -> None:
        """下载收尾：派生指标写入本地库，缺周/月线的标的用日线聚合补齐。"""
        for asset in config.ASSETS:
            if asset.derived:
                daily = self._derived_history(asset, "day", 0)
                if daily:
                    self.store.upsert_bars(asset.id, "day", daily)
                    self.store.export_csv(asset.id, "day", export_dir)
                for interval in ("week", "month"):
                    bars = resample_bars(daily, interval)
                    if bars:
                        self.store.upsert_bars(asset.id, interval, bars)
                        self.store.export_csv(asset.id, interval, export_dir)
                continue
            if not asset.hist:
                continue
            daily = self.store.get_bars(asset.id, "day")
            week = self.store.get_bars(asset.id, "week")
            if asset.hist[0] == "tencent":
                # 腾讯源本身提供正式周/月线，不覆盖
                continue
            # 日线比现有周线起点更早时，用日线重建周线（中债日线可回溯到 2002 年）
            if daily and (len(week) < 2 or daily[0][0] < week[0][0]):
                rebuilt = resample_bars(daily, "week")
                if rebuilt:
                    week = rebuilt
                    self.store.delete_bars(asset.id, "week")
                    self.store.upsert_bars(asset.id, "week", week)
                    self.store.export_csv(asset.id, "week", export_dir)
            month = resample_bars(week if len(week) >= 2 else daily, "month")
            if month:
                self.store.delete_bars(asset.id, "month")
                self.store.upsert_bars(asset.id, "month", month)
                self.store.export_csv(asset.id, "month", export_dir)
