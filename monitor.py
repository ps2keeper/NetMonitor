# NetMonitor - Clash Verge TUN 真实进程流量精准监控服务
"""命令行入口。

python monitor.py                 后台启动采集与 Web 看板（Windows 下隐藏控制台）
python monitor.py --show          调试模式，保留控制台与日志输出
python monitor.py --open          打开 Web 看板（服务未启动时等待其就绪）
python monitor.py --mini          启动桌面悬浮窗
python monitor.py --stop          停止本程序的后台服务与悬浮窗
python monitor.py --autostart     开启开机自启（Windows）
python monitor.py --no-autostart  取消开机自启
python monitor.py --cleanup DAYS  删除 DAYS 天前的全部数据并压缩数据库
"""
from __future__ import annotations

import argparse
import errno
import logging
import logging.handlers
import os
import sys
import time
import urllib.request
import webbrowser
from datetime import datetime

# 嵌入式/隔离模式的 Python 不会自动把脚本目录加入 sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nm_config  # noqa: E402
import nm_windows  # noqa: E402
import storage  # noqa: E402

logger = logging.getLogger("netmonitor")


def setup_logging(show_console: bool) -> None:
    handlers = []
    try:
        nm_config.ensure_data_dir()
        handlers.append(logging.handlers.RotatingFileHandler(
            nm_config.MONITOR_LOG, maxBytes=1_000_000, backupCount=2, encoding="utf-8"))
    except OSError:
        pass
    if show_console or not handlers:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


def _port_in_use(error: OSError) -> bool:
    return getattr(error, "winerror", None) == 10048 or error.errno == errno.EADDRINUSE


def wait_until_ready(timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{nm_config.WEB_PORT}/api/health"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1):
                return True
        except OSError:
            time.sleep(0.3)
    return False


def open_dashboard() -> int:
    if not wait_until_ready():
        print(f"NetMonitor 服务尚未就绪：{nm_config.dashboard_url()}")
    theme = nm_config.load_json_config().get("theme")
    webbrowser.open(nm_config.dashboard_url(theme))
    return 0


def cleanup(days: int) -> int:
    if days < 1:
        print("保留天数至少为 1")
        return 2
    storage.init_db(nm_config.DB_FILE)
    conn = storage.connect(nm_config.DB_FILE, timeout=30)
    try:
        removed = storage.prune(conn, datetime.now().date(), days, days)
        storage.vacuum(conn)
    finally:
        conn.close()
    print(f"已删除 {days} 天前的 {removed} 行数据，并完成数据库压缩：{nm_config.DB_FILE}")
    return 0


def run_service(show_console: bool) -> int:
    from collector import TrafficCollector
    from web import ThreadedServer

    if not show_console and os.environ.get("HIDE_CONSOLE", "1") == "1":
        nm_windows.hide_console_window()
    setup_logging(show_console)

    collector = TrafficCollector(nm_config.DB_FILE)
    try:
        server = ThreadedServer(("127.0.0.1", nm_config.WEB_PORT), collector, nm_config.DB_FILE)
    except OSError as e:
        if _port_in_use(e):
            print(f"[提示] 端口 {nm_config.WEB_PORT} 已被占用，NetMonitor 可能已经在运行：{nm_config.dashboard_url()}")
            return 0
        logger.exception("无法启动 Web 服务")
        nm_windows.show_error(f"NetMonitor 无法监听端口 {nm_config.WEB_PORT}：{e}")
        return 1

    try:
        storage.init_db(nm_config.DB_FILE)
    except Exception as e:
        logger.exception("无法打开数据库 %s", nm_config.DB_FILE)
        server.server_close()
        nm_windows.show_error(
            f"NetMonitor 无法打开数据库：\n{nm_config.DB_FILE}\n\n{e}\n\n"
            "可通过环境变量 DB_FILE 指定其他位置。")
        return 1

    collector.start()
    nm_windows.write_pid(nm_config.MONITOR_PID)
    logger.info("NetMonitor 已启动：%s（数据库 %s）", nm_config.dashboard_url(), nm_config.DB_FILE)
    print("=====================================================")
    print("  NetMonitor 流量监控服务已运行!")
    print(f"  Web 仪表盘访问地址: {nm_config.dashboard_url()}")
    print(f"  数据库: {nm_config.DB_FILE}")
    print("=====================================================")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止服务...")
    finally:
        collector.stop()
        collector.join(timeout=5)
        server.server_close()
        nm_windows.remove_pid(nm_config.MONITOR_PID)
        logger.info("NetMonitor 已停止")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="NetMonitor - Clash Verge TUN 进程流量监控")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--stop", action="store_true", help="停止后台服务与悬浮窗")
    group.add_argument("--mini", action="store_true", help="启动桌面悬浮窗")
    group.add_argument("--open", action="store_true", help="打开 Web 看板")
    group.add_argument("--autostart", action="store_true", help="开启开机自启（Windows）")
    group.add_argument("--no-autostart", action="store_true", help="取消开机自启")
    group.add_argument("--cleanup", type=int, metavar="DAYS", help="删除 DAYS 天前的数据并压缩数据库")
    parser.add_argument("--show", action="store_true", help="保留控制台窗口与日志输出")
    args = parser.parse_args(argv)

    if args.stop:
        messages = nm_windows.stop_all()
        print("\n".join(messages) if messages else "未发现正在运行的 NetMonitor 进程。")
        return 0
    if args.mini:
        from web import launch_widget
        launch_widget()
        print("[OK] NetMonitor 桌面悬浮窗已启动！")
        return 0
    if args.open:
        return open_dashboard()
    if args.autostart:
        if not nm_config.IS_WINDOWS:
            print("开机自启功能仅支持 Windows 系统。")
            return 1
        try:
            print(f"[OK] 已开启 NetMonitor 开机自动启动：{nm_windows.enable_autostart()}")
        except OSError as e:
            print(f"[ERROR] 设置开机自启失败：{e}")
            return 1
        return 0
    if args.no_autostart:
        print("[OK] 已取消 NetMonitor 开机自动启动。" if nm_windows.disable_autostart()
              else "[INFO] 当前未开启 NetMonitor 开机自启。")
        return 0
    if args.cleanup is not None:
        return cleanup(args.cleanup)
    return run_service(args.show)


if __name__ == "__main__":
    sys.exit(main())
