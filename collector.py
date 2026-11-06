"""流量采集：轮询 Mihomo /connections，按进程归因并写入 SQLite。

两条准确性保证：
1. 内核全局计数（uploadTotal / downloadTotal）作为总量基准。按连接归因不到的部分——两次轮询之间
   建立又关闭的短连接、连接关闭前最后一段流量——记为“未归属（短连接）”，总流量不再偏低。
2. 写库失败时增量留在内存中，下一轮合并重试，不会因为数据库暂时被占用而丢失。
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime

import nm_config
import storage

logger = logging.getLogger("netmonitor.collector")

UNATTRIBUTED = storage.UNATTRIBUTED
MAX_PENDING_KEYS = 50000  # 长时间写不进数据库时的内存上限
WRITE_TIMEOUT = 0.5       # 秒

STATUS_MESSAGES = {
    "starting": "正在连接 Clash 外部控制器…",
    "ok": "采集正常",
    "auth_failed": "Clash 外部控制密钥不正确（HTTP {code}）：请设置环境变量 CLASH_SECRET，或检查 Clash Verge 的外部控制设置",
    "controller_disabled": "Clash Verge 未开启外部控制：请在 Clash Verge「设置」中打开“外部控制器”",
    "unreachable": "无法连接 Clash 外部控制器 {url}：请确认 Clash 已运行并开启外部控制",
    "error": "采集异常：{detail}",
}


class ClashError(Exception):
    def __init__(self, kind: str, **fields):
        super().__init__(kind)
        self.kind = kind
        self.fields = fields


def classify_route(conn: dict) -> tuple[str, str]:
    """根据连接的 chains 判断出站类型。chains[0] 为最终实际出站（节点或 DIRECT/REJECT）。"""
    chains = conn.get("chains") or []
    node = chains[0] if chains else "DIRECT"
    up = str(node).upper()
    if up.startswith("REJECT"):
        return "reject", node
    if up == "DIRECT":
        return "direct", "DIRECT"
    return "proxy", node


def _process_identity(meta: dict) -> tuple[str, str, str]:
    path = meta.get("processPath") or ""
    proc = meta.get("process") or os.path.basename(path) or "系统/未知"
    host = meta.get("host") or meta.get("destinationIP") or "Unknown"
    return proc, path, host


def _empty_realtime() -> dict:
    return {
        "current_down_speed": 0, "current_up_speed": 0, "process_speeds": {},
        "proxy_down_speed": 0, "proxy_up_speed": 0, "direct_down_speed": 0, "direct_up_speed": 0,
        "last_updated": time.time(),
    }


class TrafficCollector(threading.Thread):
    def __init__(self, db_file: str | None = None, poll_interval: float | None = None,
                 resolve_clash=nm_config.resolve_clash, opener=urllib.request.urlopen):
        super().__init__(daemon=True, name="TrafficCollector")
        self.db_file = db_file or nm_config.DB_FILE
        self.poll_interval = poll_interval or nm_config.POLL_INTERVAL
        self._resolve_clash = resolve_clash
        self._opener = opener
        self.lock = threading.Lock()
        self.active_conns: dict = {}
        self.realtime_stats = _empty_realtime()
        self.first_poll = True
        self.prev_totals = None        # 内核全局 (upload, download) 计数
        self._debt = [0, 0]            # 归因暂时超过全局计数的部分，从后续“未归属”中扣回
        self.pending_proc: dict = {}   # (date, hour, proc, path, host) -> [up, down, proxy, direct]
        self.pending_route: dict = {}  # (date, hour, route, node) -> [up, down]
        self.last_snapshot_time = None
        self._clash = None
        self._clash_resolved_at = 0.0
        self._conn = None
        self._last_prune_day = None
        self._stop_event = threading.Event()
        self.status = {"state": "starting", "message": STATUS_MESSAGES["starting"], "since": time.time(),
                       "last_success": None, "consecutive_failures": 0,
                       "pending_rows": 0, "last_write_error": None, "clash_source": None}

    # --- 线程生命周期 -----------------------------------------------------------

    def run(self) -> None:
        logger.info("采集服务启动，数据库：%s", self.db_file)
        while not self._stop_event.is_set():
            started = time.time()
            self.poll_once()
            self._stop_event.wait(max(self.poll_interval - (time.time() - started), 0.05))
        self.close()

    def close(self) -> None:
        """写入剩余缓冲并关闭数据库连接。"""
        self.flush()
        self._reset_db()

    def stop(self) -> None:
        self._stop_event.set()

    def status_snapshot(self) -> dict:
        with self.lock:
            return dict(self.status)

    def realtime_snapshot(self) -> dict:
        with self.lock:
            rt = dict(self.realtime_stats)
            rt["process_speeds"] = dict(rt["process_speeds"])
            return rt

    # --- 采集 -------------------------------------------------------------------

    def _clash_target(self) -> dict:
        # 出错时每 15 秒重新读取一次 Clash 配置，用户改了密钥/端口无需重启
        if self._clash is None or (self.status["state"] != "ok" and time.time() - self._clash_resolved_at > 15):
            self._clash = self._resolve_clash()
            self._clash_resolved_at = time.time()
            with self.lock:
                self.status["clash_source"] = self._clash.get("source")
        return self._clash

    def fetch_connections(self) -> dict:
        target = self._clash_target()
        headers = {"Authorization": f"Bearer {target['secret']}"} if target.get("secret") else {}
        req = urllib.request.Request(f"{target['url']}/connections", headers=headers)
        try:
            with self._opener(req, timeout=2.5) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise ClashError("auth_failed", code=e.code)
            raise ClashError("error", detail=f"HTTP {e.code}")
        except (urllib.error.URLError, OSError):
            if target.get("problem") == "controller_disabled":
                raise ClashError("controller_disabled")
            raise ClashError("unreachable", url=target["url"])

    def poll_once(self) -> None:
        try:
            data = self.fetch_connections()
        except ClashError as e:
            self._set_failure(e.kind, **e.fields)
        except Exception as e:  # 网络库之外的意外错误：记录完整堆栈，下一轮继续
            logger.exception("拉取连接列表失败")
            self._set_failure("error", detail=type(e).__name__)
        else:
            try:
                self.process_snapshot(data, time.time(), datetime.now())
            except Exception as e:
                logger.exception("处理连接快照失败")
                self._set_failure("error", detail=type(e).__name__)
            else:
                self._set_ok()
        self.flush()
        self.maybe_prune()

    def _set_status(self, state: str, message: str, failure: bool) -> None:
        with self.lock:
            changed = self.status["state"] != state
            self.status["state"] = state
            self.status["message"] = message
            if changed:
                self.status["since"] = time.time()
            if failure:
                self.status["consecutive_failures"] += 1
            else:
                self.status["consecutive_failures"] = 0
                self.status["last_success"] = time.time()
        if changed:
            (logger.warning if failure else logger.info)("采集状态：%s", message)

    def _set_failure(self, kind: str, **fields) -> None:
        self._set_status(kind, STATUS_MESSAGES[kind].format(**fields), failure=True)

    def _set_ok(self) -> None:
        self._set_status("ok", STATUS_MESSAGES["ok"], failure=False)

    def process_snapshot(self, data: dict, now_ts: float, now_dt: datetime) -> None:
        """把一次 /connections 快照转为增量，累加到待写缓冲并更新实时速率。"""
        time_delta = (max(now_ts - self.last_snapshot_time, 0.1) if self.last_snapshot_time is not None
                      else self.poll_interval)
        self.last_snapshot_time = now_ts
        date_str = now_dt.strftime("%Y-%m-%d")
        hour_str = now_dt.strftime("%H")
        is_initial_seed = self.first_poll
        self.first_poll = False

        seen_ids = set()
        proc_deltas = {}
        attributed = [0, 0]
        route_speed = {"proxy": [0, 0], "direct": [0, 0]}
        seed_proc_routes = []

        for conn in data.get("connections") or []:
            cid = conn.get("id")
            if not cid:
                continue
            seen_ids.add(cid)
            cur_up = int(conn.get("upload") or 0)
            cur_down = int(conn.get("download") or 0)
            meta = conn.get("metadata") or {}
            proc, path, host = _process_identity(meta)
            route, node = classify_route(conn)

            prev = self.active_conns.get(cid)
            if prev is not None:
                delta_up = max(cur_up - prev["upload"], 0)
                delta_down = max(cur_down - prev["download"], 0)
            elif is_initial_seed:
                # 启动后首次轮询只登记基准水位，避免重启服务重复累加存量长连接流量
                delta_up = delta_down = 0
                if route != "reject" and (cur_up + cur_down) > 0:
                    seed_proc_routes.append((proc, host, route, cur_up + cur_down))
            else:
                delta_up, delta_down = cur_up, cur_down
            self.active_conns[cid] = {"upload": cur_up, "download": cur_down}

            if delta_up <= 0 and delta_down <= 0:
                continue
            attributed[0] += delta_up
            attributed[1] += delta_down
            stat = proc_deltas.setdefault(proc, {"up": 0, "down": 0, "proxy": 0, "direct": 0, "nodes": set()})
            stat["up"] += delta_up
            stat["down"] += delta_down
            total = delta_up + delta_down
            proxy_bytes = total if route == "proxy" else 0
            direct_bytes = total if route == "direct" else 0
            if route == "proxy":
                stat["proxy"] += total
                stat["nodes"].add(node)
            elif route == "direct":
                stat["direct"] += total
            self._add_pending_proc((date_str, hour_str, proc, path, host), delta_up, delta_down, proxy_bytes, direct_bytes)
            if route != "reject":
                self._add_pending_route((date_str, hour_str, route, node), delta_up, delta_down)
                route_speed[route][0] += delta_up
                route_speed[route][1] += delta_down

        for cid in set(self.active_conns) - seen_ids:
            del self.active_conns[cid]

        global_delta = self._global_delta(data, is_initial_seed)
        if global_delta is not None:
            unattributed = []
            for i in (0, 1):
                gap = global_delta[i] - attributed[i] - self._debt[i]
                self._debt[i] = max(-gap, 0)
                unattributed.append(max(gap, 0))
            if unattributed[0] or unattributed[1]:
                self._add_pending_proc((date_str, hour_str, UNATTRIBUTED, "", "-"), unattributed[0], unattributed[1], 0, 0)
        speed_totals = global_delta if global_delta is not None else attributed

        if is_initial_seed and seed_proc_routes:
            self._backfill_routes(date_str, seed_proc_routes)

        with self.lock:
            self.realtime_stats = {
                "current_up_speed": speed_totals[0] / time_delta,
                "current_down_speed": speed_totals[1] / time_delta,
                "process_speeds": {
                    p: {
                        "up": d["up"] / time_delta,
                        "down": d["down"] / time_delta,
                        "proxy_speed": d["proxy"] / time_delta,
                        "direct_speed": d["direct"] / time_delta,
                        "route": ("proxy" if d["proxy"] and not d["direct"]
                                  else "direct" if d["direct"] and not d["proxy"] else "mixed"),
                        "node": ", ".join(sorted(d["nodes"])) if d["nodes"] else "DIRECT",
                    }
                    for p, d in proc_deltas.items()
                },
                "proxy_up_speed": route_speed["proxy"][0] / time_delta,
                "proxy_down_speed": route_speed["proxy"][1] / time_delta,
                "direct_up_speed": route_speed["direct"][0] / time_delta,
                "direct_down_speed": route_speed["direct"][1] / time_delta,
                "last_updated": now_ts,
            }

    def _global_delta(self, data: dict, is_initial_seed: bool):
        up, down = data.get("uploadTotal"), data.get("downloadTotal")
        if not isinstance(up, int) or not isinstance(down, int):
            return None
        prev = self.prev_totals
        self.prev_totals = (up, down)
        if prev is None or is_initial_seed:
            return None
        delta = [up - prev[0], down - prev[1]]
        if delta[0] < 0 or delta[1] < 0:
            # 内核重启后计数清零：这段时间的流量就是当前计数本身
            delta = [up, down]
            self._debt = [0, 0]
        return delta

    def _add_pending_proc(self, key, up, down, proxy, direct) -> None:
        if key not in self.pending_proc and len(self.pending_proc) >= MAX_PENDING_KEYS:
            logger.error("待写入数据超过 %d 组，丢弃最新增量：%s", MAX_PENDING_KEYS, key)
            return
        acc = self.pending_proc.setdefault(key, [0, 0, 0, 0])
        acc[0] += up
        acc[1] += down
        acc[2] += proxy
        acc[3] += direct

    def _add_pending_route(self, key, up, down) -> None:
        acc = self.pending_route.setdefault(key, [0, 0])
        acc[0] += up
        acc[1] += down

    # --- 写库 -------------------------------------------------------------------

    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            # 等锁时间要短：写不进去的数据会保留重试，不能让写库拖慢轮询、错过短连接
            self._conn = storage.connect(self.db_file, timeout=WRITE_TIMEOUT)
        return self._conn

    def _reset_db(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
        self._conn = None

    def flush(self) -> bool:
        """写入缓冲中的全部增量；失败时保留，下一轮重试。"""
        if not self.pending_proc and not self.pending_route:
            return True
        proc_rows = [(*key, *vals) for key, vals in self.pending_proc.items()]
        route_rows = [(*key, *vals) for key, vals in self.pending_route.items()]
        try:
            storage.write_batch(self._db(), proc_rows, route_rows)
        except sqlite3.Error as e:
            self._reset_db()
            with self.lock:
                first_error = self.status["last_write_error"] is None
                self.status["last_write_error"] = f"{type(e).__name__}: {e}"
                self.status["pending_rows"] = len(self.pending_proc)
            if first_error:
                logger.warning("写入数据库失败，数据已保留待重试：%s", e)
            return False
        self.pending_proc.clear()
        self.pending_route.clear()
        with self.lock:
            if self.status["last_write_error"] is not None:
                logger.info("数据库写入已恢复")
            self.status["last_write_error"] = None
            self.status["pending_rows"] = 0
        return True

    def _backfill_routes(self, date_str: str, seed_proc_routes) -> None:
        try:
            storage.backfill_initial_routes(self._db(), date_str, seed_proc_routes)
        except sqlite3.Error:
            logger.warning("回填今日代理/直连拆分失败", exc_info=True)
            self._reset_db()

    def maybe_prune(self) -> None:
        today = datetime.now().date()
        if self._last_prune_day == today:
            return
        self._last_prune_day = today
        try:
            removed = storage.prune(self._db(), today, nm_config.HOURLY_RETENTION_DAYS, nm_config.RETENTION_DAYS)
            if removed:
                logger.info("已按保留策略清理 %d 行历史数据", removed)
        except sqlite3.Error:
            logger.warning("清理历史数据失败", exc_info=True)
            self._reset_db()
