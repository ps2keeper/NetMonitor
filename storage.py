"""SQLite 存储：建表与版本化迁移、批量写入、数据保留与看板查询。

表结构
- process_traffic：小时粒度明细（日期, 小时, 进程, 域名），只用于追溯，按 HOURLY_RETENTION_DAYS 清理
- daily_traffic：天粒度汇总（日期, 进程, 路径, 域名），看板与悬浮窗的全部统计都读这张表
- route_traffic：小时粒度的代理/直连与出站节点统计
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

SCHEMA_VERSION = 1
UNATTRIBUTED = "未归属（短连接）"

_UPSERT_HOURLY = """
    INSERT INTO process_traffic (
        date_str, hour_str, process_name, process_path, host,
        upload_bytes, download_bytes, proxy_bytes, direct_bytes
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(date_str, hour_str, process_name, host) DO UPDATE SET
        upload_bytes = upload_bytes + excluded.upload_bytes,
        download_bytes = download_bytes + excluded.download_bytes,
        proxy_bytes = COALESCE(proxy_bytes, 0) + excluded.proxy_bytes,
        direct_bytes = COALESCE(direct_bytes, 0) + excluded.direct_bytes,
        process_path = COALESCE(NULLIF(excluded.process_path, ''), process_traffic.process_path)
"""

_UPSERT_DAILY = """
    INSERT INTO daily_traffic (
        date_str, process_name, process_path, host,
        upload_bytes, download_bytes, proxy_bytes, direct_bytes
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(date_str, process_name, process_path, host) DO UPDATE SET
        upload_bytes = upload_bytes + excluded.upload_bytes,
        download_bytes = download_bytes + excluded.download_bytes,
        proxy_bytes = proxy_bytes + excluded.proxy_bytes,
        direct_bytes = direct_bytes + excluded.direct_bytes
"""

_UPSERT_ROUTE = """
    INSERT INTO route_traffic (date_str, hour_str, route, node, upload_bytes, download_bytes)
    VALUES (?, ?, ?, ?, ?, ?)
    ON CONFLICT(date_str, hour_str, route, node) DO UPDATE SET
        upload_bytes = upload_bytes + excluded.upload_bytes,
        download_bytes = download_bytes + excluded.download_bytes
"""


def connect(db_file: str, timeout: float = 5.0) -> sqlite3.Connection:
    conn = sqlite3.connect(db_file, timeout=timeout, check_same_thread=False)
    conn.execute(f"PRAGMA busy_timeout = {int(timeout * 1000)}")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


# --- 建表与迁移 ----------------------------------------------------------------

def init_db(db_file: str) -> None:
    conn = connect(db_file)
    try:
        # WAL：看板读取与采集写入互不阻塞（设置持久保存在数据库文件中）
        conn.execute("PRAGMA journal_mode = WAL")
        with conn:
            _create_tables(conn)
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version < 1:
                _migrate_v1(conn)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    finally:
        conn.close()


def _create_tables(conn: sqlite3.Connection) -> None:
    conn.execute("""
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
    columns = {row[1] for row in conn.execute("PRAGMA table_info(process_traffic)")}
    for col in ("proxy_bytes", "direct_bytes"):
        if col not in columns:
            conn.execute(f"ALTER TABLE process_traffic ADD COLUMN {col} INTEGER DEFAULT 0")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_traffic (
            date_str TEXT NOT NULL,
            process_name TEXT NOT NULL,
            process_path TEXT NOT NULL DEFAULT '',
            host TEXT NOT NULL,
            upload_bytes INTEGER NOT NULL DEFAULT 0,
            download_bytes INTEGER NOT NULL DEFAULT 0,
            proxy_bytes INTEGER NOT NULL DEFAULT 0,
            direct_bytes INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (date_str, process_name, process_path, host)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS route_traffic (
            date_str TEXT,
            hour_str TEXT,
            route TEXT,
            node TEXT,
            upload_bytes INTEGER,
            download_bytes INTEGER,
            PRIMARY KEY (date_str, hour_str, route, node)
        )
    """)


def _migrate_v1(conn: sqlite3.Connection) -> None:
    """旧版数据库升级：回填天粒度汇总、删除只写不读的表和冗余索引。"""
    if conn.execute("SELECT 1 FROM daily_traffic LIMIT 1").fetchone() is None:
        conn.execute("""
            INSERT INTO daily_traffic (date_str, process_name, process_path, host,
                                       upload_bytes, download_bytes, proxy_bytes, direct_bytes)
            SELECT date_str, process_name, COALESCE(process_path, ''), host,
                   SUM(COALESCE(upload_bytes, 0)), SUM(COALESCE(download_bytes, 0)),
                   SUM(COALESCE(proxy_bytes, 0)), SUM(COALESCE(direct_bytes, 0))
            FROM process_traffic
            GROUP BY date_str, process_name, COALESCE(process_path, ''), host
        """)
    # completed_connections 每条连接一行、从未被读取，是数据库膨胀的主要来源
    conn.execute("DROP TABLE IF EXISTS completed_connections")
    # 主键已以 date_str 开头；看板改读 daily_traffic 后按进程名的索引也不再使用
    conn.execute("DROP INDEX IF EXISTS idx_proc_date")
    conn.execute("DROP INDEX IF EXISTS idx_proc_name")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_route_date ON route_traffic(date_str)")


# --- 写入 ----------------------------------------------------------------------

def write_batch(conn: sqlite3.Connection, proc_rows, route_rows) -> None:
    """在一个事务中写入一批增量；失败时抛出异常，由调用方保留数据重试。

    proc_rows: (date, hour, process, path, host, up, down, proxy, direct)
    route_rows: (date, hour, route, node, up, down)
    """
    proc_rows = list(proc_rows)
    with conn:
        conn.executemany(_UPSERT_HOURLY, proc_rows)
        conn.executemany(_UPSERT_DAILY, [
            (d, proc, path or "", host, up, down, px, dr)
            for d, _h, proc, path, host, up, down, px, dr in proc_rows
        ])
        conn.executemany(_UPSERT_ROUTE, list(route_rows))


def backfill_initial_routes(conn: sqlite3.Connection, date_str: str, seed_proc_routes) -> None:
    """首次启动时若今日尚无代理/直连拆分，按当前活跃连接特征一次性回填（不改变总流量）。"""
    already = conn.execute(
        "SELECT COALESCE(SUM(proxy_bytes + direct_bytes), 0) FROM daily_traffic WHERE date_str = ?",
        (date_str,)).fetchone()[0]
    if already:
        return
    ratios = {}
    for proc, host, route, total in seed_proc_routes:
        st = ratios.setdefault((proc, host), {"proxy": 0, "direct": 0})
        if route in st:
            st[route] += total
    with conn:
        for table, key in (("process_traffic", "rowid"), ("daily_traffic", "rowid")):
            rows = conn.execute(
                f"SELECT {key}, process_name, host, upload_bytes + download_bytes FROM {table} WHERE date_str = ?",
                (date_str,)).fetchall()
            for rowid, proc, host, total in rows:
                st = ratios.get((proc, host))
                if st and (st["proxy"] + st["direct"]) > 0:
                    proxy = int(round(total * st["proxy"] / (st["proxy"] + st["direct"])))
                    conn.execute(f"UPDATE {table} SET proxy_bytes = ?, direct_bytes = ? WHERE {key} = ?",
                                 (proxy, max(total - proxy, 0), rowid))


# --- 数据保留 ------------------------------------------------------------------

def prune(conn: sqlite3.Connection, today: date, hourly_days: int, daily_days: int) -> int:
    """删除超出保留期的数据，返回删除的行数。保留天数为 0 表示不清理。"""
    removed = 0
    with conn:
        if hourly_days:
            cutoff = (today - timedelta(days=hourly_days)).isoformat()
            removed += conn.execute("DELETE FROM process_traffic WHERE date_str < ?", (cutoff,)).rowcount
        if daily_days:
            cutoff = (today - timedelta(days=daily_days)).isoformat()
            for table in ("daily_traffic", "route_traffic", "process_traffic"):
                removed += conn.execute(f"DELETE FROM {table} WHERE date_str < ?", (cutoff,)).rowcount
    return removed


def vacuum(conn: sqlite3.Connection) -> None:
    conn.execute("VACUUM")


# --- 查询 ----------------------------------------------------------------------

def _month_range(month: str) -> tuple[str, str]:
    """'2026-10' -> ('2026-10-01', '2026-11-01')，用于可走索引的日期范围条件。"""
    year, mon = (int(x) for x in month.split("-"))
    nxt = f"{year + 1}-01" if mon == 12 else f"{year}-{mon + 1:02d}"
    return f"{month}-01", f"{nxt}-01"


def totals(conn, where: str = "", params=()) -> tuple[int, int, int]:
    """返回 (下载, 上传, 天数)。"""
    dl, ul, days = conn.execute(f"""
        SELECT COALESCE(SUM(download_bytes), 0), COALESCE(SUM(upload_bytes), 0), COUNT(DISTINCT date_str)
        FROM daily_traffic {where}
    """, params).fetchone()
    return dl, ul, days


def month_totals(conn, month: str) -> tuple[int, int, int]:
    return totals(conn, "WHERE date_str >= ? AND date_str < ?", _month_range(month))


def route_split(conn, where: str, params, node_limit: int = 0) -> dict:
    """代理 / 直连上下行与总量，可选附带代理节点排行。"""
    res = {
        "proxy": {"upload": 0, "download": 0, "total": 0},
        "direct": {"upload": 0, "download": 0, "total": 0},
    }
    for route, ul, dl in conn.execute(f"""
        SELECT route, COALESCE(SUM(upload_bytes), 0), COALESCE(SUM(download_bytes), 0)
        FROM route_traffic {where} GROUP BY route
    """, params):
        if route in res:
            res[route] = {"upload": ul, "download": dl, "total": ul + dl}
    if node_limit:
        rows = conn.execute(f"""
            SELECT node, COALESCE(SUM(upload_bytes), 0), COALESCE(SUM(download_bytes), 0),
                   COALESCE(SUM(upload_bytes + download_bytes), 0) AS tot
            FROM route_traffic {where} {"AND" if where else "WHERE"} route = 'proxy'
            GROUP BY node ORDER BY tot DESC LIMIT ?
        """, (*params, int(node_limit))).fetchall()
        res["nodes"] = [{"node": n, "upload": ul, "download": dl, "total": t} for n, ul, dl, t in rows]
    return res


def route_split_for_day(conn, day: str, node_limit: int = 0) -> dict:
    return route_split(conn, "WHERE date_str = ?", (day,), node_limit)


def route_split_for_month(conn, month: str) -> dict:
    return route_split(conn, "WHERE date_str >= ? AND date_str < ?", _month_range(month))


def available_months(conn) -> list[str]:
    return [row[0] for row in conn.execute(
        "SELECT DISTINCT substr(date_str, 1, 7) AS m FROM daily_traffic ORDER BY m DESC")]


def daily_summary(conn, limit: int = 60, top_days: int = 14) -> list[dict]:
    rows = conn.execute("""
        SELECT date_str, SUM(download_bytes), SUM(upload_bytes),
               SUM(download_bytes + upload_bytes), COUNT(DISTINCT process_name)
        FROM daily_traffic GROUP BY date_str ORDER BY date_str DESC LIMIT ?
    """, (limit,)).fetchall()
    if not rows:
        return []
    oldest = rows[-1][0]
    top_since = rows[min(top_days, len(rows)) - 1][0]
    # 一次窗口查询取得每天的流量冠军，代替逐日查询
    top = {d: (p, t) for d, p, t in conn.execute("""
        SELECT date_str, process_name, tot FROM (
            SELECT date_str, process_name, SUM(download_bytes + upload_bytes) AS tot,
                   ROW_NUMBER() OVER (PARTITION BY date_str
                                      ORDER BY SUM(download_bytes + upload_bytes) DESC) AS rn
            FROM daily_traffic WHERE date_str >= ? GROUP BY date_str, process_name
        ) WHERE rn = 1
    """, (top_since,))}
    routes = {}
    for d, route, tot in conn.execute("""
        SELECT date_str, route, COALESCE(SUM(upload_bytes + download_bytes), 0)
        FROM route_traffic WHERE date_str >= ? GROUP BY date_str, route
    """, (oldest,)):
        if route in ("proxy", "direct"):
            routes.setdefault(d, {"proxy": 0, "direct": 0})[route] = tot
    summary = []
    for d, dl, ul, tot, count in rows:
        top_proc = top.get(d)
        split = routes.get(d, {"proxy": 0, "direct": 0})
        summary.append({
            "date": d, "download": dl, "upload": ul, "total": tot,
            "proxy": split["proxy"], "direct": split["direct"], "proc_count": count,
            "top_process": top_proc[0] if top_proc else "-",
            "top_process_bytes": top_proc[1] if top_proc else 0,
        })
    return summary


_SORT_COLUMNS = {"total": "total", "download": "dl", "upload": "ul", "proxy": "p_bytes", "direct": "d_bytes"}


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def ranking(conn, day: str | None, search: str = "", sort: str = "total", limit: int = 100,
            with_hosts: bool = True) -> tuple[list[dict], int]:
    """按（进程名, 路径）排行；day 为 None 表示全部日期。返回 (排行, 所选范围总流量)。"""
    clauses, params = [], []
    if day is not None:
        clauses.append("date_str = ?")
        params.append(day)
    if search:
        pattern = f"%{_escape_like(search)}%"
        clauses.append("(process_name LIKE ? ESCAPE '\\' OR process_path LIKE ? ESCAPE '\\')")
        params.extend([pattern, pattern])
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    selected_total = conn.execute(
        f"SELECT COALESCE(SUM(download_bytes + upload_bytes), 0) FROM daily_traffic {where}", params
    ).fetchone()[0] or 0
    order = _SORT_COLUMNS.get(sort, "total")
    rows = conn.execute(f"""
        SELECT process_name, process_path,
               SUM(download_bytes) AS dl, SUM(upload_bytes) AS ul,
               SUM(download_bytes + upload_bytes) AS total,
               SUM(proxy_bytes) AS p_bytes, SUM(direct_bytes) AS d_bytes
        FROM daily_traffic {where}
        GROUP BY process_name, process_path
        ORDER BY {order} DESC
        LIMIT ?
    """, (*params, limit)).fetchall()

    hosts = {}
    if with_hosts and rows:
        day_where, day_params = ("WHERE date_str = ?", (day,)) if day is not None else ("", ())
        # 一次窗口查询取得每个程序访问量最大的 3 个目标，代替逐个程序查询
        for proc, path, host in conn.execute(f"""
            SELECT process_name, process_path, host FROM (
                SELECT process_name, process_path, host,
                       ROW_NUMBER() OVER (PARTITION BY process_name, process_path
                                          ORDER BY SUM(download_bytes + upload_bytes) DESC) AS rn
                FROM daily_traffic {day_where} GROUP BY process_name, process_path, host
            ) WHERE rn <= 3
        """, day_params):
            hosts.setdefault((proc, path), []).append(host)

    denominator = selected_total or 1
    result = []
    for rank, (proc, path, dl, ul, tot, p_bytes, d_bytes) in enumerate(rows, 1):
        item = {
            "rank": rank, "process_name": proc, "process_path": path,
            "download": dl, "upload": ul, "total": tot, "proxy": p_bytes, "direct": d_bytes,
            "pct": round(tot / denominator * 100, 2),
        }
        if with_hosts:
            item["top_hosts"] = hosts.get((proc, path), [])
        result.append(item)
    return result, selected_total
