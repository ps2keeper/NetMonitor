"""测试公共设施：隔离的数据目录、假 Mihomo 外部控制器。

必须在导入 nm_config 之前导入本模块（各测试文件的第一行 import）。
"""
import json
import logging
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

TEST_HOME = tempfile.mkdtemp(prefix="netmonitor-tests-")
os.environ["NETMONITOR_DATA_DIR"] = os.path.join(TEST_HOME, "data")
os.environ["DB_FILE"] = os.path.join(TEST_HOME, "data", "default.db")
# 采集状态变化会写 WARNING 日志，测试中不输出
logging.getLogger("netmonitor").setLevel(logging.CRITICAL)

for name in ("CLASH_API_URL", "CLASH_SECRET", "WEB_PORT", "NETMONITOR_IPC_PORT"):
    os.environ.pop(name, None)


def conn(cid, down, up=0, process="app.exe", path=r"C:\App\app.exe", host="example.test", chains=("DIRECT",)):
    """构造一条 Mihomo /connections 中的连接记录。"""
    return {"id": cid, "upload": up, "download": down, "chains": list(chains), "rule": "Match", "start": "",
            "metadata": {"process": process, "processPath": path, "host": host}}


class FakeMihomo:
    """可编程的假 /connections 接口：设置 connections / totals / status 即可改变响应。"""

    def __init__(self, secret=""):
        self.secret = secret
        self.connections = []
        self.upload_total = 0
        self.download_total = 0
        self.requests = 0
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                fake.requests += 1
                if fake.secret and self.headers.get("Authorization") != f"Bearer {fake.secret}":
                    self.send_response(401)
                    self.end_headers()
                    return
                body = json.dumps({"connections": fake.connections, "uploadTotal": fake.upload_total,
                                   "downloadTotal": fake.download_total}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
