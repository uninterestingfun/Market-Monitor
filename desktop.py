"""桌面版入口：本地起服务 + 独立应用窗口（无地址栏 / 无标签页）。

打包成 exe 后双击即可运行，不依赖本机 Python：
    python desktop.py                # 开发模式，直接开窗口
    python desktop.py --no-window    # 只起服务，便于排错
    python desktop.py --selftest     # 自检：起服务 → 请求接口 → 退出

窗口策略（按顺序降级）：
    1. Edge / Chrome 的 --app 模式：独立窗口、独立任务栏项，不带浏览器界面
    2. 系统默认浏览器
"""
from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from market_monitor.server import create_server  # noqa: E402
from market_monitor.service import MarketService  # noqa: E402
from market_monitor.store import Store  # noqa: E402

APP_NAME = "MarketMonitor"
APP_TITLE = "市场监测 · Markets Monitor"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_dir() -> str:
    """可写数据目录：打包后放在 %LOCALAPPDATA%，避免 Program Files 只读。"""
    if is_frozen():
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, APP_NAME)
    return os.path.join(ROOT, "data")


def setup_logging(directory: str) -> None:
    os.makedirs(directory, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        os.path.join(directory, "app.log"), maxBytes=1_000_000, backupCount=2, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def find_browser() -> str | None:
    """按优先级找一个 Chromium 内核浏览器（支持 --app 模式）。"""
    candidates = [
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Google\Chrome\Application\chrome.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Microsoft\Edge\Application\msedge.exe"),
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


def wait_ready(url: str, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url + "api/overview", timeout=2) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.25)
    return False


def _alive(url: str, timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(url + "api/overview", timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def running_port(directory: str) -> int | None:
    """已有实例在跑就返回它的端口，避免重复双击起两个服务互相抢端口。"""
    lock = os.path.join(directory, "instance.json")
    try:
        with open(lock, "r", encoding="utf-8") as fh:
            port = int(json.load(fh).get("port") or 0)
    except Exception:
        return None
    if port and _alive(f"http://127.0.0.1:{port}/"):
        return port
    return None


def write_lock(directory: str, port: int) -> str:
    path = os.path.join(directory, "instance.json")
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"port": port, "pid": os.getpid()}, fh)
    except Exception:
        logging.warning("无法写入实例锁文件 %s", path)
    return path


def open_window(url: str, directory: str) -> str:
    browser = find_browser()
    if browser:
        profile = os.path.join(directory, "ui-profile")
        os.makedirs(profile, exist_ok=True)
        args = [
            browser,
            f"--app={url}",
            "--window-size=1500,950",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-features=Translate,MediaRouter",
        ]
        logging.info("打开应用窗口: %s", browser)
        proc = subprocess.Popen(args)
        proc.wait()
        return "app"
    logging.warning("未找到 Edge/Chrome，改用默认浏览器")
    webbrowser.open(url)
    return "browser"


def fatal(message: str) -> None:
    logging.error("启动失败: %s", message)
    if is_frozen():
        try:
            import tkinter as tk
            from tkinter import messagebox

            root = tk.Tk()
            root.withdraw()
            messagebox.showerror(APP_TITLE, message)
            root.destroy()
        except Exception:
            pass
    else:
        print(message, file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=APP_TITLE)
    parser.add_argument("--port", type=int, default=8666, help="本地端口（0 表示随机）")
    parser.add_argument("--no-window", action="store_true", help="只启动服务，不开窗口")
    parser.add_argument("--selftest", action="store_true", help="自检后退出")
    args = parser.parse_args(argv)

    directory = app_dir()
    setup_logging(directory)
    logging.info("启动 %s（frozen=%s）目录=%s", APP_TITLE, is_frozen(), directory)

    # 单实例：已有窗口在跑时，只再开一个窗口指向它，不重复起服务
    if not args.selftest and not args.no_window:
        existing = running_port(directory)
        if existing:
            logging.info("已有实例运行在 %s，直接打开窗口", existing)
            open_window(f"http://127.0.0.1:{existing}/", directory)
            return 0

    store = None
    service = None
    httpd = None
    lock_path = None
    try:
        store = Store(os.path.join(directory, "market.db"))
        service = MarketService(store)
        service.start()
        httpd, port = create_server(service, "127.0.0.1", args.port if args.port else 0)
        url = f"http://127.0.0.1:{port}/"
        threading.Thread(target=httpd.serve_forever, name="http", daemon=True).start()
        if not args.selftest:
            lock_path = write_lock(directory, port)
        logging.info("服务已启动 %s", url)

        if args.selftest:
            ok = wait_ready(url, 30)
            detail = ""
            if ok:
                try:
                    import json

                    with urllib.request.urlopen(url + "api/overview", timeout=10) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
                    detail = f"assets={sum(len(g['assets']) for g in data['groups'])}"
                except Exception as exc:  # noqa: BLE001
                    detail = f"overview error: {exc}"
            print(f"SELFTEST {'OK' if ok else 'FAIL'} {url} {detail}")
            return 0 if ok else 1

        if args.no_window:
            print(f"服务已启动: {url}  (Ctrl+C 退出)")
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass
            return 0

        if not wait_ready(url, 30):
            fatal("本地服务启动超时，请查看日志：" + os.path.join(directory, "app.log"))
            return 2

        open_window(url, directory)
        return 0
    except Exception as exc:  # noqa: BLE001
        logging.exception("运行异常")
        fatal(f"{type(exc).__name__}: {exc}\n\n日志：{os.path.join(directory, 'app.log')}")
        return 1
    finally:
        logging.info("退出中…")
        if service:
            service.stop()
        if httpd:
            try:
                httpd.shutdown()
                httpd.server_close()
            except Exception:
                pass
        if store:
            try:
                store.close()
            except Exception:
                pass
        if lock_path:
            try:
                os.remove(lock_path)
            except Exception:
                pass
        logging.info("已退出")


if __name__ == "__main__":
    sys.exit(main())
