"""内置 HTTP 服务：静态页面 + JSON API（仅标准库，无额外依赖）。"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import socket
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, unquote, urlparse

from . import bonds, gold, gold_spot, industry_map, macro, sectors, update, us_sectors
from .service import MarketService


def _web_dir() -> str:
    """开发时用包内目录；PyInstaller 打包后资源解压在 _MEIPASS 下。"""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return os.path.join(meipass, "market_monitor", "web")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")


WEB_DIR = _web_dir()

RANGE_LIMITS = {"1M": 22, "3M": 66, "6M": 130, "1Y": 250, "3Y": 156, "ALL": 0}
RANGE_INTERVAL = {"1M": "day", "3M": "day", "6M": "day", "1Y": "day", "3Y": "week", "ALL": "month"}


class Handler(BaseHTTPRequestHandler):
    service: MarketService
    server_version = "MarketMonitor/1.0"
    protocol_version = "HTTP/1.1"

    # ---------------------------------------------------------- 基础工具
    def log_message(self, fmt: str, *args: Any) -> None:  # 静默访问日志
        return

    def _send(self, status: int, body: bytes, content_type: str, extra: Optional[Dict[str, str]] = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _file(self, path: str) -> None:
        if not os.path.isfile(path):
            self._send(404, b"Not Found", "text/plain; charset=utf-8")
            return
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if path.endswith(".js"):
            ctype = "application/javascript; charset=utf-8"
        elif path.endswith(".css"):
            ctype = "text/css; charset=utf-8"
        elif path.endswith(".html"):
            ctype = "text/html; charset=utf-8"
        with open(path, "rb") as fh:
            body = fh.read()
        self._send(200, body, ctype)

    # ---------------------------------------------------------- 路由
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            self._file(os.path.join(WEB_DIR, "index.html"))
            return
        if path.startswith("/static/"):
            rel = path[len("/static/"):].replace("..", "")
            self._file(os.path.join(WEB_DIR, rel))
            return
        if path == "/api/ping":
            self.service.ping()
            self._json({"ok": True})
            return
        if path == "/api/bye":
            self.service.farewell()
            self._json({"ok": True})
            return
        if path == "/api/overview":
            self._json(self.service.snapshot())
            return
        if path == "/api/history":
            asset_id = (query.get("id") or [""])[0]
            rng = (query.get("range") or ["1Y"])[0].upper()
            interval = (query.get("interval") or [RANGE_INTERVAL.get(rng, "day")])[0]
            limit = int((query.get("limit") or [RANGE_LIMITS.get(rng, 250)])[0])
            self._json(self.service.history_payload(asset_id, interval, limit))
            return
        if path == "/api/intraday":
            self._json(self.service.intraday((query.get("id") or [""])[0]))
            return
        if path == "/api/flow":
            aid = (query.get("id") or [""])[0]
            try:
                limit = int((query.get("limit") or ["120"])[0])
            except ValueError:
                limit = 120
            self._json(self.service.flow(aid, max(20, min(limit, 500))))
            return
        if path == "/api/flow/markets":
            self._json(self.service.market_flow())
            return
        if path == "/api/macro":
            force = (query.get("refresh") or [""])[0] == "1"
            self._json(macro.snapshot(force=force))
            return
        if path == "/api/stock":
            code = (query.get("code") or [""])[0].strip().lower()
            if not re.match(r"^(sh|sz|bj)\d{6}$", code):
                self._json({"error": "invalid code"})
                return
            self._json(self.service.stock(code))
            return
        if path == "/api/sectors":
            market = (query.get("market") or ["cn"])[0].lower()
            if market == "us":
                self._json({"market": "us", "industries": us_sectors.industries()})
            else:
                scheme = (query.get("scheme") or ["sina"])[0]
                detail = (query.get("detail") or [""])[0] == "1"
                if scheme == "csi":
                    data = industry_map.build_tree(detail)
                    if data.get("ok"):
                        self._json({"market": "cn", "scheme": "csi", "tree": data["tree"],
                                    "industries": data["industries"],
                                    "covered": data["covered"], "total": data["total"]})
                    else:
                        self._json({"market": "cn", "scheme": "csi", "error": data.get("error"),
                                    "path": data.get("path"), "tree": [], "industries": [],
                                    "catalog_rows": data.get("catalog_rows", 0),
                                    "catalog_path": data.get("catalog_path")})
                    return
                self._json({"market": "cn", "scheme": "sina",
                            "industries": sectors.industries(), "tree": []})
            return
        if path == "/api/sectors/board":
            node = (query.get("node") or [""])[0]
            market = (query.get("market") or ["cn"])[0].lower()
            try:
                limit = int((query.get("limit") or ["50"])[0])
            except ValueError:
                limit = 50
            limit = max(10, min(limit, 100))
            if market == "us":
                self._json(us_sectors.board(node, limit))
            elif (query.get("scheme") or ["gbt"])[0] == "csi":
                self._json(industry_map.board(node) or {"error": "unknown node", "members": []})
            else:
                self._json(sectors.board(node, limit))
            return
        if path == "/api/update/check":
            self._json(update.check())
            return
        if path == "/api/update/start":
            self._json(update.start_download())
            return
        if path == "/api/update/status":
            self._json(update.status())
            return
        if path == "/api/update/apply":
            self._json(update.apply_update())
            return
        if path == "/api/gold/spot":
            self._json(gold_spot.snapshot())
            return
        if path == "/api/gold":
            self._json(gold.snapshot(self.service.gold_price_by_year()))
            return
        if path == "/api/bonds":
            self._json(bonds.snapshot(self.service.quote_map(), self.service.last_close))
            return
        if path == "/api/analysis":
            ids = [x for x in (query.get("ids") or [""])[0].split(",") if x]
            try:
                window = int((query.get("window") or ["60"])[0])
            except ValueError:
                window = 60
            rng = (query.get("range") or ["2Y"])[0].upper()
            weights = None
            raw = (query.get("weights") or [""])[0]
            if raw:
                try:
                    vals = [float(x) for x in raw.split(",") if x.strip()]
                    if len(vals) == 6:
                        weights = dict(zip(
                            ("pearson", "spearman", "kendall", "downside", "volatility", "leadlag"), vals))
                except ValueError:
                    weights = None
            self._json(self.service.analysis(ids, window, rng, weights))
            return
        if path == "/api/export":
            asset_id = (query.get("id") or [""])[0]
            interval = (query.get("interval") or ["day"])[0]
            bars = self.service.history(asset_id, interval)
            lines = ["date,open,high,low,close,volume"]
            for b in bars:
                lines.append(",".join("" if v is None else str(v) for v in b))
            body = ("\ufeff" + "\n".join(lines)).encode("utf-8")
            self._send(200, body, "text/csv; charset=utf-8", {
                "Content-Disposition": f'attachment; filename="{asset_id}_{interval}.csv"'
            })
            return
        if path == "/api/download/status":
            self._json(self.service.download_state)
            return
        if path in ("/favicon.ico",):
            self._send(204, b"", "image/x-icon")
            return
        self._send(404, b"Not Found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/download":
            ok = self.service.start_download(("day", "week", "month"))
            self._json({"started": ok, "state": self.service.download_state})
            return
        if parsed.path == "/api/refresh":
            try:
                self.service.refresh()
                self._json({"ok": True, "updated_at": self.service.updated_at})
            except Exception as exc:  # noqa: BLE001
                self._json({"ok": False, "error": str(exc)[:200]}, 500)
            return
        self._send(404, b"Not Found", "text/plain; charset=utf-8")


def _free_port(host: str, preferred: int) -> int:
    for port in range(preferred, preferred + 40):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((host, port))
                return port
            except OSError:
                continue
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def create_server(service: MarketService, host: str = "127.0.0.1", port: int = 8765):
    """创建（但不启动）HTTP 服务，返回 (httpd, 实际端口)；桌面版在后台线程里跑它。"""
    Handler.service = service
    actual = _free_port(host, port)
    httpd = ThreadingHTTPServer((host, actual), Handler)
    httpd.daemon_threads = True
    return httpd, actual


def serve(service: MarketService, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    httpd, actual = create_server(service, host, port)
    url = f"http://{host}:{actual}/"
    print(f"\n  市场监测面板已启动  →  {url}\n  按 Ctrl+C 停止\n")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  已停止。")
    finally:
        httpd.server_close()
