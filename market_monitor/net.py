"""轻量 HTTP 客户端：连接复用、超时、重试、按主机限速与健康状态记录。"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional

import requests

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
}


class HttpClient:
    """线程安全的同步请求封装，附带简单的失败退避与统计。"""

    def __init__(self, timeout: float = 8.0, retries: int = 2) -> None:
        self.timeout = timeout
        self.retries = retries
        self._local = threading.local()
        self._lock = threading.Lock()
        self._last_call: Dict[str, float] = {}
        self.stats: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------ 内部
    def _session(self) -> requests.Session:
        s = getattr(self._local, "session", None)
        if s is None:
            s = requests.Session()
            s.headers.update(DEFAULT_HEADERS)
            self._local.session = s
        return s

    def _throttle(self, host: str, min_interval: float) -> None:
        if min_interval <= 0:
            return
        with self._lock:
            last = self._last_call.get(host, 0.0)
            wait = last + min_interval - time.monotonic()
            self._last_call[host] = time.monotonic() + max(wait, 0.0)
        if wait > 0:
            time.sleep(wait)

    def _mark(self, host: str, ok: bool, latency_ms: int, error: Optional[str]) -> None:
        with self._lock:
            st = self.stats.setdefault(host, {"ok": 0, "fail": 0})
            st["ok" if ok else "fail"] += 1
            st["latency_ms"] = latency_ms
            st["last_ts"] = time.time()
            if error:
                st["last_error"] = error[:160]
                st["last_error_ts"] = time.time()
            else:
                st["last_error"] = None

    # ------------------------------------------------------------------ 对外
    def get(
        self,
        url: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        encoding: Optional[str] = None,
        timeout: Optional[float] = None,
        min_interval: float = 0.0,
        retries: Optional[int] = None,
    ) -> requests.Response:
        host = url.split("/")[2]
        attempts = self.retries if retries is None else retries
        last_err: Optional[Exception] = None
        for attempt in range(attempts + 1):
            self._throttle(host, min_interval)
            started = time.monotonic()
            try:
                resp = self._session().get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=timeout or self.timeout,
                )
                resp.raise_for_status()
                if encoding:
                    resp.encoding = encoding
                self._mark(host, True, int((time.monotonic() - started) * 1000), None)
                return resp
            except Exception as exc:  # noqa: BLE001 - 统一降级处理
                last_err = exc
                self._mark(host, False, int((time.monotonic() - started) * 1000), str(exc))
                if attempt < attempts:
                    time.sleep(0.35 * (attempt + 1))
        raise last_err if last_err else RuntimeError("request failed")

    def get_text(self, url: str, **kwargs: Any) -> str:
        return self.get(url, **kwargs).text

    def get_json(self, url: str, **kwargs: Any) -> Any:
        return self.get(url, **kwargs).json()

    def health(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            now = time.time()
            out: Dict[str, Dict[str, Any]] = {}
            for host, st in self.stats.items():
                ok = st.get("ok", 0)
                fail = st.get("fail", 0)
                out[host] = {
                    "ok": ok,
                    "fail": fail,
                    "latency_ms": st.get("latency_ms", 0),
                    "healthy": st.get("last_error") is None,
                    "last_error": st.get("last_error"),
                    "age_s": int(now - st["last_ts"]) if st.get("last_ts") else None,
                }
            return out


http = HttpClient()
