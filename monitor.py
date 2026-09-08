# NetMonitor - Clash Verge TUN 真实进程流量精准监控服务
import http.server
import json
import os
import socketserver
import sqlite3
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

# --- Web 仪表盘 ---
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>NetMonitor - 本机流量每日汇总与程序排行</title>
    <style>
        :root {
            --bg: #0b1120;
            --card-bg: #151f32;
            --card-sub: #1e293b;
            --border: #283548;
            --border-hover: #3b82f6;
            --text-main: #f8fafc;
            --text-sub: #94a3b8;
            --primary: #38bdf8;
            --primary-dark: #0284c7;
            --success: #4ade80;
            --warning: #fbbf24;
            --danger: #f87171;
            --accent: #a855f7;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Microsoft YaHei", sans-serif;
            background-color: var(--bg);
            color: var(--text-main);
            padding: 20px;
            line-height: 1.5;
        }
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
            padding-bottom: 14px;
            border-bottom: 1px solid var(--border);
            flex-wrap: wrap;
            gap: 12px;
        }
        .header h1 { font-size: 22px; font-weight: 700; color: var(--primary); display: flex; align-items: center; gap: 8px; }
        .header-actions { display: flex; align-items: center; gap: 10px; }
        .btn-action {
            background: var(--card-sub);
            border: 1px solid var(--border);
            color: var(--text-main);
            padding: 6px 14px;
            border-radius: 6px;
            font-size: 13px;
            cursor: pointer;
            transition: all 0.2s;
            display: inline-flex;
            align-items: center;
            gap: 6px;
        }
        .btn-action:hover { background: #334155; border-color: var(--primary); }
        .btn-action.active { background: var(--primary); color: #0b1120; font-weight: 600; border-color: var(--primary); }

        .grid-4 {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }
        .card {
            background-color: var(--card-bg);
            border-radius: 10px;
            padding: 16px;
            border: 1px solid var(--border);
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.2);
            transition: transform 0.15s ease, border-color 0.15s ease;
        }
        .card:hover { border-color: #3b82f6; }
        .card .title { font-size: 13px; color: var(--text-sub); margin-bottom: 8px; display: flex; justify-content: space-between; }
        .card .value { font-size: 26px; font-weight: 700; letter-spacing: -0.5px; }
        .card .value.down { color: var(--success); }
        .card .value.up { color: var(--primary); }
        .card .sub { font-size: 12px; color: var(--text-sub); margin-top: 6px; display: flex; gap: 12px; }

        .section {
            background-color: var(--card-bg);
            border-radius: 10px;
            padding: 20px;
            border: 1px solid var(--border);
            margin-bottom: 24px;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.2);
        }
        .section-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 16px;
            flex-wrap: wrap;
            gap: 12px;
        }
        .section-title {
            font-size: 17px;
            font-weight: 600;
            display: flex;
            align-items: center;
            gap: 8px;
        }

        /* 每日趋势柱状图 */
        .chart-container {
            display: flex;
            align-items: flex-end;
            gap: 16px;
            height: 120px;
            padding: 12px 10px 4px 10px;
            background: rgba(15, 23, 42, 0.6);
            border-radius: 8px;
            margin-bottom: 16px;
            overflow-x: auto;
            border: 1px solid rgba(255, 255, 255, 0.05);
        }
        .chart-col {
            flex: 1;
            min-width: 70px;
            max-width: 130px;
            height: 100%;
            display: flex;
            flex-direction: column;
            justify-content: flex-end;
            align-items: center;
            cursor: pointer;
            position: relative;
        }
        .chart-col:hover .chart-bar-wrap { filter: brightness(1.2); }
        .chart-bar-wrap {
            width: 32px;
            background: #1e293b;
            border-radius: 4px 4px 0 0;
            overflow: hidden;
            display: flex;
            flex-direction: column;
            justify-content: flex-end;
            transition: all 0.2s;
        }
        .chart-bar-dl { background: var(--success); width: 100%; }
        .chart-bar-ul { background: var(--primary); width: 100%; }
        .chart-label { font-size: 11px; color: var(--text-sub); margin-top: 6px; white-space: nowrap; }
        .chart-val-label { font-size: 11px; font-weight: 600; margin-bottom: 4px; color: var(--text-main); }
        .chart-col.selected .chart-bar-wrap { outline: 2px solid var(--primary); outline-offset: 2px; }

        /* 表格样式 */
        .table-responsive {
            overflow-x: auto;
            border-radius: 8px;
            border: 1px solid var(--border);
        }
        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
            text-align: left;
        }
        th, td {
            padding: 11px 14px;
            border-bottom: 1px solid var(--border);
        }
        th {
            background-color: var(--card-sub);
            color: var(--text-sub);
            font-weight: 600;
            font-size: 12px;
            letter-spacing: 0.3px;
        }
        tr:last-child td { border-bottom: none; }
        tr:hover td { background-color: rgba(255, 255, 255, 0.02); }
        tr.active-row td { background-color: rgba(56, 189, 248, 0.08); font-weight: 500; }

        .progress-bar-bg {
            background-color: #334155;
            border-radius: 4px;
            height: 6px;
            overflow: hidden;
            width: 100%;
            margin-top: 5px;
        }
        .progress-bar-fill {
            background-color: var(--primary);
            height: 100%;
            border-radius: 4px;
            transition: width 0.3s ease;
        }

        .badge {
            display: inline-block;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 11px;
            background: #334155;
            color: #cbd5e1;
            font-weight: 500;
        }
        .badge-danger { background: rgba(248, 113, 113, 0.2); color: var(--danger); font-weight: 600; border: 1px solid rgba(248, 113, 113, 0.3); }
        .badge-warn { background: rgba(251, 191, 36, 0.2); color: var(--warning); font-weight: 600; border: 1px solid rgba(251, 191, 36, 0.3); }
        .badge-success { background: rgba(74, 222, 128, 0.2); color: var(--success); font-weight: 600; border: 1px solid rgba(74, 222, 128, 0.3); }

        .rank-badge {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 24px;
            height: 24px;
            border-radius: 50%;
            font-weight: 700;
            font-size: 12px;
            background: #334155;
            color: #e2e8f0;
        }
        .rank-1 { background: linear-gradient(135deg, #fbbf24, #d97706); color: #000; }
        .rank-2 { background: linear-gradient(135deg, #94a3b8, #64748b); color: #000; }
        .rank-3 { background: linear-gradient(135deg, #d97706, #92400e); color: #fff; }

        .filter-input {
            background: var(--card-sub);
            border: 1px solid var(--border);
            color: var(--text-main);
            padding: 6px 12px;
            border-radius: 6px;
            font-size: 13px;
            outline: none;
            transition: border-color 0.2s;
        }
        .filter-input:focus { border-color: var(--primary); }
        .filter-select {
            background: var(--card-sub);
            border: 1px solid var(--border);
            color: var(--text-main);
            padding: 6px 12px;
            border-radius: 6px;
            font-size: 13px;
            outline: none;
            cursor: pointer;
        }
        .filter-select:focus { border-color: var(--primary); }

        .filter-tag-btn {
            background: var(--card-sub);
            border: 1px solid var(--border);
            color: var(--text-sub);
            padding: 4px 10px;
            border-radius: 5px;
            font-size: 12px;
            cursor: pointer;
            transition: all 0.2s;
        }
        .filter-tag-btn:hover { color: var(--text-main); border-color: var(--primary); }
        .filter-tag-btn.active { background: var(--primary); color: #0b1120; font-weight: 600; border-color: var(--primary); }

        .btn-view-day {
            background: rgba(56, 189, 248, 0.15);
            border: 1px solid rgba(56, 189, 248, 0.3);
            color: var(--primary);
            padding: 3px 8px;
            border-radius: 4px;
            font-size: 11px;
            cursor: pointer;
            transition: all 0.15s;
        }
        .btn-view-day:hover {
            background: var(--primary);
            color: #0b1120;
            font-weight: 600;
        }

        .highlight-banner {
            background: linear-gradient(90deg, rgba(56, 189, 248, 0.12), rgba(168, 85, 247, 0.08));
            border-left: 4px solid var(--primary);
            padding: 10px 14px;
            border-radius: 0 6px 6px 0;
            margin-bottom: 14px;
            font-size: 13px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 8px;
        }

        .host-tag {
            display: inline-block;
            background: rgba(255, 255, 255, 0.05);
            padding: 2px 6px;
            border-radius: 4px;
            margin-right: 4px;
            margin-bottom: 2px;
            font-size: 11px;
            color: #cbd5e1;
        }
    </style>
</head>
<body>

    <!-- 顶部导航栏 -->
    <div class="header">
        <div>
            <h1>📡 NetMonitor 流量监视中心</h1>
            <div style="font-size: 12px; color: var(--text-sub); margin-top: 4px;">
                针对 Clash Verge TUN 模式 · 真实程序网络流量精准归因 · 每日流量汇总与历史排行
            </div>
        </div>
        <div class="header-actions">
            <span class="badge badge-success" id="api-status">● 监控运行中</span>
            <button class="btn-action" id="btn-pause" onclick="toggleAutoRefresh()">⏸️ 暂停刷新</button>
            <button class="btn-action" onclick="fetchData(true)">🔄 立即刷新</button>
        </div>
    </div>

    <!-- 4大核心统计卡片 -->
    <div class="grid-4">
        <div class="card">
            <div class="title">
                <span>实时网速 (瞬时吞吐)</span>
                <span>⚡ 动态</span>
            </div>
            <div class="value down" id="live-down">0 KB/s</div>
            <div class="sub">
                <span>下行: <strong id="sub-live-down" style="color:var(--success);">0 KB/s</strong></span>
                <span>上行: <strong id="sub-live-up" style="color:var(--primary);">0 KB/s</strong></span>
            </div>
        </div>

        <div class="card">
            <div class="title">
                <span>今日共用总流量 (<span id="today-date-str">今日</span>)</span>
                <span>📅 当天</span>
            </div>
            <div class="value" id="today-total-str">0 MB</div>
            <div class="sub">
                <span>下行: <strong id="today-dl-str" style="color:var(--success);">0 MB</strong></span>
                <span>上行: <strong id="today-ul-str" style="color:var(--primary);">0 MB</strong></span>
            </div>
        </div>

        <div class="card">
            <div class="title">
                <span>昨日共用总流量 (<span id="yesterday-date-str">昨日</span>)</span>
                <span>🗓️ 昨日</span>
            </div>
            <div class="value" id="yesterday-total-str">0 MB</div>
            <div class="sub">
                <span>下行: <strong id="yesterday-dl-str" style="color:var(--success);">0 MB</strong></span>
                <span>上行: <strong id="yesterday-ul-str" style="color:var(--primary);">0 MB</strong></span>
            </div>
        </div>

        <div class="card">
            <div class="title">
                <span>历史监控累计总流量</span>
                <span>🌐 全程</span>
            </div>
            <div class="value" id="all-total-str">0 GB</div>
            <div class="sub">
                <span>下行: <strong id="all-dl-str" style="color:var(--success);">0 GB</strong></span>
                <span>上行: <strong id="all-ul-str" style="color:var(--primary);">0 GB</strong></span>
                <span id="all-days-count" style="margin-left:auto;">2 天</span>
            </div>
        </div>
    </div>

    <!-- 模块一：每日流量汇总与历史趋势 -->
    <div class="section">
        <div class="section-header">
            <div class="section-title">
                <span>📅 每日共用流量汇总与趋势</span>
                <span style="font-size: 12px; color: var(--text-sub); font-weight: normal;">(点击任意日期直接查看该日程序排行)</span>
            </div>
            <div style="font-size: 12px; color: var(--text-sub);">
                <span style="display:inline-block; width:10px; height:10px; background:var(--success); border-radius:2px; margin-right:4px;"></span>下行流量
                <span style="display:inline-block; width:10px; height:10px; background:var(--primary); border-radius:2px; margin-left:10px; margin-right:4px;"></span>上行流量
            </div>
        </div>

        <!-- 柱状图可视化 -->
        <div class="chart-container" id="daily-chart">
            <!-- 动态生成 -->
        </div>

        <!-- 每日汇总明细表 -->
        <div class="table-responsive">
            <table>
                <thead>
                    <tr>
                        <th style="width: 140px;">日期</th>
                        <th style="width: 150px;">当天共用总流量</th>
                        <th style="width: 130px;">下行下载</th>
                        <th style="width: 130px;">上行上传</th>
                        <th style="width: 110px;">活跃程序数</th>
                        <th>当天消耗最大程序 (Top 1)</th>
                        <th style="width: 110px; text-align: center;">操作</th>
                    </tr>
                </thead>
                <tbody id="daily-table-body">
                    <tr><td colspan="7" style="text-align: center; color: var(--text-sub); padding: 20px;">加载每日数据中...</td></tr>
                </tbody>
            </table>
        </div>
    </div>

    <!-- 模块二：历史查看每天各个程序使用流量的排名 -->
    <div class="section">
        <div class="section-header">
            <div class="section-title">
                <span>📊 各程序流量消耗排名</span>
            </div>
            <!-- 筛选与控制区 -->
            <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">
                <div style="display:flex; gap: 4px;" id="quick-date-buttons">
                    <!-- 快捷日期按钮组 -->
                </div>
                <select class="filter-select" id="date-select" onchange="onDateSelectChanged(this.value)">
                    <option value="today">今天</option>
                    <option value="yesterday">昨天</option>
                    <option value="all">全部历史累计</option>
                </select>
                <input type="text" class="filter-input" id="search-input" placeholder="🔍 搜索程序名/路径..." oninput="onSearchInput(this.value)">
                <select class="filter-select" id="sort-select" onchange="onSortChanged(this.value)">
                    <option value="total">按总流量排序</option>
                    <option value="download">按下载流量排序</option>
                    <option value="upload">按上传流量排序</option>
                </select>
            </div>
        </div>

        <!-- 当前查看日期的状态横幅 -->
        <div class="highlight-banner" id="ranking-banner">
            <div>
                正在查看：<strong id="banner-date-name" style="color:var(--primary); font-size: 14px;">今天</strong> 
                <span id="banner-desc" style="color:var(--text-sub); margin-left:8px;">共消耗 -，涉及 - 个程序</span>
            </div>
            <div id="banner-stats" style="color:var(--text-sub); font-size: 12px;"></div>
        </div>

        <div class="table-responsive">
            <table>
                <thead>
                    <tr>
                        <th style="width: 60px; text-align: center;">排名</th>
                        <th style="width: 260px;">程序名称及完整路径</th>
                        <th style="width: 120px;">下载流量</th>
                        <th style="width: 120px;">上传流量</th>
                        <th style="width: 130px;">总流量</th>
                        <th style="width: 160px;">流量占比</th>
                        <th>常访问主要域名与目标</th>
                        <th style="width: 100px;">标记</th>
                    </tr>
                </thead>
                <tbody id="ranking-table-body">
                    <tr><td colspan="8" style="text-align: center; color: var(--text-sub); padding: 30px;">正在加载程序排名...</td></tr>
                </tbody>
            </table>
        </div>
    </div>

    <!-- 模块三：实时网络吞吐活动列表 -->
    <div class="section">
        <div class="section-header">
            <div class="section-title">
                <span>🔥 实时正在产生网络活动的程序</span>
                <span class="badge" id="live-proc-count">0 个活跃</span>
            </div>
        </div>
        <div class="table-responsive">
            <table>
                <thead>
                    <tr>
                        <th style="width: 250px;">程序名称</th>
                        <th style="width: 150px;">当前下行速率</th>
                        <th style="width: 150px;">当前上行速率</th>
                        <th>瞬时状态</th>
                    </tr>
                </thead>
                <tbody id="live-table-body">
                    <tr><td colspan="4" style="text-align: center; color: var(--text-sub); padding: 15px;">当前无明显吞吐传输</td></tr>
                </tbody>
            </table>
        </div>
    </div>

    <script>
        let currentDateFilter = 'today';
        let currentSort = 'total';
        let currentSearch = '';
        let autoRefresh = true;
        let refreshTimer = null;
        let cachedDailySummary = [];

        function formatBytes(bytes) {
            if (bytes === null || bytes === undefined || isNaN(bytes)) return "0 B";
            if (bytes < 1024) return bytes + " B";
            if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
            if (bytes < 1024 * 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(2) + " MB";
            return (bytes / (1024 * 1024 * 1024)).toFixed(2) + " GB";
        }

        function formatSpeed(bps) {
            return formatBytes(bps) + "/s";
        }

        function toggleAutoRefresh() {
            autoRefresh = !autoRefresh;
            const btn = document.getElementById('btn-pause');
            const status = document.getElementById('api-status');
            if (autoRefresh) {
                btn.innerText = "⏸️ 暂停刷新";
                btn.classList.remove('active');
                status.innerText = "● 监控运行中";
                status.className = "badge badge-success";
            } else {
                btn.innerText = "▶️ 恢复刷新";
                btn.classList.add('active');
                status.innerText = "⏸️ 刷新已暂停";
                status.className = "badge badge-warn";
            }
        }

        function selectDate(dateVal) {
            currentDateFilter = dateVal;
            document.getElementById('date-select').value = dateVal;
            updateQuickButtons();
            fetchData(false);
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
            }, 300);
        }

        function updateQuickButtons() {
            document.querySelectorAll('.filter-tag-btn').forEach(btn => {
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

            } catch (err) {
                console.error("Fetch stats error:", err);
                document.getElementById('api-status').innerText = "❌ 连接异常";
                document.getElementById('api-status').className = "badge badge-danger";
            }
        }

        // 渲染顶部总览
        function renderOverview(data) {
            const rt = data.realtime || {};
            const ov = data.overview || {};

            // 实时速率
            document.getElementById('live-down').innerText = formatSpeed(rt.current_down_speed || 0);
            document.getElementById('sub-live-down').innerText = formatSpeed(rt.current_down_speed || 0);
            document.getElementById('sub-live-up').innerText = formatSpeed(rt.current_up_speed || 0);

            // 今日总计
            const td = ov.today || {};
            document.getElementById('today-date-str').innerText = td.date || '今日';
            document.getElementById('today-total-str').innerText = formatBytes(td.total || 0);
            document.getElementById('today-dl-str').innerText = formatBytes(td.download || 0);
            document.getElementById('today-ul-str').innerText = formatBytes(td.upload || 0);

            // 昨日总计
            const yd = ov.yesterday || {};
            document.getElementById('yesterday-date-str').innerText = yd.date || '昨日';
            document.getElementById('yesterday-total-str').innerText = formatBytes(yd.total || 0);
            document.getElementById('yesterday-dl-str').innerText = formatBytes(yd.download || 0);
            document.getElementById('yesterday-ul-str').innerText = formatBytes(yd.upload || 0);

            // 历史全量
            const at = ov.all_time || {};
            document.getElementById('all-total-str').innerText = formatBytes(at.total || 0);
            document.getElementById('all-dl-str').innerText = formatBytes(at.download || 0);
            document.getElementById('all-ul-str').innerText = formatBytes(at.upload || 0);
            document.getElementById('all-days-count').innerText = `共计 ${(at.days_count || 0)} 天记录`;
        }

        // 渲染每日汇总与柱状图
        function renderDailySection(data) {
            const list = data.daily_summary || [];
            cachedDailySummary = list;

            // 更新快捷选择下拉框
            const dateSelect = document.getElementById('date-select');
            const currentVal = dateSelect.value;
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

            // 更新快捷按钮组
            const qBtnContainer = document.getElementById('quick-date-buttons');
            let qBtns = `
                <button class="filter-tag-btn ${currentDateFilter==='all'?'active':''}" data-date="all" onclick="selectDate('all')">全部历史</button>
                <button class="filter-tag-btn ${currentDateFilter==='today'?'active':''}" data-date="today" onclick="selectDate('today')">今天</button>
                <button class="filter-tag-btn ${currentDateFilter==='yesterday'?'active':''}" data-date="yesterday" onclick="selectDate('yesterday')">昨天</button>
            `;
            qBtnContainer.innerHTML = qBtns;

            // 渲染柱状图
            const chartBox = document.getElementById('daily-chart');
            if (list.length === 0) {
                chartBox.innerHTML = '<div style="margin:auto; color:var(--text-sub); font-size:12px;">暂无历史日度记录</div>';
            } else {
                const maxDayTotal = Math.max(...list.map(d => d.total), 1);
                // 柱状图按时间正序展示
                const sortedForChart = [...list].reverse();
                chartBox.innerHTML = sortedForChart.map(item => {
                    const isSelected = (item.date === data.selected_date);
                    const dlHeight = Math.max(Math.round((item.download / maxDayTotal) * 75), 3);
                    const ulHeight = Math.max(Math.round((item.upload / maxDayTotal) * 75), 3);
                    return `
                        <div class="chart-col ${isSelected ? 'selected' : ''}" onclick="selectDate('${item.date}')" title="${item.date} 共用: ${formatBytes(item.total)} (下行:${formatBytes(item.download)}, 上行:${formatBytes(item.upload)})">
                            <span class="chart-val-label">${formatBytes(item.total)}</span>
                            <div class="chart-bar-wrap">
                                <div class="chart-bar-ul" style="height: ${ulHeight}px;"></div>
                                <div class="chart-bar-dl" style="height: ${dlHeight}px;"></div>
                            </div>
                            <span class="chart-label">${item.date.slice(5)}</span>
                        </div>
                    `;
                }).join('');
            }

            // 渲染每日表格
            const tbody = document.getElementById('daily-table-body');
            if (list.length === 0) {
                tbody.innerHTML = '<tr><td colspan="7" style="text-align: center; color: var(--text-sub); padding: 20px;">暂无每日汇总数据</td></tr>';
                return;
            }

            tbody.innerHTML = list.map(item => {
                const isSelected = (item.date === data.selected_date);
                return `
                    <tr class="${isSelected ? 'active-row' : ''}">
                        <td>
                            <strong>${item.date}</strong>
                            ${item.date === data.overview?.today?.date ? '<span class="badge badge-success" style="margin-left:4px;">今天</span>' : ''}
                            ${item.date === data.overview?.yesterday?.date ? '<span class="badge" style="margin-left:4px;">昨天</span>' : ''}
                        </td>
                        <td><strong style="color:var(--primary); font-size:14px;">${formatBytes(item.total)}</strong></td>
                        <td style="color:var(--success); font-weight:600;">${formatBytes(item.download)}</td>
                        <td style="color:var(--primary);">${formatBytes(item.upload)}</td>
                        <td><span class="badge">${item.proc_count} 个程序</span></td>
                        <td>
                            <strong>${item.top_process}</strong>
                            <span style="font-size: 11px; color: var(--text-sub); margin-left: 6px;">(${formatBytes(item.top_process_bytes)})</span>
                        </td>
                        <td style="text-align: center;">
                            <button class="btn-view-day" onclick="selectDate('${item.date}')">
                                ${isSelected ? '✓ 正在查看' : '🔍 查看排行'}
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
            bannerDesc.innerText = `当天共用流量：${formatBytes(data.selected_date_total || 0)}，上榜程序：${ranking.length} 个`;
            bannerStats.innerText = currentSearch ? `当前搜索关键词: "${currentSearch}"` : `排序模式: ${currentSort === 'total' ? '总流量' : currentSort === 'download' ? '下行' : '上行'}`;

            const tbody = document.getElementById('ranking-table-body');
            if (ranking.length === 0) {
                tbody.innerHTML = '<tr><td colspan="8" style="text-align: center; color: var(--text-sub); padding: 30px;">未找到匹配的程序流量记录</td></tr>';
                return;
            }

            tbody.innerHTML = ranking.map(item => {
                let rankClass = "rank-badge";
                if (item.rank === 1) rankClass += " rank-1";
                else if (item.rank === 2) rankClass += " rank-2";
                else if (item.rank === 3) rankClass += " rank-3";

                const isHuge = item.total > 1024 * 1024 * 1024; // > 1GB
                const isBig = item.total > 300 * 1024 * 1024;  // > 300MB

                let tagHtml = '<span class="badge">正常</span>';
                if (isHuge) tagHtml = '<span class="badge badge-danger">⚡ >1GB 巨量</span>';
                else if (isBig) tagHtml = '<span class="badge badge-warn">⚠️ 大流量</span>';

                const hostsHtml = (item.top_hosts || []).map(h => `<span class="host-tag">${h}</span>`).join('') || '<span style="color:var(--text-sub); font-size:12px;">-</span>';

                return `
                    <tr>
                        <td style="text-align: center;"><span class="${rankClass}">${item.rank}</span></td>
                        <td>
                            <div style="font-weight: 600; font-size: 14px; color: var(--text-main);">${item.process_name}</div>
                            <div style="font-size: 11px; color: var(--text-sub); max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${item.process_path}">${item.process_path || '-'}</div>
                        </td>
                        <td style="color: var(--success); font-weight: 600;">${formatBytes(item.download)}</td>
                        <td style="color: var(--primary);">${formatBytes(item.upload)}</td>
                        <td><strong style="color: var(--text-main); font-size: 14px;">${formatBytes(item.total)}</strong></td>
                        <td>
                            <div style="display:flex; justify-content:space-between; font-size:11px; color:var(--text-sub);">
                                <span>${item.pct}%</span>
                            </div>
                            <div class="progress-bar-bg">
                                <div class="progress-bar-fill" style="width: ${Math.min(item.pct, 100)}%;"></div>
                            </div>
                        </td>
                        <td>${hostsHtml}</td>
                        <td>${tagHtml}</td>
                    </tr>
                `;
            }).join('');
        }

        // 渲染实时活跃吞吐
        function renderLiveSection(data) {
            const rt = data.realtime || {};
            const speeds = rt.process_speeds || {};
            const liveEntries = Object.entries(speeds)
                .filter(([p, s]) => (s.up > 20 || s.down > 20))
                .sort((a, b) => (b[1].down + b[1].up) - (a[1].down + a[1].up));

            document.getElementById('live-proc-count').innerText = `${liveEntries.length} 个程序活跃`;

            const tbody = document.getElementById('live-table-body');
            if (liveEntries.length === 0) {
                tbody.innerHTML = '<tr><td colspan="4" style="text-align: center; color: var(--text-sub); padding: 15px;">当前无明显吞吐传输</td></tr>';
                return;
            }

            tbody.innerHTML = liveEntries.map(([proc, s]) => {
                const isHigh = (s.down > 1024 * 1024 || s.up > 1024 * 1024);
                return `
                    <tr>
                        <td><strong>${proc}</strong></td>
                        <td style="color: var(--success); font-weight: 600;">${formatSpeed(s.down)}</td>
                        <td style="color: var(--primary); font-weight: 600;">${formatSpeed(s.up)}</td>
                        <td>${isHigh ? '<span class="badge badge-danger">⚡ 高速传输中</span>' : '<span class="badge badge-success">传输中</span>'}</td>
                    </tr>
                `;
            }).join('');
        }

        // 首次加载
        fetchData(true);
        // 每 2 秒定时轮询
        setInterval(() => fetchData(false), 2000);
    </script>
</body>
</html>
"""

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

def main():
    global collector
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
    main()
