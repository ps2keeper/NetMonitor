from tests import support  # noqa: F401

import http.client
import json
import os
import tempfile
import threading
import unittest
from datetime import datetime

import nm_config
import storage
import web


class StubCollector:
    def status_snapshot(self):
        return {"state": "ok", "message": "采集正常", "last_write_error": None, "pending_rows": 0}

    def realtime_snapshot(self):
        return {"current_down_speed": 1, "current_up_speed": 2, "process_speeds": {
            "evil.exe": {"up": 0, "down": 1, "proxy_speed": 1, "direct_speed": 0, "route": "proxy",
                         "node": "<img src=x onerror=alert(1)>"}},
            "proxy_down_speed": 0, "proxy_up_speed": 0, "direct_down_speed": 0, "direct_up_speed": 0}


class WebSecurityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.db = os.path.join(cls.tmp, "w.db")
        storage.init_db(cls.db)
        conn = storage.connect(cls.db)
        today = datetime.now().strftime("%Y-%m-%d")
        storage.write_batch(conn, [(today, "10", "steam.exe", r"C:\Steam\steam.exe", "cdn.test", 1, 100, 101, 0)],
                            [(today, "10", "proxy", "HK", 1, 100)])
        conn.close()
        cls.server = web.ThreadedServer(("127.0.0.1", 0), StubCollector(), cls.db)
        cls.port = cls.server.server_address[1]
        # 与真实端口一致的 Host / Origin 白名单
        cls.server.allowed_hosts = {f"127.0.0.1:{cls.port}", f"localhost:{cls.port}"}
        cls.server.allowed_origins = {f"http://127.0.0.1:{cls.port}", f"http://localhost:{cls.port}"}
        cls.origin = f"http://127.0.0.1:{cls.port}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def request(self, method, path, headers=None, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp, data

    def test_api_has_no_cors_and_sends_security_headers(self):
        resp, data = self.request("GET", "/api/stats?date=all", {"Origin": "https://evil.example"})
        self.assertEqual(resp.status, 200)
        self.assertIsNone(resp.getheader("Access-Control-Allow-Origin"))
        self.assertIn("connect-src 'self'", resp.getheader("Content-Security-Policy"))
        self.assertEqual(resp.getheader("X-Content-Type-Options"), "nosniff")
        payload = json.loads(data)
        self.assertEqual(payload["ranking"][0]["process_name"], "steam.exe")
        self.assertEqual(payload["collector"]["state"], "ok")

    def test_foreign_host_header_is_rejected(self):
        resp, _ = self.request("GET", "/api/stats", {"Host": f"attacker.example:{self.port}"})
        self.assertEqual(resp.status, 403)

    def test_side_effect_endpoints_reject_get_and_cross_site_post(self):
        self.assertEqual(self.request("GET", "/api/theme?set=light")[0].status, 405)
        self.assertEqual(self.request("GET", "/api/mini/launch")[0].status, 405)
        body = json.dumps({"theme": "light"})
        plain = {"Content-Type": "application/json"}
        self.assertEqual(self.request("POST", "/api/theme", plain, body)[0].status, 403)
        cross = dict(plain, **{"X-NetMonitor": "1", "Origin": "https://evil.example"})
        self.assertEqual(self.request("POST", "/api/theme", cross, body)[0].status, 403)
        fetch_meta = dict(plain, **{"X-NetMonitor": "1", "Sec-Fetch-Site": "cross-site"})
        self.assertEqual(self.request("POST", "/api/theme", fetch_meta, body)[0].status, 403)

    def test_same_origin_theme_update_is_saved(self):
        headers = {"Content-Type": "application/json", "X-NetMonitor": "1", "Origin": self.origin}
        resp, _ = self.request("POST", "/api/theme", headers, json.dumps({"theme": "ocean"}))
        self.assertEqual(resp.status, 200)
        self.assertEqual(nm_config.load_json_config()["theme"], "ocean")
        resp, _ = self.request("POST", "/api/theme", headers, json.dumps({"theme": "<script>"}))
        self.assertEqual(resp.status, 400)

    def test_mini_stats_and_dashboard(self):
        resp, data = self.request("GET", "/api/mini/stats")
        payload = json.loads(data)
        self.assertEqual(payload["overview"]["today"]["download"], 100)
        self.assertEqual(payload["ranking"][0]["process_name"], "steam.exe")
        resp, html = self.request("GET", "/")
        self.assertEqual(resp.status, 200)
        self.assertIn(b"<!DOCTYPE html>", html)

    def test_unknown_path_is_404(self):
        self.assertEqual(self.request("GET", "/etc/passwd")[0].status, 404)


class DashboardTemplateTest(unittest.TestCase):
    """看板脚本中来自数据的字段必须经过 esc()。"""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(nm_config.STATIC_DIR, "index.html"), encoding="utf-8") as f:
            cls.html = f.read()

    def test_untrusted_fields_are_escaped(self):
        for raw in ('title="${h}"', '>${item.process_name}<', '>${proc}<', "${s.node ||", "${item.top_process}<",
                    'title="${item.process_path'):
            self.assertNotIn(raw, self.html)
        for escaped in ("esc(h)", "esc(item.process_name)", "esc(proc)", "esc(s.node", "esc(item.top_process)",
                        "esc(item.process_path"):
            self.assertIn(escaped, self.html)

    def test_side_effects_use_post_with_header(self):
        self.assertNotIn("/api/theme?set=", self.html)
        self.assertNotIn("fetch('/api/mini/launch')", self.html)
        self.assertIn("'X-NetMonitor': '1'", self.html)


if __name__ == "__main__":
    unittest.main()
