"""本地 Web 服务：看板页面与 JSON 接口。

安全边界：服务只监听 127.0.0.1，但浏览器中的任何网页都能向它发请求，因此
- 不返回 CORS 头，其他网站读不到响应；
- 校验 Host 头，阻止 DNS 重绑定；
- 有副作用的接口只接受带 X-NetMonitor 头的同源 POST（跨站请求无法携带自定义头）。
"""
from __future__ import annotations

import http.server
import json
import logging
import os
import socketserver
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
from datetime import datetime, timedelta

import nm_config
import storage

logger = logging.getLogger("netmonitor.web")

CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data:; connect-src 'self'; font-src 'self' data:; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
CSRF_HEADER = "X-NetMonitor"
MAX_BODY = 4096


def _read_static(name: str) -> bytes:
    with open(os.path.join(nm_config.STATIC_DIR, name), "rb") as f:
        return f.read()


class TTLCache:
    def __init__(self):
        self._items = {}
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            item = self._items.get(key)
            if item and item[0] > time.time():
                return item[1]
            self._items.pop(key, None)
            return None

    def set(self, key, value, ttl: float) -> None:
        with self._lock:
            if len(self._items) > 256:
                self._items.clear()
            self._items[key] = (time.time() + ttl, value)


class ThreadedServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    # Windows 上 SO_REUSEADDR 允许多个进程绑定同一端口，单实例依赖端口独占
    allow_reuse_address = not nm_config.IS_WINDOWS

    def __init__(self, address, collector, db_file: str):
        super().__init__(address, WebHandler)
        self.collector = collector
        self.db_file = db_file
        self.cache = TTLCache()
        self.index_html = _read_static("index.html")
        self.favicon = _read_static("favicon.svg")
        port = address[1]
        self.allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}", "127.0.0.1", "localhost"}
        self.allowed_origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}


class WebHandler(http.server.BaseHTTPRequestHandler):
    server: ThreadedServer
    server_version = "NetMonitor"
    sys_version = ""

    # --- 公共 -------------------------------------------------------------------

    def log_message(self, format, *args):  # 访问日志写入 debug，不打扰控制台
        logger.debug("%s - %s", self.address_string(), format % args)

    def _send(self, status: int, body: bytes, content_type: str, cache: str = "no-store") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_json(self, payload, status: int = 200) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _host_allowed(self) -> bool:
        host = self.headers.get("Host")
        return host is None or host.strip().lower() in self.server.allowed_hosts

    def _post_allowed(self) -> bool:
        if self.headers.get(CSRF_HEADER) != "1":
            return False
        origin = self.headers.get("Origin")
        if origin and origin.rstrip("/").lower() not in self.server.allowed_origins:
            return False
        return self.headers.get("Sec-Fetch-Site") not in ("cross-site", "same-site")

    def _json_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            return {}
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _db(self) -> sqlite3.Connection:
        return storage.connect(self.server.db_file, timeout=3)

    # --- 路由 -------------------------------------------------------------------

    def do_GET(self):
        if not self._host_allowed():
            return self.send_json({"error": "forbidden host"}, 403)
        parsed = urllib.parse.urlparse(self.path)
        route = GET_ROUTES.get(parsed.path)
        if route is None:
            if parsed.path in POST_ROUTES:
                return self.send_json({"error": "method not allowed"}, 405)
            return self.send_json({"error": "not found"}, 404)
        query = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        try:
            route(self, query)
        except sqlite3.Error:
            logger.exception("查询数据库失败：%s", parsed.path)
            self.send_json({"error": "database unavailable"}, 503)

    do_HEAD = do_GET

    def do_POST(self):
        if not self._host_allowed():
            return self.send_json({"error": "forbidden host"}, 403)
        route = POST_ROUTES.get(urllib.parse.urlparse(self.path).path)
        if route is None:
            return self.send_json({"error": "not found"}, 404)
        if not self._post_allowed():
            return self.send_json({"error": "cross-site request rejected"}, 403)
        route(self, self._json_body())

    # --- GET --------------------------------------------------------------------

    def get_index(self, query):
        self._send(200, self.server.index_html, "text/html; charset=utf-8")

    def get_favicon(self, query):
        self._send(200, self.server.favicon, "image/svg+xml; charset=utf-8", cache="public, max-age=86400")

    def get_health(self, query):
        self.send_json({"status": "ok", "collector": self.server.collector.status_snapshot()["state"]})

    def get_mini_stats(self, query):
        today = datetime.now().strftime("%Y-%m-%d")
        cache = self.server.cache
        cached = cache.get(("mini", today))
        if cached is None:
            conn = self._db()
            try:
                dl, ul, _days = storage.totals(conn, "WHERE date_str = ?", (today,))
                ranking, _total = storage.ranking(conn, today, limit=50, with_hosts=False)
                cached = {
                    "overview": {
                        "today": {"download": dl, "upload": ul, "total": dl + ul},
                        "route_today": storage.route_split_for_day(conn, today, node_limit=15),
                    },
                    "ranking": ranking,
                }
            finally:
                conn.close()
            cache.set(("mini", today), cached, 2.0)
        collector = self.server.collector
        self.send_json({"realtime": collector.realtime_snapshot(), "collector": collector.status_snapshot(), **cached})

    def _overview(self, conn, now: datetime) -> dict:
        today = now.strftime("%Y-%m-%d")
        yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        last_month_day = now.replace(day=1) - timedelta(days=1)
        last_month = last_month_day.strftime("%Y-%m")
        this_month = now.strftime("%Y-%m")
        t_dl, t_ul, _ = storage.totals(conn, "WHERE date_str = ?", (today,))
        y_dl, y_ul, _ = storage.totals(conn, "WHERE date_str = ?", (yesterday,))
        lm_dl, lm_ul, lm_days = storage.month_totals(conn, last_month)
        a_dl, a_ul, days = storage.totals(conn)
        return {
            "route": {
                "today": storage.route_split_for_day(conn, today, node_limit=10),
                "yesterday": storage.route_split_for_day(conn, yesterday),
                "this_month": storage.route_split_for_month(conn, this_month),
                "month": this_month,
            },
            "today": {"date": today, "download": t_dl, "upload": t_ul, "total": t_dl + t_ul},
            "yesterday": {"date": yesterday, "download": y_dl, "upload": y_ul, "total": y_dl + y_ul},
            # 不用 strftime 拼中文：Windows 上 Python 3.8/3.9 的 strftime 无法编码非 ASCII 格式串
            "last_month": {"month": last_month, "label": f"{last_month_day.year}年{last_month_day.month:02d}月",
                           "download": lm_dl, "upload": lm_ul, "total": lm_dl + lm_ul, "days_count": lm_days},
            "all_time": {"download": a_dl, "upload": a_ul, "total": a_dl + a_ul, "days_count": days},
        }

    def get_stats(self, query):
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        date_filter = query.get("date", "today")
        actual_date = {"today": today, "yesterday": (now - timedelta(days=1)).strftime("%Y-%m-%d")}.get(date_filter, date_filter)
        sort_by = query.get("sort", "total")
        search = query.get("search", "").strip()[:100]
        cache = self.server.cache

        conn = None
        try:
            summary = cache.get(("summary", today))
            if summary is None:
                conn = conn or self._db()
                daily = storage.daily_summary(conn)
                summary = {"overview": self._overview(conn, now), "daily_summary": daily,
                           "available_dates": [d["date"] for d in daily],
                           "available_months": storage.available_months(conn)}
                cache.set(("summary", today), summary, 5.0)

            key = ("ranking", actual_date, sort_by, search)
            ranked = cache.get(key)
            if ranked is None:
                conn = conn or self._db()
                ranked = storage.ranking(conn, None if actual_date == "all" else actual_date, search, sort_by)
                # 历史日期的数据不再变化，可以缓存更久
                cache.set(key, ranked, 5.0 if actual_date in (today, "all") else 60.0)
        finally:
            if conn is not None:
                conn.close()

        ranking, selected_total = ranked
        collector = self.server.collector
        self.send_json({
            "realtime": collector.realtime_snapshot(),
            "collector": collector.status_snapshot(),
            **summary,
            "selected_date": actual_date,
            "selected_date_total": selected_total or 1,
            "ranking": ranking,
        })

    # --- POST -------------------------------------------------------------------

    def post_theme(self, body):
        theme = str(body.get("theme") or "")
        if theme not in nm_config.THEMES:
            return self.send_json({"error": "unknown theme"}, 400)
        try:
            cfg = nm_config.load_json_config()
            cfg["theme"] = theme
            nm_config.save_json_config(cfg)
        except OSError:
            logger.warning("保存主题失败", exc_info=True)
            return self.send_json({"error": "save failed"}, 500)
        self.send_json({"status": "ok"})

    def post_mini_launch(self, body):
        try:
            launch_widget()
        except OSError:
            logger.warning("拉起悬浮窗失败", exc_info=True)
            return self.send_json({"error": "launch failed"}, 500)
        self.send_json({"status": "ok"})

    def post_shutdown(self, body):
        self.send_json({"status": "stopping"})
        logger.info("收到停止请求，正在退出")
        threading.Thread(target=self.server.shutdown, daemon=True).start()


GET_ROUTES = {
    "/": WebHandler.get_index,
    "/index.html": WebHandler.get_index,
    "/favicon.ico": WebHandler.get_favicon,
    "/favicon.svg": WebHandler.get_favicon,
    "/api/health": WebHandler.get_health,
    "/api/stats": WebHandler.get_stats,
    "/api/mini/stats": WebHandler.get_mini_stats,
}
POST_ROUTES = {
    "/api/theme": WebHandler.post_theme,
    "/api/mini/launch": WebHandler.post_mini_launch,
    "/api/shutdown": WebHandler.post_shutdown,
}


def pythonw_executable() -> str:
    """Windows 上优先用 pythonw.exe 启动 GUI/后台进程，避免控制台窗口闪现。"""
    candidate = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return candidate if os.path.exists(candidate) else sys.executable


def launch_widget() -> None:
    if not os.path.exists(nm_config.WIDGET_SCRIPT):
        raise OSError("mini_widget.py not found")
    flags = 0x08000000 if nm_config.IS_WINDOWS else 0  # CREATE_NO_WINDOW
    subprocess.Popen([pythonw_executable(), nm_config.WIDGET_SCRIPT], cwd=nm_config.APP_DIR, creationflags=flags)
