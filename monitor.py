# NetMonitor - Clash Verge TUN 真实进程流量精准监控服务
import http.server
import json
import os
import socketserver
import sqlite3
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

# --- 配置 ---
CLASH_API_URL = os.environ.get("CLASH_API_URL", "http://127.0.0.1:9097")
CLASH_SECRET = os.environ.get("CLASH_SECRET", "123456")
POLL_INTERVAL = float(os.environ.get("POLL_INTERVAL", "1.0"))  # 每秒轮询一次活跃连接
WEB_PORT = int(os.environ.get("WEB_PORT", "8899"))
# 数据文件存放路径（默认 E:\tmp\traffic_history.db，支持环境变量 DB_FILE 自定义）
DEFAULT_DATA_DIR = r"E:\tmp" if (os.path.exists(r"E:\tmp") or os.name == "nt") else os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.environ.get("DB_FILE", os.path.join(DEFAULT_DATA_DIR, "traffic_history.db"))

# --- 数据库初始化 ---
def init_db():
    db_dir = os.path.dirname(os.path.abspath(DB_FILE))
    if db_dir:
        try:
            os.makedirs(db_dir, exist_ok=True)
        except Exception:
            pass
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS process_traffic (
            date_str TEXT,
            hour_str TEXT,
            process_name TEXT,
            process_path TEXT,
            host TEXT,
            upload_bytes INTEGER,
            download_bytes INTEGER,
            PRIMARY KEY (date_str, hour_str, process_name, host)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS completed_connections (
            id TEXT PRIMARY KEY,
            start_time TEXT,
            end_time TEXT,
            process_name TEXT,
            process_path TEXT,
            host TEXT,
            dest_ip TEXT,
            rule TEXT,
            upload_bytes INTEGER,
            download_bytes INTEGER
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_proc_date ON process_traffic(date_str)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_proc_name ON process_traffic(process_name)")
    conn.commit()
    conn.close()

# --- 流量收集器 ---
class TrafficCollector(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.active_conns = {}
        self.realtime_stats = {
            "current_down_speed": 0,
            "current_up_speed": 0,
            "process_speeds": {},
            "last_updated": time.time()
        }
        self.lock = threading.Lock()

    def run(self):
        print(f"[*] 流量采集服务已启动，监听 Clash API: {CLASH_API_URL}")
        print(f"[*] 数据存储文件: {DB_FILE}")
        last_poll_time = time.time()
        
        while True:
            try:
                now = time.time()
                time_delta = max(now - last_poll_time, 0.1)
                last_poll_time = now

                req = urllib.request.Request(
                    f"{CLASH_API_URL}/connections",
                    headers={"Authorization": f"Bearer {CLASH_SECRET}"}
                )
                with urllib.request.urlopen(req, timeout=2.5) as resp:
                    data = json.loads(resp.read().decode("utf-8"))

                current_conns_list = data.get("connections", [])
                seen_ids = set()
                
                proc_deltas = {}
                total_down_delta = 0
                total_up_delta = 0

                db_records_to_update = []
                closed_conns_to_save = []

                date_str = datetime.now().strftime("%Y-%m-%d")
                hour_str = datetime.now().strftime("%H")

                for conn in current_conns_list:
                    cid = conn.get("id")
                    if not cid:
                        continue
                    seen_ids.add(cid)

                    cur_up = conn.get("upload", 0)
                    cur_down = conn.get("download", 0)
                    meta = conn.get("metadata", {})
                    proc = meta.get("process") or os.path.basename(meta.get("processPath") or "") or "系统/未知"
                    path = meta.get("processPath") or ""
                    host = meta.get("host") or meta.get("destinationIP") or "Unknown"

                    if cid in self.active_conns:
                        prev = self.active_conns[cid]
                        delta_up = max(cur_up - prev["upload"], 0)
                        delta_down = max(cur_down - prev["download"], 0)
                    else:
                        delta_up = cur_up
                        delta_down = cur_down

                    self.active_conns[cid] = {
                        "upload": cur_up,
                        "download": cur_down,
                        "meta": meta,
                        "start": conn.get("start", ""),
                        "rule": conn.get("rule", "")
                    }

                    if delta_up > 0 or delta_down > 0:
                        total_down_delta += delta_down
                        total_up_delta += delta_up
                        if proc not in proc_deltas:
                            proc_deltas[proc] = {"up": 0, "down": 0}
                        proc_deltas[proc]["up"] += delta_up
                        proc_deltas[proc]["down"] += delta_down

                        db_records_to_update.append((
                            date_str, hour_str, proc, path, host, delta_up, delta_down
                        ))

                closed_ids = set(self.active_conns.keys()) - seen_ids
                for cid in closed_ids:
                    info = self.active_conns.pop(cid, None)
                    if info:
                        meta = info["meta"]
                        proc = meta.get("process") or os.path.basename(meta.get("processPath") or "") or "系统/未知"
                        path = meta.get("processPath") or ""
                        host = meta.get("host") or meta.get("destinationIP") or "Unknown"
                        dest_ip = meta.get("destinationIP") or ""
                        closed_conns_to_save.append((
                            cid,
                            info.get("start", ""),
                            datetime.now().isoformat(),
                            proc,
                            path,
                            host,
                            dest_ip,
                            info.get("rule", ""),
                            info["upload"],
                            info["download"]
                        ))

                with self.lock:
                    self.realtime_stats["current_down_speed"] = total_down_delta / time_delta
                    self.realtime_stats["current_up_speed"] = total_up_delta / time_delta
                    self.realtime_stats["process_speeds"] = {
                        p: {
                            "up": d["up"] / time_delta,
                            "down": d["down"] / time_delta
                        }
                        for p, d in proc_deltas.items()
                    }
                    self.realtime_stats["last_updated"] = now

                if db_records_to_update or closed_conns_to_save:
                    self.save_to_db(db_records_to_update, closed_conns_to_save)

            except Exception:
                pass

            time.sleep(POLL_INTERVAL)

    def save_to_db(self, hourly_deltas, closed_conns):
        try:
            conn = sqlite3.connect(DB_FILE, timeout=5)
            c = conn.cursor()
            
            for date_str, hour_str, proc, path, host, delta_up, delta_down in hourly_deltas:
                c.execute("""
                    INSERT INTO process_traffic (date_str, hour_str, process_name, process_path, host, upload_bytes, download_bytes)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(date_str, hour_str, process_name, host)
                    DO UPDATE SET
                        upload_bytes = upload_bytes + excluded.upload_bytes,
                        download_bytes = download_bytes + excluded.download_bytes,
                        process_path = COALESCE(excluded.process_path, process_traffic.process_path)
                """, (date_str, hour_str, proc, path, host, delta_up, delta_down))

            for conn_data in closed_conns:
                c.execute("""
                    INSERT OR REPLACE INTO completed_connections (
                        id, start_time, end_time, process_name, process_path, host, dest_ip, rule, upload_bytes, download_bytes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, conn_data)

            conn.commit()
            conn.close()
        except Exception:
            pass

# --- 网站图标与仪表盘 ---
FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
  <defs>
    <linearGradient id="g" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#38bdf8"/>
      <stop offset="100%" stop-color="#6366f1"/>
    </linearGradient>
  </defs>
  <rect width="32" height="32" rx="8" fill="#090d16"/>
  <rect x="1" y="1" width="30" height="30" rx="7" fill="none" stroke="rgba(56, 189, 248, 0.35)" stroke-width="1.2"/>
  <circle cx="16" cy="16" r="3.5" fill="url(#g)"/>
  <circle cx="16" cy="16" r="7.5" fill="none" stroke="#38bdf8" stroke-width="1.6" stroke-dasharray="3 2" opacity="0.9"/>
  <circle cx="16" cy="16" r="11.5" fill="none" stroke="#818cf8" stroke-width="1.3" opacity="0.65"/>
  <circle cx="21.5" cy="10.5" r="1.8" fill="#34d399"/>
</svg>"""

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>NetMonitor · 本机网络流量精准归因中心</title>
    <link rel="icon" type="image/svg+xml" href="/favicon.svg">
    <link rel="alternate icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20viewBox%3D%220%200%2032%2032%22%3E%3Cdefs%3E%3ClinearGradient%20id%3D%22g%22%20x1%3D%220%25%22%20y1%3D%220%25%22%20x2%3D%22100%25%22%20y2%3D%22100%25%22%3E%3Cstop%20offset%3D%220%25%22%20stop-color%3D%22%2338bdf8%22%2F%3E%3Cstop%20offset%3D%22100%25%22%20stop-color%3D%22%236366f1%22%2F%3E%3C%2FlinearGradient%3E%3C%2Fdefs%3E%3Crect%20width%3D%2232%22%20height%3D%2232%22%20rx%3D%228%22%20fill%3D%22%23090d16%22%2F%3E%3Crect%20x%3D%221%22%20y%3D%221%22%20width%3D%2230%22%20height%3D%2230%22%20rx%3D%227%22%20fill%3D%22none%22%20stroke%3D%22rgba(56%2C%20189%2C%20248%2C%200.35)%22%20stroke-width%3D%221.2%22%2F%3E%3Ccircle%20cx%3D%2216%22%20cy%3D%2216%22%20r%3D%223.5%22%20fill%3D%22url(%23g)%22%2F%3E%3Ccircle%20cx%3D%2216%22%20cy%3D%2216%22%20r%3D%227.5%22%20fill%3D%22none%22%20stroke%3D%22%2338bdf8%22%20stroke-width%3D%221.6%22%20stroke-dasharray%3D%223%202%22%20opacity%3D%220.9%22%2F%3E%3Ccircle%20cx%3D%2216%22%20cy%3D%2216%22%20r%3D%2211.5%22%20fill%3D%22none%22%20stroke%3D%22%23818cf8%22%20stroke-width%3D%221.3%22%20opacity%3D%220.65%22%2F%3E%3Ccircle%20cx%3D%2221.5%22%20cy%3D%2210.5%22%20r%3D%221.8%22%20fill%3D%22%2334d399%22%2F%3E%3C%2Fsvg%3E">
    <style>
        :root {
            /* Refactoring UI: Layered surface tokens */
            --bg-base: #090d16;
            --surface-card: #0f172a;
            --surface-card-hover: #131f38;
            --surface-elevated: #1e293b;
            --surface-elevated-hover: #27374f;
            --surface-input: #0c1424;
            --surface-subtle: rgba(255, 255, 255, 0.025);
            
            /* Border hierarchy: de-emphasized, translucent */
            --border-subtle: rgba(255, 255, 255, 0.06);
            --border-card: rgba(255, 255, 255, 0.08);
            --border-hover: rgba(56, 189, 248, 0.4);
            --border-focus: #38bdf8;
            
            /* Typography hierarchy */
            --text-main: #f8fafc;
            --text-sub: #94a3b8;
            --text-muted: #64748b;
            --text-faint: #475569;
            
            /* Semantic color palette (with refined tints & borders) */
            --brand: #38bdf8;
            --brand-tint: rgba(56, 189, 248, 0.12);
            --brand-border: rgba(56, 189, 248, 0.25);
            
            --down: #34d399; /* Emerald 400 */
            --down-tint: rgba(52, 211, 153, 0.12);
            --down-border: rgba(52, 211, 153, 0.25);
            
            --up: #38bdf8; /* Sky 400 */
            --up-tint: rgba(56, 189, 248, 0.12);
            --up-border: rgba(56, 189, 248, 0.25);
            
            --warn: #fbbf24; /* Amber 400 */
            --warn-tint: rgba(251, 191, 36, 0.12);
            --warn-border: rgba(251, 191, 36, 0.25);
            
            --danger: #f87171; /* Rose 400 */
            --danger-tint: rgba(248, 113, 113, 0.12);
            --danger-border: rgba(248, 113, 113, 0.25);
            
            /* Layered elevation shadows */
            --shadow-sm: 0 1px 2px 0 rgba(0, 0, 0, 0.4);
            --shadow-card: 0 4px 20px -2px rgba(0, 0, 0, 0.45), 0 0 0 1px rgba(255, 255, 255, 0.05);
            --shadow-card-hover: 0 8px 30px -4px rgba(0, 0, 0, 0.6), 0 0 0 1px rgba(56, 189, 248, 0.25);
            --shadow-dropdown: 0 10px 25px -5px rgba(0, 0, 0, 0.6), 0 0 0 1px rgba(255, 255, 255, 0.1);
        }

        * { box-sizing: border-box; margin: 0; padding: 0; }

        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Microsoft YaHei", sans-serif;
            background-color: var(--bg-base);
            color: var(--text-main);
            padding: 24px;
            line-height: 1.5;
            -webkit-font-smoothing: antialiased;
            min-height: 100vh;
        }

        /* Enforce tabular numerals across all metrics */
        .tabular, .num, .speed, .stat-value {
            font-variant-numeric: tabular-nums;
        }

        /* Top App Bar */
        .app-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 24px;
            padding-bottom: 18px;
            border-bottom: 1px solid var(--border-subtle);
            flex-wrap: wrap;
            gap: 16px;
        }
        .brand-group {
            display: flex;
            align-items: center;
            gap: 12px;
        }
        .brand-icon {
            width: 40px;
            height: 40px;
            border-radius: 10px;
            background: linear-gradient(135deg, rgba(56, 189, 248, 0.2), rgba(99, 102, 241, 0.2));
            border: 1px solid rgba(56, 189, 248, 0.3);
            display: flex;
            align-items: center;
            justify-content: center;
            color: var(--brand);
            box-shadow: 0 0 15px rgba(56, 189, 248, 0.15);
        }
        .brand-title {
            font-size: 20px;
            font-weight: 700;
            color: var(--text-main);
            letter-spacing: -0.02em;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .brand-title span.badge-pro {
            font-size: 11px;
            font-weight: 600;
            padding: 2px 7px;
            border-radius: 9999px;
            background: var(--brand-tint);
            color: var(--brand);
            border: 1px solid var(--brand-border);
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }
        .brand-subtitle {
            font-size: 12px;
            color: var(--text-sub);
            margin-top: 2px;
        }

        .header-actions {
            display: flex;
            align-items: center;
            gap: 10px;
        }

        /* Status Indicator Pill */
        .status-pill {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 5px 12px;
            border-radius: 9999px;
            font-size: 12px;
            font-weight: 500;
            background: var(--down-tint);
            color: var(--down);
            border: 1px solid var(--down-border);
            transition: all 0.2s;
        }
        .status-pill.paused {
            background: var(--warn-tint);
            color: var(--warn);
            border-color: var(--warn-border);
        }
        .status-pill.error {
            background: var(--danger-tint);
            color: var(--danger);
            border-color: var(--danger-border);
        }
        .status-dot {
            width: 7px;
            height: 7px;
            border-radius: 50%;
            background-color: currentColor;
            box-shadow: 0 0 8px currentColor;
            animation: pulse-dot 2s infinite ease-in-out;
        }
        @keyframes pulse-dot {
            0%, 100% { opacity: 1; transform: scale(1); }
            50% { opacity: 0.4; transform: scale(0.85); }
        }

        /* Action Buttons */
        .btn-ghost {
            background: var(--surface-card);
            border: 1px solid var(--border-card);
            color: var(--text-sub);
            padding: 6px 13px;
            border-radius: 8px;
            font-size: 12px;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.15s ease;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            box-shadow: var(--shadow-sm);
        }
        .btn-ghost:hover {
            background: var(--surface-elevated);
            color: var(--text-main);
            border-color: rgba(255, 255, 255, 0.15);
        }
        .btn-ghost.active {
            background: var(--brand-tint);
            color: var(--brand);
            border-color: var(--brand-border);
        }

        /* 4 KPI Grid Cards */
        .grid-4 {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }
        .kpi-card {
            background: var(--surface-card);
            border-radius: 12px;
            padding: 18px 20px;
            border: 1px solid var(--border-card);
            box-shadow: var(--shadow-card);
            transition: transform 0.15s ease, box-shadow 0.15s ease, border-color 0.15s ease;
            position: relative;
            overflow: hidden;
        }
        .kpi-card:hover {
            border-color: rgba(255, 255, 255, 0.12);
            box-shadow: var(--shadow-card-hover);
        }
        .kpi-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 12px;
        }
        .kpi-title {
            font-size: 12px;
            font-weight: 600;
            color: var(--text-sub);
            text-transform: uppercase;
            letter-spacing: 0.04em;
        }
        .kpi-icon-wrap {
            width: 32px;
            height: 32px;
            border-radius: 8px;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .kpi-icon-blue { background: rgba(56, 189, 248, 0.1); color: var(--brand); }
        .kpi-icon-green { background: rgba(52, 211, 153, 0.1); color: var(--down); }
        .kpi-icon-purple { background: rgba(168, 85, 247, 0.1); color: #c084fc; }
        .kpi-icon-amber { background: rgba(251, 191, 36, 0.1); color: var(--warn); }

        .kpi-value {
            font-size: 26px;
            font-weight: 700;
            letter-spacing: -0.02em;
            color: var(--text-main);
            margin-bottom: 12px;
            font-variant-numeric: tabular-nums;
            display: flex;
            align-items: baseline;
            gap: 4px;
        }
        .kpi-value .unit {
            font-size: 13px;
            font-weight: 500;
            color: var(--text-muted);
            letter-spacing: normal;
        }
        .kpi-sub {
            display: flex;
            align-items: center;
            gap: 8px;
            flex-wrap: wrap;
            font-size: 12px;
        }

        /* Pill tags for Inbound/Outbound */
        .chip {
            display: inline-flex;
            align-items: center;
            gap: 4px;
            padding: 2px 8px;
            border-radius: 6px;
            font-size: 11px;
            font-weight: 500;
            font-variant-numeric: tabular-nums;
        }
        .chip-down {
            background: var(--down-tint);
            color: var(--down);
            border: 1px solid var(--down-border);
        }
        .chip-up {
            background: var(--up-tint);
            color: var(--up);
            border: 1px solid var(--up-border);
        }
        .chip-muted {
            background: rgba(255, 255, 255, 0.04);
            color: var(--text-sub);
            border: 1px solid var(--border-subtle);
        }

        /* Main Section Cards */
        .section-card {
            background: var(--surface-card);
            border-radius: 14px;
            padding: 22px;
            border: 1px solid var(--border-card);
            margin-bottom: 24px;
            box-shadow: var(--shadow-card);
        }
        .section-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 18px;
            flex-wrap: wrap;
            gap: 14px;
        }
        .section-title {
            font-size: 16px;
            font-weight: 700;
            color: var(--text-main);
            display: flex;
            align-items: center;
            gap: 10px;
            letter-spacing: -0.01em;
        }
        .section-subtitle {
            font-size: 12px;
            font-weight: 400;
            color: var(--text-muted);
            margin-left: 4px;
        }

        /* Segmented Controller (iOS/Tailwind UI inspired) */
        .segmented-control {
            display: inline-flex;
            background: var(--surface-input);
            padding: 3px;
            border-radius: 8px;
            border: 1px solid var(--border-subtle);
            gap: 2px;
        }
        .segmented-btn {
            background: transparent;
            border: none;
            color: var(--text-sub);
            padding: 5px 12px;
            border-radius: 6px;
            font-size: 12px;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.15s ease;
        }
        .segmented-btn:hover {
            color: var(--text-main);
        }
        .segmented-btn.active {
            background: var(--surface-elevated);
            color: var(--text-main);
            box-shadow: var(--shadow-sm);
            font-weight: 600;
        }

        /* Form Inputs & Filters */
        .search-wrap {
            position: relative;
            display: inline-flex;
            align-items: center;
        }
        .search-icon {
            position: absolute;
            left: 10px;
            width: 14px;
            height: 14px;
            color: var(--text-muted);
            pointer-events: none;
        }
        .filter-input {
            background: var(--surface-input);
            border: 1px solid var(--border-card);
            color: var(--text-main);
            padding: 6px 12px 6px 32px;
            border-radius: 8px;
            font-size: 12px;
            outline: none;
            transition: all 0.15s ease;
            min-width: 200px;
        }
        .filter-input:focus {
            border-color: var(--border-focus);
            box-shadow: 0 0 0 2px rgba(56, 189, 248, 0.2);
            background: var(--surface-card);
        }
        .filter-select {
            background: var(--surface-input);
            border: 1px solid var(--border-card);
            color: var(--text-sub);
            padding: 6px 12px;
            border-radius: 8px;
            font-size: 12px;
            outline: none;
            cursor: pointer;
            transition: all 0.15s ease;
        }
        .filter-select:focus {
            border-color: var(--border-focus);
            box-shadow: 0 0 0 2px rgba(56, 189, 248, 0.2);
            color: var(--text-main);
        }

        /* Daily Trend Visual Chart Carousel */
        .chart-carousel-wrap {
            position: relative;
            width: 100%;
            margin-bottom: 18px;
        }
        .chart-box {
            display: flex;
            align-items: flex-end;
            gap: 14px;
            height: 130px;
            padding: 16px 20px 6px 20px;
            background: rgba(10, 15, 26, 0.7);
            border-radius: 10px;
            overflow-x: auto;
            scroll-behavior: smooth;
            border: 1px solid var(--border-subtle);
            /* 彻底隐藏原生横向滚动条（横拉框） */
            scrollbar-width: none;
            -ms-overflow-style: none;
        }
        .chart-box::-webkit-scrollbar {
            display: none;
        }
        /* 左右切换箭头按钮 */
        .chart-nav-btn {
            position: absolute;
            top: 50%;
            transform: translateY(-50%);
            width: 32px;
            height: 32px;
            border-radius: 50%;
            background: rgba(15, 23, 42, 0.88);
            backdrop-filter: blur(8px);
            -webkit-backdrop-filter: blur(8px);
            border: 1px solid rgba(255, 255, 255, 0.15);
            color: var(--text-main);
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            z-index: 10;
            box-shadow: 0 4px 14px rgba(0, 0, 0, 0.55);
            transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
            opacity: 0;
            pointer-events: none;
        }
        .chart-nav-btn.visible {
            opacity: 1;
            pointer-events: auto;
        }
        .chart-nav-btn:hover:not(:disabled) {
            background: var(--brand);
            color: #090d16;
            border-color: var(--brand);
            box-shadow: 0 0 16px rgba(56, 189, 248, 0.45);
            transform: translateY(-50%) scale(1.1);
        }
        .chart-nav-btn:active:not(:disabled) {
            transform: translateY(-50%) scale(0.95);
        }
        .chart-nav-btn:disabled {
            opacity: 0.22;
            cursor: not-allowed;
            background: rgba(15, 23, 42, 0.4);
            border-color: rgba(255, 255, 255, 0.05);
        }
        .chart-nav-prev {
            left: 6px;
        }
        .chart-nav-next {
            right: 6px;
        }
        .chart-col {
            flex: 1;
            min-width: 68px;
            max-width: 120px;
            height: 100%;
            display: flex;
            flex-direction: column;
            justify-content: flex-end;
            align-items: center;
            cursor: pointer;
            position: relative;
            padding-bottom: 2px;
            transition: all 0.15s ease;
        }
        .chart-col:hover .chart-bar-wrap {
            transform: scaleY(1.03);
            filter: brightness(1.2);
        }
        .chart-bar-wrap {
            width: 32px;
            background: rgba(255, 255, 255, 0.04);
            border-radius: 6px 6px 0 0;
            overflow: hidden;
            display: flex;
            flex-direction: column;
            justify-content: flex-end;
            transition: transform 0.15s ease, filter 0.15s ease;
            box-shadow: 0 2px 5px rgba(0, 0, 0, 0.3);
        }
        .chart-bar-dl {
            background: linear-gradient(180deg, #34d399, #10b981);
            width: 100%;
            transition: height 0.3s ease;
        }
        .chart-bar-ul {
            background: linear-gradient(180deg, #38bdf8, #0284c7);
            width: 100%;
            transition: height 0.3s ease;
        }
        .chart-label {
            font-size: 11px;
            font-weight: 500;
            color: var(--text-muted);
            margin-top: 8px;
            white-space: nowrap;
        }
        .chart-val-label {
            font-size: 10px;
            font-weight: 600;
            margin-bottom: 5px;
            color: var(--text-sub);
            font-variant-numeric: tabular-nums;
        }
        .chart-col.selected .chart-bar-wrap {
            outline: 2px solid var(--brand);
            outline-offset: 2px;
            box-shadow: 0 0 15px rgba(56, 189, 248, 0.35);
        }
        .chart-col.selected .chart-label {
            color: var(--brand);
            font-weight: 700;
        }

        /* Banner Notification */
        .filter-banner {
            background: linear-gradient(90deg, rgba(56, 189, 248, 0.08), rgba(99, 102, 241, 0.04));
            border-left: 3px solid var(--brand);
            padding: 10px 16px;
            border-radius: 0 8px 8px 0;
            margin-bottom: 16px;
            font-size: 12px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 8px;
        }

        /* Professional Tables */
        .table-wrap {
            overflow-x: auto;
            border-radius: 10px;
            border: 1px solid var(--border-subtle);
            background: rgba(10, 15, 26, 0.4);
        }
        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
            text-align: left;
        }
        th, td {
            padding: 12px 16px;
            border-bottom: 1px solid var(--border-subtle);
            vertical-align: middle;
        }
        th {
            background-color: rgba(255, 255, 255, 0.02);
            color: var(--text-muted);
            font-weight: 600;
            font-size: 11px;
            letter-spacing: 0.05em;
            text-transform: uppercase;
            white-space: nowrap;
        }
        tr:last-child td {
            border-bottom: none;
        }
        tr:hover td {
            background-color: rgba(255, 255, 255, 0.025);
        }
        tr.selected-row td {
            background-color: rgba(56, 189, 248, 0.06);
        }

        /* Right-aligned numeric classes */
        .text-right { text-align: right; }
        .text-center { text-align: center; }
        .text-left { text-align: left; }

        /* Process Identification Monogram */
        .proc-cell {
            display: flex;
            align-items: center;
            gap: 12px;
        }
        .proc-avatar {
            width: 32px;
            height: 32px;
            border-radius: 8px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 700;
            font-size: 11px;
            letter-spacing: 0.02em;
            flex-shrink: 0;
            text-transform: uppercase;
        }
        .proc-info {
            min-width: 0;
            overflow: hidden;
        }
        .proc-name {
            font-weight: 600;
            font-size: 13px;
            color: var(--text-main);
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .proc-path {
            font-size: 11px;
            color: var(--text-muted);
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
            max-width: 280px;
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
        }

        /* Subtle slim progress bar */
        .progress-track {
            background: rgba(255, 255, 255, 0.06);
            border-radius: 9999px;
            height: 5px;
            width: 100%;
            overflow: hidden;
            margin-top: 4px;
        }
        .progress-bar {
            background: linear-gradient(90deg, #38bdf8, #818cf8);
            height: 100%;
            border-radius: 9999px;
            transition: width 0.3s ease;
        }

        /* Rank badges */
        .rank-badge {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 24px;
            height: 24px;
            border-radius: 50%;
            font-weight: 700;
            font-size: 11px;
            color: var(--text-muted);
            background: rgba(255, 255, 255, 0.05);
            font-variant-numeric: tabular-nums;
        }
        .rank-gold {
            background: linear-gradient(135deg, #fbbf24, #d97706);
            color: #1e1b4b;
            box-shadow: 0 0 10px rgba(251, 191, 36, 0.3);
        }
        .rank-silver {
            background: linear-gradient(135deg, #e2e8f0, #94a3b8);
            color: #0f172a;
        }
        .rank-bronze {
            background: linear-gradient(135deg, #f59e0b, #b45309);
            color: #fff;
        }

        /* Host domain tags */
        .domain-tag {
            display: inline-block;
            background: rgba(255, 255, 255, 0.035);
            border: 1px solid var(--border-subtle);
            padding: 2px 6px;
            border-radius: 5px;
            margin-right: 4px;
            margin-bottom: 2px;
            font-size: 11px;
            color: #cbd5e1;
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
            max-width: 220px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
            vertical-align: middle;
        }

        /* Pill Badges */
        .badge {
            display: inline-flex;
            align-items: center;
            gap: 4px;
            padding: 2px 8px;
            border-radius: 9999px;
            font-size: 11px;
            font-weight: 500;
            white-space: nowrap;
        }
        .badge-normal {
            background: rgba(255, 255, 255, 0.04);
            color: var(--text-sub);
            border: 1px solid var(--border-subtle);
        }
        .badge-warn {
            background: var(--warn-tint);
            color: var(--warn);
            border: 1px solid var(--warn-border);
        }
        .badge-danger {
            background: var(--danger-tint);
            color: var(--danger);
            border: 1px solid var(--danger-border);
        }
        .badge-success {
            background: var(--down-tint);
            color: var(--down);
            border: 1px solid var(--down-border);
        }

        /* Action buttons in rows */
        .btn-action-table {
            background: rgba(56, 189, 248, 0.08);
            border: 1px solid rgba(56, 189, 248, 0.2);
            color: var(--brand);
            padding: 4px 10px;
            border-radius: 6px;
            font-size: 11px;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.15s ease;
        }
        .btn-action-table:hover {
            background: var(--brand);
            color: #090d16;
            font-weight: 600;
            box-shadow: 0 0 10px rgba(56, 189, 248, 0.3);
        }
        .btn-action-table.active {
            background: rgba(56, 189, 248, 0.2);
            border-color: var(--brand);
            color: var(--brand);
            font-weight: 600;
        }

        /* Empty state */
        .empty-state {
            padding: 36px 20px;
            text-align: center;
            color: var(--text-muted);
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 10px;
        }
        .empty-icon {
            width: 44px;
            height: 44px;
            border-radius: 12px;
            background: rgba(255, 255, 255, 0.03);
            display: flex;
            align-items: center;
            justify-content: center;
            color: var(--text-faint);
            border: 1px solid var(--border-subtle);
        }
    </style>
</head>
<body>

    <!-- 顶部应用栏 -->
    <header class="app-header">
        <div class="brand-group">
            <div class="brand-icon">
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <circle cx="12" cy="12" r="2"></circle>
                    <path d="M16.24 7.76a6 6 0 0 1 0 8.49m-8.48-.01a6 6 0 0 1 0-8.49m11.31-2.82a10 10 0 0 1 0 14.14m-14.14 0a10 10 0 0 1 0-14.14"></path>
                </svg>
            </div>
            <div>
                <div class="brand-title">
                    NetMonitor
                    <span class="badge-pro">TUN 进程归因</span>
                </div>
                <div class="brand-subtitle">
                    针对 Clash Verge / Mihomo TUN 模式底层套接字精准捕获 · 每日流量汇总与实时下钻
                </div>
            </div>
        </div>

        <div class="header-actions">
            <div class="status-pill" id="api-status">
                <span class="status-dot"></span>
                <span id="status-text">监控运行中</span>
            </div>
            <button class="btn-ghost" id="btn-pause" onclick="toggleAutoRefresh()">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <rect x="6" y="4" width="4" height="16"></rect>
                    <rect x="14" y="4" width="4" height="16"></rect>
                </svg>
                <span>暂停刷新</span>
            </button>
            <button class="btn-ghost" onclick="fetchData(true)">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"></path>
                </svg>
                <span>立即刷新</span>
            </button>
        </div>
    </header>

    <!-- 4 大核心统计 KPI 卡片 -->
    <div class="grid-4">
        <!-- 卡片 1：实时网速 -->
        <div class="kpi-card">
            <div class="kpi-header">
                <span class="kpi-title">实时全机吞吐速率</span>
                <div class="kpi-icon-wrap kpi-icon-blue">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon>
                    </svg>
                </div>
            </div>
            <div class="kpi-value" id="live-total-speed">
                0.00 <span class="unit">KB/s</span>
            </div>
            <div class="kpi-sub">
                <span class="chip chip-down" id="live-down-chip">
                    <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="12" y1="5" x2="12" y2="19"></line><polyline points="19 12 12 19 5 12"></polyline></svg>
                    <span id="live-down">0.00 KB/s</span>
                </span>
                <span class="chip chip-up" id="live-up-chip">
                    <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="12" y1="19" x2="12" y2="5"></line><polyline points="5 12 12 5 19 12"></polyline></svg>
                    <span id="live-up">0.00 KB/s</span>
                </span>
                <span class="chip chip-muted" style="margin-left:auto;">秒级刷新</span>
            </div>
        </div>

        <!-- 卡片 2：今日总流量 -->
        <div class="kpi-card">
            <div class="kpi-header">
                <span class="kpi-title">今日共用总流量 (<span id="today-date-str">今日</span>)</span>
                <div class="kpi-icon-wrap kpi-icon-green">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <rect x="3" y="4" width="18" height="18" rx="2" ry="2"></rect>
                        <line x1="16" y1="2" x2="16" y2="6"></line>
                        <line x1="8" y1="2" x2="8" y2="6"></line>
                        <line x1="3" y1="10" x2="21" y2="10"></line>
                    </svg>
                </div>
            </div>
            <div class="kpi-value" id="today-total-str">
                0.00 <span class="unit">MB</span>
            </div>
            <div class="kpi-sub">
                <span class="chip chip-down">
                    <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="12" y1="5" x2="12" y2="19"></line><polyline points="19 12 12 19 5 12"></polyline></svg>
                    下行 <strong id="today-dl-str" class="tabular">0 MB</strong>
                </span>
                <span class="chip chip-up">
                    <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="12" y1="19" x2="12" y2="5"></line><polyline points="5 12 12 5 19 12"></polyline></svg>
                    上行 <strong id="today-ul-str" class="tabular">0 MB</strong>
                </span>
            </div>
        </div>

        <!-- 卡片 3：昨日总流量 -->
        <div class="kpi-card">
            <div class="kpi-header">
                <span class="kpi-title">昨日共用总流量 (<span id="yesterday-date-str">昨日</span>)</span>
                <div class="kpi-icon-wrap kpi-icon-purple">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <circle cx="12" cy="12" r="10"></circle>
                        <polyline points="12 6 12 12 14 14"></polyline>
                    </svg>
                </div>
            </div>
            <div class="kpi-value" id="yesterday-total-str">
                0.00 <span class="unit">MB</span>
            </div>
            <div class="kpi-sub">
                <span class="chip chip-down">
                    <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="12" y1="5" x2="12" y2="19"></line><polyline points="19 12 12 19 5 12"></polyline></svg>
                    下行 <strong id="yesterday-dl-str" class="tabular">0 MB</strong>
                </span>
                <span class="chip chip-up">
                    <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="12" y1="19" x2="12" y2="5"></line><polyline points="5 12 12 5 19 12"></polyline></svg>
                    上行 <strong id="yesterday-ul-str" class="tabular">0 MB</strong>
                </span>
            </div>
        </div>

        <!-- 卡片 4：历史累计流量 -->
        <div class="kpi-card">
            <div class="kpi-header">
                <span class="kpi-title">历史监控累计总流量</span>
                <div class="kpi-icon-wrap kpi-icon-amber">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <circle cx="12" cy="12" r="10"></circle>
                        <line x1="2" y1="12" x2="22" y2="12"></line>
                        <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"></path>
                    </svg>
                </div>
            </div>
            <div class="kpi-value" id="all-total-str">
                0.00 <span class="unit">GB</span>
            </div>
            <div class="kpi-sub">
                <span class="chip chip-down">
                    下行 <strong id="all-dl-str" class="tabular">0 GB</strong>
                </span>
                <span class="chip chip-up">
                    上行 <strong id="all-ul-str" class="tabular">0 GB</strong>
                </span>
                <span class="chip chip-muted" id="all-days-count" style="margin-left:auto;">0 天</span>
            </div>
        </div>
    </div>

    <!-- 模块一：每日流量汇总与历史趋势 -->
    <section class="section-card">
        <div class="section-header">
            <div class="section-title">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="var(--brand)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <rect x="3" y="4" width="18" height="18" rx="2" ry="2"></rect>
                    <line x1="16" y1="2" x2="16" y2="6"></line>
                    <line x1="8" y1="2" x2="8" y2="6"></line>
                    <line x1="3" y1="10" x2="21" y2="10"></line>
                </svg>
                <span>每日共用流量汇总与趋势</span>
                <span class="section-subtitle">点击任意柱状图或行操作，快速下钻查看该日程序消耗</span>
            </div>
            <div style="display:flex; align-items:center; gap: 14px; font-size:12px;">
                <span class="chip chip-down">
                    <span style="display:inline-block; width:8px; height:8px; background:var(--down); border-radius:2px;"></span>
                    下行下载
                </span>
                <span class="chip chip-up">
                    <span style="display:inline-block; width:8px; height:8px; background:var(--up); border-radius:2px;"></span>
                    上行上传
                </span>
            </div>
        </div>

        <!-- 柱状图可视化与左右箭头导航 -->
        <div class="chart-carousel-wrap">
            <button class="chart-nav-btn chart-nav-prev" id="chart-prev-btn" onclick="scrollChart('left')" title="查看更早历史">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                    <polyline points="15 18 9 12 15 6"></polyline>
                </svg>
            </button>
            <div class="chart-box" id="daily-chart" onscroll="updateChartArrows()">
                <!-- 动态生成 -->
            </div>
            <button class="chart-nav-btn chart-nav-next" id="chart-next-btn" onclick="scrollChart('right')" title="查看更近日期">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                    <polyline points="9 18 15 12 9 6"></polyline>
                </svg>
            </button>
        </div>

        <!-- 每日明细表 -->
        <div class="table-wrap">
            <table>
                <thead>
                    <tr>
                        <th class="text-left" style="width: 140px;">日期</th>
                        <th class="text-right" style="width: 140px;">当天共用总流量</th>
                        <th class="text-right" style="width: 130px;">下行下载</th>
                        <th class="text-right" style="width: 130px;">上行上传</th>
                        <th class="text-center" style="width: 120px;">活跃程序数</th>
                        <th class="text-left">当天消耗最大程序 (Top 1)</th>
                        <th class="text-center" style="width: 110px;">操作</th>
                    </tr>
                </thead>
                <tbody id="daily-table-body">
                    <tr><td colspan="7" class="text-center" style="padding: 24px; color: var(--text-muted);">正在加载每日数据...</td></tr>
                </tbody>
            </table>
        </div>
    </section>

    <!-- 模块二：各程序流量消耗排行下钻 -->
    <section class="section-card">
        <div class="section-header">
            <div class="section-title">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="var(--brand)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <line x1="18" y1="20" x2="18" y2="10"></line>
                    <line x1="12" y1="20" x2="12" y2="4"></line>
                    <line x1="6" y1="20" x2="6" y2="14"></line>
                </svg>
                <span>各程序流量消耗排名</span>
            </div>

            <!-- 控制区：分段日期器 + 搜索 + 排序 -->
            <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">
                <!-- 类似 iOS 的分段控制器 -->
                <div class="segmented-control" id="quick-date-buttons">
                    <button class="segmented-btn" data-date="all" onclick="selectDate('all')">全部历史</button>
                    <button class="segmented-btn active" data-date="today" onclick="selectDate('today')">今天</button>
                    <button class="segmented-btn" data-date="yesterday" onclick="selectDate('yesterday')">昨天</button>
                </div>

                <select class="filter-select" id="date-select" onchange="onDateSelectChanged(this.value)">
                    <option value="today">今天</option>
                    <option value="yesterday">昨天</option>
                    <option value="all">全部历史累计</option>
                </select>

                <div class="search-wrap">
                    <svg class="search-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <circle cx="11" cy="11" r="8"></circle>
                        <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
                    </svg>
                    <input type="text" class="filter-input" id="search-input" placeholder="搜索程序名、路径..." oninput="onSearchInput(this.value)">
                </div>

                <select class="filter-select" id="sort-select" onchange="onSortChanged(this.value)">
                    <option value="total">按总流量降序</option>
                    <option value="download">按下载流量降序</option>
                    <option value="upload">按上传流量降序</option>
                </select>
            </div>
        </div>

        <!-- 选定日期的状态横幅 -->
        <div class="filter-banner" id="ranking-banner">
            <div>
                正在查看：<strong id="banner-date-name" style="color:var(--brand); font-size: 13px;">今天</strong> 
                <span id="banner-desc" style="color:var(--text-sub); margin-left:8px;">共消耗 -，涉及 - 个程序</span>
            </div>
            <div id="banner-stats" style="color:var(--text-muted); font-size: 11px;"></div>
        </div>

        <div class="table-wrap">
            <table>
                <thead>
                    <tr>
                        <th class="text-center" style="width: 56px;">排名</th>
                        <th class="text-left" style="min-width: 260px;">程序名称及完整路径</th>
                        <th class="text-right" style="width: 120px;">下载流量</th>
                        <th class="text-right" style="width: 120px;">上传流量</th>
                        <th class="text-right" style="width: 130px;">总流量</th>
                        <th class="text-right" style="width: 150px;">流量占比</th>
                        <th class="text-left">常访问主要域名与目标</th>
                        <th class="text-center" style="width: 110px;">状态标记</th>
                    </tr>
                </thead>
                <tbody id="ranking-table-body">
                    <tr><td colspan="8" class="text-center" style="padding: 36px; color: var(--text-muted);">正在加载程序排名...</td></tr>
                </tbody>
            </table>
        </div>
    </section>

    <!-- 模块三：实时网络活动程序 -->
    <section class="section-card">
        <div class="section-header">
            <div class="section-title">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="var(--down)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon>
                </svg>
                <span>实时网络传输程序</span>
                <span class="badge badge-normal" id="live-proc-count">0 个活跃</span>
            </div>
        </div>
        <div class="table-wrap">
            <table>
                <thead>
                    <tr>
                        <th class="text-left" style="width: 260px;">程序名称</th>
                        <th class="text-right" style="width: 160px;">当前下行速率</th>
                        <th class="text-right" style="width: 160px;">当前上行速率</th>
                        <th class="text-center" style="width: 140px;">瞬时状态</th>
                    </tr>
                </thead>
                <tbody id="live-table-body">
                    <tr>
                        <td colspan="4">
                            <div class="empty-state">
                                <div class="empty-icon">
                                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                        <path d="M5 12.55a11 11 0 0 1 14.08 0"></path>
                                        <path d="M1.42 9a16 16 0 0 1 21.16 0"></path>
                                        <path d="M8.53 16.11a6 6 0 0 1 6.95 0"></path>
                                        <line x1="12" y1="20" x2="12.01" y2="20"></line>
                                    </svg>
                                </div>
                                <div>当前无高频传输进程，网络处于空闲状态</div>
                            </div>
                        </td>
                    </tr>
                </tbody>
            </table>
        </div>
    </section>

    <script>
        let currentDateFilter = 'today';
        let currentSort = 'total';
        let currentSearch = '';
        let autoRefresh = true;
        let refreshTimer = null;
        let cachedDailySummary = [];

        // 精确截取保留 2 位小数
        function truncate2(val) {
            if (val === null || val === undefined || isNaN(val) || val <= 0) return "0.00";
            return (Math.floor(Number(val) * 100 + 0.000001) / 100).toFixed(2);
        }

        // 格式化字节，分离数值与单位，便于排版降权
        function formatBytes(bytes) {
            if (bytes === null || bytes === undefined || isNaN(bytes) || bytes <= 0) return "0.00 B";
            if (bytes < 1024) return truncate2(bytes) + " B";
            if (bytes < 1024 * 1024) return truncate2(bytes / 1024) + " KB";
            if (bytes < 1024 * 1024 * 1024) return truncate2(bytes / (1024 * 1024)) + " MB";
            return truncate2(bytes / (1024 * 1024 * 1024)) + " GB";
        }

        // 格式化 KPI 大数字与小单位
        function formatKpiHtml(bytes) {
            if (bytes === null || bytes === undefined || isNaN(bytes) || bytes <= 0) {
                return '0.00 <span class="unit">MB</span>';
            }
            if (bytes < 1024) return `${truncate2(bytes)} <span class="unit">B</span>`;
            if (bytes < 1024 * 1024) return `${truncate2(bytes / 1024)} <span class="unit">KB</span>`;
            if (bytes < 1024 * 1024 * 1024) return `${truncate2(bytes / (1024 * 1024))} <span class="unit">MB</span>`;
            return `${truncate2(bytes / (1024 * 1024 * 1024))} <span class="unit">GB</span>`;
        }

        function formatSpeed(bps) {
            if (bps === null || bps === undefined || isNaN(bps) || bps <= 0) return "0.00 KB/s";
            if (bps < 1024 * 1024) return truncate2(bps / 1024) + " KB/s";
            if (bps < 1024 * 1024 * 1024) return truncate2(bps / (1024 * 1024)) + " MB/s";
            return truncate2(bps / (1024 * 1024 * 1024)) + " GB/s";
        }

        function formatSpeedKpi(bps) {
            if (bps === null || bps === undefined || isNaN(bps) || bps <= 0) {
                return '0.00 <span class="unit">KB/s</span>';
            }
            if (bps < 1024 * 1024) return `${truncate2(bps / 1024)} <span class="unit">KB/s</span>`;
            if (bps < 1024 * 1024 * 1024) return `${truncate2(bps / (1024 * 1024))} <span class="unit">MB/s</span>`;
            return `${truncate2(bps / (1024 * 1024 * 1024))} <span class="unit">GB/s</span>`;
        }

        // Refactoring UI: 进程生成 2 字符 Monogram 头像徽标，具备一致性柔和背景色
        function getProcAvatar(name) {
            if (!name) return { text: '??', bg: 'rgba(148, 163, 184, 0.15)', color: '#cbd5e1', border: 'rgba(148, 163, 184, 0.3)' };
            const clean = name.replace(/\.exe$/i, '').trim();
            const text = clean.slice(0, 2).toUpperCase();
            
            // 简单 hash 映射到指定色调
            let hash = 0;
            for (let i = 0; i < clean.length; i++) hash = clean.charCodeAt(i) + ((hash << 5) - hash);
            const palette = [
                { bg: 'rgba(56, 189, 248, 0.15)', color: '#38bdf8', border: 'rgba(56, 189, 248, 0.3)' }, // sky
                { bg: 'rgba(52, 211, 153, 0.15)', color: '#34d399', border: 'rgba(52, 211, 153, 0.3)' }, // emerald
                { bg: 'rgba(168, 85, 247, 0.15)', color: '#c084fc', border: 'rgba(168, 85, 247, 0.3)' }, // purple
                { bg: 'rgba(251, 191, 36, 0.15)', color: '#fbbf24', border: 'rgba(251, 191, 36, 0.3)' }, // amber
                { bg: 'rgba(248, 113, 113, 0.15)', color: '#f87171', border: 'rgba(248, 113, 113, 0.3)' }, // rose
                { bg: 'rgba(99, 102, 241, 0.15)', color: '#818cf8', border: 'rgba(99, 102, 241, 0.3)' }, // indigo
                { bg: 'rgba(45, 212, 191, 0.15)', color: '#2dd4bf', border: 'rgba(45, 212, 191, 0.3)' }  // teal
            ];
            return { text, ...palette[Math.abs(hash) % palette.length] };
        }

        function toggleAutoRefresh() {
            autoRefresh = !autoRefresh;
            const btn = document.getElementById('btn-pause');
            const status = document.getElementById('api-status');
            const statusText = document.getElementById('status-text');
            if (autoRefresh) {
                btn.innerHTML = `
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <rect x="6" y="4" width="4" height="16"></rect>
                        <rect x="14" y="4" width="4" height="16"></rect>
                    </svg>
                    <span>暂停刷新</span>
                `;
                btn.classList.remove('active');
                statusText.innerText = "监控运行中";
                status.className = "status-pill";
            } else {
                btn.innerHTML = `
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <polygon points="5 3 19 12 5 21 5 3"></polygon>
                    </svg>
                    <span>恢复刷新</span>
                `;
                btn.classList.add('active');
                statusText.innerText = "刷新已暂停";
                status.className = "status-pill paused";
            }
        }

        function scrollChart(direction) {
            const box = document.getElementById('daily-chart');
            if (!box) return;
            const scrollStep = Math.max(box.clientWidth * 0.65, 180);
            box.scrollBy({
                left: direction === 'left' ? -scrollStep : scrollStep,
                behavior: 'smooth'
            });
            setTimeout(updateChartArrows, 320);
        }

        function updateChartArrows() {
            const box = document.getElementById('daily-chart');
            const prevBtn = document.getElementById('chart-prev-btn');
            const nextBtn = document.getElementById('chart-next-btn');
            if (!box || !prevBtn || !nextBtn) return;

            const maxScroll = box.scrollWidth - box.clientWidth;
            if (maxScroll <= 4) {
                prevBtn.classList.remove('visible');
                nextBtn.classList.remove('visible');
                return;
            }

            prevBtn.classList.add('visible');
            nextBtn.classList.add('visible');

            prevBtn.disabled = (box.scrollLeft <= 4);
            nextBtn.disabled = (box.scrollLeft >= maxScroll - 4);
        }

        function selectDate(dateVal) {
            currentDateFilter = dateVal;
            document.getElementById('date-select').value = dateVal;
            updateQuickButtons();
            fetchData(false);

            // 平滑滚动所选日期柱体至视口中心
            setTimeout(() => {
                const targetCol = document.querySelector(`.chart-col[data-date="${dateVal}"]`);
                if (targetCol) {
                    targetCol.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' });
                    setTimeout(updateChartArrows, 320);
                }
            }, 80);
        }

        function onDateSelectChanged(val) {
            currentDateFilter = val;
            updateQuickButtons();
            fetchData(false);
        }

        function onSortChanged(val) {
            currentSort = val;
            fetchData(false);
        }

        let searchDebounce = null;
        function onSearchInput(val) {
            clearTimeout(searchDebounce);
            searchDebounce = setTimeout(() => {
                currentSearch = val.trim();
                fetchData(false);
            }, 250);
        }

        function updateQuickButtons() {
            document.querySelectorAll('#quick-date-buttons .segmented-btn').forEach(btn => {
                if (btn.getAttribute('data-date') === currentDateFilter) {
                    btn.classList.add('active');
                } else {
                    btn.classList.remove('active');
                }
            });
        }

        async function fetchData(force = false) {
            if (!autoRefresh && !force) return;

            try {
                const params = new URLSearchParams({
                    date: currentDateFilter,
                    sort: currentSort,
                    search: currentSearch
                });
                const res = await fetch('/api/stats?' + params.toString());
                const data = await res.json();

                renderOverview(data);
                renderDailySection(data);
                renderRankingSection(data);
                renderLiveSection(data);

                // 恢复正常运行提示
                if (autoRefresh) {
                    document.getElementById('api-status').className = "status-pill";
                    document.getElementById('status-text').innerText = "监控运行中";
                }
            } catch (err) {
                console.error("Fetch stats error:", err);
                const status = document.getElementById('api-status');
                status.className = "status-pill error";
                document.getElementById('status-text').innerText = "连接中断";
            }
        }

        // 渲染顶部 KPI
        function renderOverview(data) {
            const rt = data.realtime || {};
            const ov = data.overview || {};

            // 实时吞吐
            const downSpd = rt.current_down_speed || 0;
            const upSpd = rt.current_up_speed || 0;
            const totalSpd = downSpd + upSpd;

            const elTotalSpeed = document.getElementById('live-total-speed');
            if (elTotalSpeed) elTotalSpeed.innerHTML = formatSpeedKpi(totalSpd);

            const elLiveDown = document.getElementById('live-down');
            if (elLiveDown) elLiveDown.innerText = formatSpeed(downSpd);

            const elLiveUp = document.getElementById('live-up');
            if (elLiveUp) elLiveUp.innerText = formatSpeed(upSpd);

            // 今日总计
            const td = ov.today || {};
            document.getElementById('today-date-str').innerText = td.date || '今日';
            document.getElementById('today-total-str').innerHTML = formatKpiHtml(td.total || 0);
            document.getElementById('today-dl-str').innerText = formatBytes(td.download || 0);
            document.getElementById('today-ul-str').innerText = formatBytes(td.upload || 0);

            // 昨日总计
            const yd = ov.yesterday || {};
            document.getElementById('yesterday-date-str').innerText = yd.date || '昨日';
            document.getElementById('yesterday-total-str').innerHTML = formatKpiHtml(yd.total || 0);
            document.getElementById('yesterday-dl-str').innerText = formatBytes(yd.download || 0);
            document.getElementById('yesterday-ul-str').innerText = formatBytes(yd.upload || 0);

            // 历史累计
            const at = ov.all_time || {};
            document.getElementById('all-total-str').innerHTML = formatKpiHtml(at.total || 0);
            document.getElementById('all-dl-str').innerText = formatBytes(at.download || 0);
            document.getElementById('all-ul-str').innerText = formatBytes(at.upload || 0);
            document.getElementById('all-days-count').innerText = `${at.days_count || 0} 天记录`;
        }

        // 渲染每日汇总与柱状图
        function renderDailySection(data) {
            const list = data.daily_summary || [];
            cachedDailySummary = list;

            // 下拉选择项同步
            const dateSelect = document.getElementById('date-select');
            const datesInDb = data.available_dates || [];
            
            let optionsHtml = `
                <option value="today">今天 (${data.overview?.today?.date || ''})</option>
                <option value="yesterday">昨天 (${data.overview?.yesterday?.date || ''})</option>
                <option value="all">全部历史累计</option>
            `;
            datesInDb.forEach(d => {
                optionsHtml += `<option value="${d}">具体日期: ${d}</option>`;
            });
            dateSelect.innerHTML = optionsHtml;
            dateSelect.value = currentDateFilter;

            updateQuickButtons();

            // 柱状图
            const chartBox = document.getElementById('daily-chart');
            if (list.length === 0) {
                chartBox.innerHTML = '<div style="margin:auto; color:var(--text-muted); font-size:12px;">暂无历史日度记录</div>';
                updateChartArrows();
            } else {
                const maxDayTotal = Math.max(...list.map(d => d.total), 1);
                const sortedForChart = [...list].reverse();
                chartBox.innerHTML = sortedForChart.map(item => {
                    const isSelected = (item.date === data.selected_date);
                    const dlHeight = Math.max(Math.round((item.download / maxDayTotal) * 78), 3);
                    const ulHeight = Math.max(Math.round((item.upload / maxDayTotal) * 78), 3);
                    return `
                        <div class="chart-col ${isSelected ? 'selected' : ''}" data-date="${item.date}" onclick="selectDate('${item.date}')" title="${item.date} 共用: ${formatBytes(item.total)} (下行:${formatBytes(item.download)}, 上行:${formatBytes(item.upload)})">
                            <span class="chart-val-label">${formatBytes(item.total)}</span>
                            <div class="chart-bar-wrap">
                                <div class="chart-bar-ul" style="height: ${ulHeight}px;"></div>
                                <div class="chart-bar-dl" style="height: ${dlHeight}px;"></div>
                            </div>
                            <span class="chart-label">${item.date.slice(5)}</span>
                        </div>
                    `;
                }).join('');

                // 首次加载自动滚动到最新日期（右侧），后续定时刷新保持当前位置并刷新箭头
                if (!window._chartInitialized) {
                    setTimeout(() => {
                        chartBox.scrollLeft = chartBox.scrollWidth;
                        updateChartArrows();
                        window._chartInitialized = true;
                    }, 60);
                } else {
                    updateChartArrows();
                }
            }

            // 每日表格
            const tbody = document.getElementById('daily-table-body');
            if (list.length === 0) {
                tbody.innerHTML = '<tr><td colspan="7" class="text-center" style="padding: 24px; color: var(--text-muted);">暂无每日汇总数据</td></tr>';
                return;
            }

            tbody.innerHTML = list.map(item => {
                const isSelected = (item.date === data.selected_date);
                return `
                    <tr class="${isSelected ? 'selected-row' : ''}">
                        <td class="text-left">
                            <strong style="color:var(--text-main); font-weight:600;">${item.date}</strong>
                            ${item.date === data.overview?.today?.date ? '<span class="badge badge-success" style="margin-left:6px;">今天</span>' : ''}
                            ${item.date === data.overview?.yesterday?.date ? '<span class="badge badge-normal" style="margin-left:6px;">昨天</span>' : ''}
                        </td>
                        <td class="text-right"><strong class="tabular" style="color:var(--brand); font-size:13px; font-weight:700;">${formatBytes(item.total)}</strong></td>
                        <td class="text-right tabular" style="color:var(--down); font-weight:500;">${formatBytes(item.download)}</td>
                        <td class="text-right tabular" style="color:var(--up); font-weight:500;">${formatBytes(item.upload)}</td>
                        <td class="text-center"><span class="badge badge-normal tabular">${item.proc_count} 个</span></td>
                        <td class="text-left">
                            <strong style="color:var(--text-main); font-weight:600;">${item.top_process}</strong>
                            <span class="tabular" style="font-size: 11px; color: var(--text-muted); margin-left: 6px;">(${formatBytes(item.top_process_bytes)})</span>
                        </td>
                        <td class="text-center">
                            <button class="btn-action-table ${isSelected ? 'active' : ''}" onclick="selectDate('${item.date}')">
                                ${isSelected ? '✓ 正在查看' : '下钻排行'}
                            </button>
                        </td>
                    </tr>
                `;
            }).join('');
        }

        // 渲染排行榜表格
        function renderRankingSection(data) {
            const ranking = data.ranking || [];
            const bannerName = document.getElementById('banner-date-name');
            const bannerDesc = document.getElementById('banner-desc');
            const bannerStats = document.getElementById('banner-stats');

            let dateDisplay = data.selected_date;
            if (data.selected_date === 'all') dateDisplay = '全部历史累计';
            else if (data.selected_date === data.overview?.today?.date) dateDisplay = `今天 (${data.selected_date})`;
            else if (data.selected_date === data.overview?.yesterday?.date) dateDisplay = `昨天 (${data.selected_date})`;

            bannerName.innerText = dateDisplay;
            bannerDesc.innerText = `当天共用总流量：${formatBytes(data.selected_date_total || 0)}，累计上榜程序：${ranking.length} 个`;
            bannerStats.innerText = currentSearch ? `搜索关键词: "${currentSearch}"` : `排序规则: ${currentSort === 'total' ? '总流量' : currentSort === 'download' ? '下行' : '上行'}`;

            const tbody = document.getElementById('ranking-table-body');
            if (ranking.length === 0) {
                tbody.innerHTML = `
                    <tr>
                        <td colspan="8">
                            <div class="empty-state">
                                <div class="empty-icon">
                                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                        <circle cx="11" cy="11" r="8"></circle>
                                        <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
                                    </svg>
                                </div>
                                <div>未找到符合 "${currentSearch || currentDateFilter}" 的程序流量记录</div>
                            </div>
                        </td>
                    </tr>
                `;
                return;
            }

            tbody.innerHTML = ranking.map(item => {
                let rankClass = "rank-badge";
                if (item.rank === 1) rankClass += " rank-gold";
                else if (item.rank === 2) rankClass += " rank-silver";
                else if (item.rank === 3) rankClass += " rank-bronze";

                const isHuge = item.total > 1024 * 1024 * 1024; // > 1GB
                const isBig = item.total > 300 * 1024 * 1024;  // > 300MB

                let tagHtml = '<span class="badge badge-normal">正常</span>';
                if (isHuge) tagHtml = '<span class="badge badge-danger">⚡ 巨量 (&gt;1GB)</span>';
                else if (isBig) tagHtml = '<span class="badge badge-warn">⚠️ 大流量</span>';

                const hostsHtml = (item.top_hosts || []).map(h => `<span class="domain-tag" title="${h}">${h}</span>`).join('') || '<span style="color:var(--text-faint); font-size:12px;">-</span>';

                const avatar = getProcAvatar(item.process_name);

                return `
                    <tr>
                        <td class="text-center"><span class="${rankClass}">${item.rank}</span></td>
                        <td class="text-left">
                            <div class="proc-cell">
                                <div class="proc-avatar" style="background:${avatar.bg}; color:${avatar.color}; border:1px solid ${avatar.border};">
                                    ${avatar.text}
                                </div>
                                <div class="proc-info">
                                    <div class="proc-name">${item.process_name}</div>
                                    <div class="proc-path" title="${item.process_path || '-'}">${item.process_path || '-'}</div>
                                </div>
                            </div>
                        </td>
                        <td class="text-right tabular" style="color: var(--down); font-weight: 500;">${formatBytes(item.download)}</td>
                        <td class="text-right tabular" style="color: var(--up); font-weight: 500;">${formatBytes(item.upload)}</td>
                        <td class="text-right tabular"><strong style="color: var(--text-main); font-size: 13px; font-weight: 700;">${formatBytes(item.total)}</strong></td>
                        <td class="text-right">
                            <div class="tabular" style="font-size:12px; font-weight:600; color:var(--text-main);">${item.pct}%</div>
                            <div class="progress-track">
                                <div class="progress-bar" style="width: ${Math.min(item.pct, 100)}%;"></div>
                            </div>
                        </td>
                        <td class="text-left">${hostsHtml}</td>
                        <td class="text-center">${tagHtml}</td>
                    </tr>
                `;
            }).join('');
        }

        // 渲染实时活动
        function renderLiveSection(data) {
            const rt = data.realtime || {};
            const speeds = rt.process_speeds || {};
            const liveEntries = Object.entries(speeds)
                .filter(([p, s]) => (s.up > 20 || s.down > 20))
                .sort((a, b) => (b[1].down + b[1].up) - (a[1].down + a[1].up));

            document.getElementById('live-proc-count').innerText = `${liveEntries.length} 个活跃`;

            const tbody = document.getElementById('live-table-body');
            if (liveEntries.length === 0) {
                tbody.innerHTML = `
                    <tr>
                        <td colspan="4">
                            <div class="empty-state">
                                <div class="empty-icon">
                                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                        <path d="M5 12.55a11 11 0 0 1 14.08 0"></path>
                                        <path d="M1.42 9a16 16 0 0 1 21.16 0"></path>
                                        <path d="M8.53 16.11a6 6 0 0 1 6.95 0"></path>
                                        <line x1="12" y1="20" x2="12.01" y2="20"></line>
                                    </svg>
                                </div>
                                <div>当前无高频传输进程，网络处于空闲状态</div>
                            </div>
                        </td>
                    </tr>
                `;
                return;
            }

            tbody.innerHTML = liveEntries.map(([proc, s]) => {
                const isHigh = (s.down > 1024 * 1024 || s.up > 1024 * 1024);
                const avatar = getProcAvatar(proc);
                return `
                    <tr>
                        <td class="text-left">
                            <div class="proc-cell">
                                <div class="proc-avatar" style="background:${avatar.bg}; color:${avatar.color}; border:1px solid ${avatar.border};">
                                    ${avatar.text}
                                </div>
                                <div class="proc-name">${proc}</div>
                            </div>
                        </td>
                        <td class="text-right tabular" style="color: var(--down); font-weight: 600;">${formatSpeed(s.down)}</td>
                        <td class="text-right tabular" style="color: var(--up); font-weight: 600;">${formatSpeed(s.up)}</td>
                        <td class="text-center">
                            ${isHigh ? '<span class="badge badge-danger">⚡ 高速传输中</span>' : '<span class="badge badge-success">传输中</span>'}
                        </td>
                    </tr>
                `;
            }).join('');
        }

        // 首次加载与定时器
        fetchData(true);
        setInterval(() => fetchData(false), 2000);
        window.addEventListener('resize', updateChartArrows);
    </script>
</body>
</html>"""

collector = None

class WebHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/" or parsed.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_TEMPLATE.encode("utf-8"))
            return

        if parsed.path in ("/favicon.ico", "/favicon.svg"):
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml; charset=utf-8")
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(FAVICON_SVG.encode("utf-8"))
            return

        if parsed.path == "/api/stats":
            qs = urllib.parse.parse_qs(parsed.query)
            date_filter = qs.get("date", ["today"])[0]
            sort_by = qs.get("sort", ["total"])[0]
            search_kw = qs.get("search", [""])[0].strip()

            # 实时速度快照
            with collector.lock:
                rt = {
                    "current_down_speed": collector.realtime_stats["current_down_speed"],
                    "current_up_speed": collector.realtime_stats["current_up_speed"],
                    "process_speeds": collector.realtime_stats["process_speeds"].copy()
                }

            today_str = datetime.now().strftime("%Y-%m-%d")
            yesterday_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

            actual_date = date_filter
            if date_filter == "today":
                actual_date = today_str
            elif date_filter == "yesterday":
                actual_date = yesterday_str

            conn = sqlite3.connect(DB_FILE, timeout=5)
            c = conn.cursor()

            # 1. 查询今日数据
            c.execute("""
                SELECT COALESCE(SUM(download_bytes), 0), COALESCE(SUM(upload_bytes), 0)
                FROM process_traffic WHERE date_str = ?
            """, (today_str,))
            t_dl, t_ul = c.fetchone()

            # 2. 查询昨日数据
            c.execute("""
                SELECT COALESCE(SUM(download_bytes), 0), COALESCE(SUM(upload_bytes), 0)
                FROM process_traffic WHERE date_str = ?
            """, (yesterday_str,))
            y_dl, y_ul = c.fetchone()

            # 3. 查询历史总计
            c.execute("""
                SELECT COALESCE(SUM(download_bytes), 0), COALESCE(SUM(upload_bytes), 0), COUNT(DISTINCT date_str)
                FROM process_traffic
            """)
            a_dl, a_ul, days_cnt = c.fetchone()

            # 4. 每日汇总列表
            c.execute("""
                SELECT date_str,
                       COALESCE(SUM(download_bytes), 0) as dl,
                       COALESCE(SUM(upload_bytes), 0) as ul,
                       COALESCE(SUM(download_bytes + upload_bytes), 0) as tot,
                       COUNT(DISTINCT process_name) as p_count
                FROM process_traffic
                GROUP BY date_str
                ORDER BY date_str DESC
            """)
            daily_rows = c.fetchall()
            
            daily_summary = []
            available_dates = []
            for d, dl, ul, tot, cnt in daily_rows:
                available_dates.append(d)
                c.execute("""
                    SELECT process_name, SUM(download_bytes + upload_bytes) as p_tot
                    FROM process_traffic
                    WHERE date_str = ?
                    GROUP BY process_name
                    ORDER BY p_tot DESC
                    LIMIT 1
                """, (d,))
                top_p = c.fetchone()
                daily_summary.append({
                    "date": d,
                    "download": dl,
                    "upload": ul,
                    "total": tot,
                    "proc_count": cnt,
                    "top_process": top_p[0] if top_p else "-",
                    "top_process_bytes": top_p[1] if top_p else 0
                })

            # 5. 目标选定日期的各程序消耗排行
            where_clauses = []
            params = []
            if actual_date != "all":
                where_clauses.append("date_str = ?")
                params.append(actual_date)
            if search_kw:
                where_clauses.append("(process_name LIKE ? OR process_path LIKE ?)")
                params.extend([f"%{search_kw}%", f"%{search_kw}%"])

            where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

            # 当日总流量（计算占比）
            c.execute(f"SELECT COALESCE(SUM(download_bytes + upload_bytes), 0) FROM process_traffic {where_sql}", params)
            selected_date_total = c.fetchone()[0] or 1

            order_col = "total"
            if sort_by == "download":
                order_col = "dl"
            elif sort_by == "upload":
                order_col = "ul"

            query = f"""
                SELECT process_name, MAX(process_path),
                       SUM(download_bytes) as dl,
                       SUM(upload_bytes) as ul,
                       SUM(download_bytes + upload_bytes) as total
                FROM process_traffic
                {where_sql}
                GROUP BY process_name
                ORDER BY {order_col} DESC
                LIMIT 100
            """
            c.execute(query, params)
            ranking_rows = c.fetchall()

            ranking = []
            for rank_num, (proc, path, dl, ul, tot) in enumerate(ranking_rows, 1):
                # 提取常访问目标
                host_where = "WHERE date_str = ? AND process_name = ?" if actual_date != "all" else "WHERE process_name = ?"
                host_params = (actual_date, proc) if actual_date != "all" else (proc,)
                c.execute(f"""
                    SELECT host, SUM(download_bytes + upload_bytes) as h_tot
                    FROM process_traffic
                    {host_where}
                    GROUP BY host
                    ORDER BY h_tot DESC
                    LIMIT 3
                """, host_params)
                top_hosts = [h[0] for h in c.fetchall()]

                ranking.append({
                    "rank": rank_num,
                    "process_name": proc,
                    "process_path": path,
                    "download": dl,
                    "upload": ul,
                    "total": tot,
                    "pct": round((tot / selected_date_total) * 100, 2),
                    "top_hosts": top_hosts
                })

            conn.close()

            payload = {
                "realtime": rt,
                "overview": {
                    "today": {
                        "date": today_str,
                        "download": t_dl,
                        "upload": t_ul,
                        "total": t_dl + t_ul
                    },
                    "yesterday": {
                        "date": yesterday_str,
                        "download": y_dl,
                        "upload": y_ul,
                        "total": y_dl + y_ul
                    },
                    "all_time": {
                        "download": a_dl,
                        "upload": a_ul,
                        "total": a_dl + a_ul,
                        "days_count": days_cnt
                    }
                },
                "daily_summary": daily_summary,
                "available_dates": available_dates,
                "selected_date": actual_date,
                "selected_date_total": selected_date_total,
                "ranking": ranking
            }

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode("utf-8"))
            return

        self.send_error(404, "Not Found")

    def log_message(self, format, *args):
        pass

# --- 控制台窗口与后台进程管理 ---
def hide_console_window():
    """在 Windows 下自动隐藏控制台命令行黑框窗口"""
    if os.name == 'nt' and '--show' not in sys.argv and os.environ.get("HIDE_CONSOLE", "1") == "1":
        try:
            import ctypes
            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 0)  # 0 = SW_HIDE
        except Exception:
            pass

def stop_existing_monitor():
    """停止后台正在运行的 NetMonitor 进程"""
    if os.name == 'nt':
        try:
            import subprocess
            my_pid = os.getpid()
            ps_cmd = (
                f"Get-CimInstance Win32_Process | "
                f"Where-Object {{ $_.Name -match '^python' -and $_.CommandLine -match 'monitor\\.py' -and $_.CommandLine -notmatch '--stop' -and $_.ProcessId -ne {my_pid} }} | "
                f"ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force; Write-Host ('已成功停止 NetMonitor 后台进程 (PID: ' + $_.ProcessId + ')') }}"
            )
            res = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                capture_output=True, text=True
            )
            out = res.stdout.strip()
            if out:
                print(out)
            else:
                print("未发现正在运行的 NetMonitor 后台进程。")
        except Exception as e:
            print(f"停止进程失败: {e}")

def get_startup_shortcut_path():
    appdata = os.environ.get("APPDATA", "")
    if not appdata:
        return ""
    return os.path.join(appdata, r"Microsoft\Windows\Start Menu\Programs\Startup\NetMonitor.lnk")

def enable_autostart():
    """将 NetMonitor 注册到 Windows 开机自启目录"""
    if os.name != 'nt':
        print("开机自启功能仅支持 Windows 系统。")
        return
    lnk_path = get_startup_shortcut_path()
    if not lnk_path:
        print("无法获取 Windows 启动文件夹路径。")
        return
    vbs_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "autostart_silent.vbs")
    work_dir = os.path.dirname(os.path.abspath(__file__))
    ps_cmd = (
        f"$ws = New-Object -ComObject WScript.Shell; "
        f"$s = $ws.CreateShortcut('{lnk_path}'); "
        f"$s.TargetPath = 'wscript.exe'; "
        f"$s.Arguments = '\"{vbs_path}\"'; "
        f"$s.WorkingDirectory = '{work_dir}'; "
        f"$s.Description = 'NetMonitor 流量监控服务（开机静默启动）'; "
        f"$s.Save()"
    )
    import subprocess
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], check=True, capture_output=True)
        if os.path.exists(lnk_path):
            print(f"[OK] 已成功开启 NetMonitor 开机自动启动！")
            print(f"快捷方式位置: {lnk_path}")
        else:
            print("[ERROR] 创建启动项快捷方式失败。")
    except Exception as e:
        print(f"设置开机自启失败: {e}")

def disable_autostart():
    """取消 NetMonitor 开机自启"""
    lnk_path = get_startup_shortcut_path()
    if lnk_path and os.path.exists(lnk_path):
        try:
            os.remove(lnk_path)
            print("[OK] 已成功取消 NetMonitor 开机自动启动。")
        except Exception as e:
            print(f"取消开机自启失败: {e}")
    else:
        print("[INFO] 当前未开启 NetMonitor 开机自启。")

def main():
    global collector
    # Windows 下自动隐藏控制台命令行窗口（如需显示可传入 --show 参数）
    hide_console_window()

    init_db()
    collector = TrafficCollector()
    collector.start()

    print(f"=====================================================")
    print(f"  NetMonitor 流量监控服务已运行!")
    print(f"  Web 仪表盘访问地址: http://127.0.0.1:{WEB_PORT}")
    print(f"  已开启每日流量汇总、历史排行及共用总流量统计功能")
    print(f"=====================================================")

    socketserver.TCPServer.allow_reuse_address = True
    try:
        server = socketserver.TCPServer(("127.0.0.1", WEB_PORT), WebHandler)
    except OSError as e:
        if getattr(e, 'winerror', None) == 10048 or "10048" in str(e):
            print(f"\n[提示] 端口 {WEB_PORT} 已被占用，NetMonitor 可能已经在后台运行中。")
            print(f"可以直接在浏览器访问：http://127.0.0.1:{WEB_PORT}")
            return
        raise e

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止服务...")
        server.server_close()

if __name__ == "__main__":
    if "--stop" in sys.argv:
        stop_existing_monitor()
        sys.exit(0)
    if "--autostart" in sys.argv:
        enable_autostart()
        sys.exit(0)
    if "--no-autostart" in sys.argv:
        disable_autostart()
        sys.exit(0)
    main()

