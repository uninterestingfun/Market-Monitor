"""市场监测工具入口。

用法：
    python run.py                 # 启动面板（默认 http://127.0.0.1:8765）
    python run.py serve --port 9000 --no-browser
    python run.py download        # 下载/更新全部标的历史数据到 data/
    python run.py check           # 自检：实时接口覆盖率
    python run.py check --history # 自检：额外验证历史接口
    python run.py list            # 列出全部监测标的
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from market_monitor import config  # noqa: E402
from market_monitor.providers import (  # noqa: E402
    fetch_cnbc, fetch_em_yields, fetch_history, fetch_sina, fetch_tencent,
)
from market_monitor.service import MarketService  # noqa: E402
from market_monitor.store import Store  # noqa: E402

DATA_DIR = os.path.join(ROOT, "data")
DB_PATH = os.path.join(DATA_DIR, "market.db")


def _banner() -> None:
    print("=" * 78)
    print("  市场监测 · Markets Monitor   A股 / 港股 / 美股 / 期货 / ETF / 利率 / 汇率")
    print("=" * 78)


def cmd_serve(args: argparse.Namespace) -> None:
    from market_monitor.server import serve

    _banner()
    store = Store(DB_PATH)
    service = MarketService(store, refresh_seconds=args.refresh)
    service.start()
    try:
        serve(service, host=args.host, port=args.port, open_browser=not args.no_browser)
    finally:
        service.stop()
        store.close()


def cmd_download(args: argparse.Namespace) -> None:
    _banner()
    store = Store(DB_PATH)
    service = MarketService(store)
    intervals = [x.strip() for x in args.intervals.split(",") if x.strip()]
    targets = [a for a in config.ASSETS if a.hist]
    export_dir = os.path.join(DATA_DIR, "exports")
    print(f"  目标 {len(targets)} 个标的，周期 {intervals}，数据库 {DB_PATH}\n")
    if not service.start_download(tuple(intervals)):
        print("  已有下载任务在进行，请稍后再试。")
        store.close()
        return

    last = -1
    is_tty = sys.stdout.isatty()
    while service.download_state["running"]:
        state = service.download_state
        if state["done"] != last:
            last = state["done"]
            line = f"  [{last:>2}/{state['total']}] {state['current'][:38]}"
            if is_tty:
                print(f"\r{line:<72}", end="", flush=True)
            elif last % 10 == 0:
                print(line)
        time.sleep(0.3)

    state = service.download_state
    if is_tty:
        print("\r" + " " * 72 + "\r", end="")
    print(f"  抓取完成：{state['done']}/{state['total']}，失败 {len(state['errors'])}")
    for err in state["errors"][:10]:
        print(f"   ! {err['id']}: {err['error']}")

    print(f"\n  {'ID':<15}{'名称':<15}{'日线':>7}{'周线':>6}{'月线':>6}")
    print("  " + "-" * 52)
    empty = []
    for asset in config.ASSETS:
        daily = store.count(asset.id, "day")
        week = store.count(asset.id, "week")
        month = store.count(asset.id, "month")
        if daily == 0:
            empty.append(asset.id)
        print(f"  {asset.id:<15}{asset.name:<15}{daily:>7}{week:>6}{month:>6}")
    print(f"\n  本地覆盖：{len(config.ASSETS) - len(empty)}/{len(config.ASSETS)} 个标的有日线数据"
          + (f"；缺失 {empty}" if empty else "；无缺失"))
    print(f"  CSV 导出：{export_dir}")
    print(f"  数据库：{DB_PATH}  {store.stats()}")
    store.close()


def cmd_check(args: argparse.Namespace) -> None:
    _banner()
    warns = config.validate_catalog()
    if warns:
        print("  目录告警：")
        for w in warns:
            print("   !", w)
    print(f"  标的数量：{len(config.ASSETS)}")

    sag = [a for a in config.ASSETS if a.sina]
    tag = [a for a in config.ASSETS if a.tencent]
    bnd = [a for a in config.ASSETS if a.cnbc]
    cny = [a for a in config.ASSETS if a.em]

    print("\n  ── 实时接口 ─────────────────────────────────────────────")
    t0 = time.time()
    sina_q = fetch_sina(sag)
    t1 = time.time()
    tencent_q = fetch_tencent(tag)
    t2 = time.time()
    cnbc_q = fetch_cnbc(bnd)
    t3 = time.time()
    em_q = fetch_em_yields(cny)
    t4 = time.time()
    print(f"  新浪   {len(sina_q):>2}/{len(sag):<2}  {t1 - t0:5.2f}s")
    print(f"  腾讯   {len(tencent_q):>2}/{len(tag):<2}  {t2 - t1:5.2f}s")
    print(f"  CNBC   {len(cnbc_q):>2}/{len(bnd):<2}  {t3 - t2:5.2f}s")
    print(f"  东财中债 {len(em_q):>2}/{len(cny):<2}  {t4 - t3:5.2f}s")

    print(f"\n  {'ID':<16}{'名称':<14}{'现价':>12}{'涨跌%':>9}  来源")
    print("  " + "-" * 66)
    pool = {}
    pool.update(sina_q)
    pool.update(tencent_q)
    pool.update(cnbc_q)
    pool.update(em_q)
    for a in config.ASSETS:
        if a.derived:
            qa, qb = pool.get(a.derived[0]), pool.get(a.derived[1])
            if qa and qb:
                pool[a.id] = {
                    "price": qa["price"] - qb["price"],
                    "pct": (qa.get("change") or 0) - (qb.get("change") or 0),
                    "source": "derived",
                }
    missing = []
    for a in config.ASSETS:
        q = pool.get(a.id)
        if not q:
            missing.append(a)
            print(f"  {a.id:<16}{a.name:<14}{'—':>12}{'—':>9}  MISSING")
            continue
        pct = q.get("pct")
        pct_s = f"{pct:+.2f}" if pct is not None else "—"
        print(f"  {a.id:<16}{a.name:<14}{q['price']:>12.4f}{pct_s:>9}  {q.get('source')}")

    if args.history:
        print("\n  ── 历史接口 ─────────────────────────────────────────────")
        for a in config.ASSETS:
            if not a.hist:
                continue
            try:
                bars = fetch_history(a, "day")
                first, last = (bars[0][0], bars[-1][0]) if bars else ("—", "—")
                flag = "OK " if bars else "空 "
                print(f"  {flag}{a.id:<16}{len(bars):>6} 条  {first} → {last}")
            except Exception as exc:  # noqa: BLE001
                print(f"  ERR {a.id:<16}{type(exc).__name__}: {str(exc)[:60]}")

    if missing:
        print(f"\n  ! {len(missing)} 个标的未取到实时数据：{', '.join(a.id for a in missing)}")
    print("\n  自检结束。")
    sys.exit(0 if not missing else 2)


def cmd_list(args: argparse.Namespace) -> None:
    _banner()
    for group in config.GROUPS:
        items = config.assets_of(group)
        print(f"\n  {group} ({len(items)})")
        for a in items:
            print(f"    {a.id:<16} {a.name:<16} hist={a.hist[0] if a.hist else '-'}")



def cmd_debt_template(args: argparse.Namespace) -> None:
    _banner()
    from market_monitor import sovereign_debt

    count = sovereign_debt.write_templates(args.dir)
    target = os.path.join(args.dir, "sovereign_debt")
    print(f"  已生成 {count} 个模板: {target}")
    print("  把各国财政部导出的数据填进对应 CSV，程序会自动优先使用。")



def cmd_gold_template(args: argparse.Namespace) -> None:
    _banner()
    from market_monitor import gold

    count = gold.write_templates(args.dir)
    target = os.path.join(args.dir, "gold_reserves")
    print(f"  已生成 {count} 个模板: {target}")
    print("  填入日期+吨数（至少两行），程序会自动算出同比买卖。")



def cmd_industry_template(args: argparse.Namespace) -> None:
    _banner()
    from market_monitor import industry_map

    path = industry_map.write_template(args.dir)
    print(f"  已生成模板: {path}")
    print("  把中证/中信的行业归属按 code,name,level1,level2,level3 填进去，刷新页面即可切换。")



def cmd_csi_template(args: argparse.Namespace) -> None:
    _banner()
    from market_monitor import industry_map

    industry_map.set_data_dir(args.dir)
    info = industry_map.write_catalog_template(args.dir)
    print(f"  已按中证分类目录生成 {info['rows']} 行模板: {info['path']}")
    print("  在每行 code 列填股票代码（可复制行挂多只），刷新页面即可看到中证行业热力图。")
    if info.get("backup"):
        print(f"  原文件已备份: {info['backup']}")



def cmd_pdf_import(args: argparse.Namespace) -> None:
    _banner()
    from market_monitor import pdf_industry

    rows = pdf_industry.parse_pdf(args.pdf)
    if not rows:
        print("  [失败] 未从 PDF 解析到数据，请确认文件结构")
        sys.exit(1)
    path = pdf_industry.write_mapping(rows, args.dir)
    stat = pdf_industry.summary(rows)
    print(f"  已生成: {path}")
    print(f"  覆盖 {stat['stocks']} 只股票 · {stat['level1']} 个门类 · {stat['level2']} 个二级 · {stat['level3']} 个三级")



def cmd_report(args: argparse.Namespace) -> None:
    _banner()
    from market_monitor import report_job
    from market_monitor.service import MarketService
    from market_monitor.store import Store

    store = Store(os.path.join(args.dir, "market.db"))
    service = MarketService(store)
    started = False
    if hasattr(service, "start"):
        service.start()
        started = True
    print("  正在收集行情并生成日报…")
    try:
        payload = report_job.generate(service, timeout=args.wait)
    finally:
        if started:
            try:
                service.stop()
            except Exception:
                pass
    if not payload.get("ok"):
        print(f"  [失败] {payload.get('error')}")
        sys.exit(1)
    paths = report_job.save(payload, root=os.path.dirname(os.path.abspath(__file__)))
    print(f"  日期: {payload['date']} · 章节 {len(payload.get('sections') or [])} 个")
    print(f"  已保存: {paths['repo']}")
    print(f"  本地归档: {paths['local']}")
    if args.push:
        result = report_job.push(root=os.path.dirname(os.path.abspath(__file__)))
        if result.get("ok") and result.get("pushed"):
            print("  已推送到远端仓库")
        elif result.get("ok"):
            print(f"  未推送：{result.get('detail')}")
        else:
            print(f"  [推送失败] {result.get('detail')}")
            sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="市场监测工具")
    sub = parser.add_subparsers(dest="command")

    p_serve = sub.add_parser("serve", help="启动 Web 面板")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8765)
    p_serve.add_argument("--refresh", type=float, default=4.0, help="服务端抓取间隔（秒）")
    p_serve.add_argument("--no-browser", action="store_true")
    p_serve.set_defaults(func=cmd_serve)

    p_dl = sub.add_parser("download", help="下载历史数据")
    p_dl.add_argument("--intervals", default="day,week,month")
    p_dl.set_defaults(func=cmd_download)

    p_check = sub.add_parser("check", help="接口自检")
    p_check.add_argument("--history", action="store_true")
    p_check.set_defaults(func=cmd_check)

    p_tpl = sub.add_parser("debt-template", help="生成各国主权债务 CSV 模板")
    p_tpl.add_argument("--dir", default="data")
    p_tpl.set_defaults(func=cmd_debt_template)

    p_gtpl = sub.add_parser("gold-template", help="生成各国黄金储备 CSV 模板")
    p_gtpl.add_argument("--dir", default="data")
    p_gtpl.set_defaults(func=cmd_gold_template)

    p_itpl = sub.add_parser("industry-template", help="生成自定义行业分类映射模板")
    p_itpl.add_argument("--dir", default="data")
    p_itpl.set_defaults(func=cmd_industry_template)

    p_ctpl = sub.add_parser("csi-template", help="按中证分类目录生成映射模板")
    p_ctpl.add_argument("--dir", default="data")
    p_ctpl.set_defaults(func=cmd_csi_template)

    p_pdf = sub.add_parser("pdf-import", help="从证监会行业分类 PDF 生成映射")
    p_pdf.add_argument("--pdf", default=os.path.join("data", "中上市行业分类.pdf"))
    p_pdf.add_argument("--dir", default="data")
    p_pdf.set_defaults(func=cmd_pdf_import)

    p_report = sub.add_parser("report", help="生成每日报告（可推送）")
    p_report.add_argument("--dir", default="data")
    p_report.add_argument("--push", action="store_true", help="生成后提交并推送 reports/")
    p_report.add_argument("--wait", type=float, default=120.0, help="等待行情的秒数")
    p_report.set_defaults(func=cmd_report)

    p_list = sub.add_parser("list", help="列出标的")
    p_list.set_defaults(func=cmd_list)

    args = parser.parse_args()
    if not getattr(args, "func", None):
        args = parser.parse_args(["serve"])
    _banner()
    print(f"  {datetime.now():%Y-%m-%d %H:%M:%S}")
    args.func(args)


if __name__ == "__main__":
    main()
