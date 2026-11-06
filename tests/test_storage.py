from tests import support  # noqa: F401

import os
import sqlite3
import tempfile
import unittest
from datetime import date

import storage

LEGACY_SCHEMA = """
CREATE TABLE process_traffic (date_str TEXT, hour_str TEXT, process_name TEXT, process_path TEXT, host TEXT,
    upload_bytes INTEGER, download_bytes INTEGER, PRIMARY KEY (date_str, hour_str, process_name, host));
CREATE TABLE completed_connections (id TEXT PRIMARY KEY, start_time TEXT, end_time TEXT, process_name TEXT,
    process_path TEXT, host TEXT, dest_ip TEXT, rule TEXT, upload_bytes INTEGER, download_bytes INTEGER);
CREATE INDEX idx_proc_date ON process_traffic(date_str);
CREATE INDEX idx_proc_name ON process_traffic(process_name);
"""


def row(day, hour, proc, host, down, path=None, up=0, proxy=0, direct=0):
    return (day, hour, proc, path if path is not None else f"C:\\{proc}", host, up, down, proxy, direct)


class MigrationTest(unittest.TestCase):
    def test_legacy_database_is_upgraded_in_place(self):
        db_file = os.path.join(tempfile.mkdtemp(), "legacy.db")
        db = sqlite3.connect(db_file)
        db.executescript(LEGACY_SCHEMA)
        db.executemany("INSERT INTO process_traffic VALUES (?, ?, ?, ?, ?, ?, ?)", [
            ("2026-10-01", "09", "a.exe", r"C:\a.exe", "x.test", 10, 100),
            ("2026-10-01", "10", "a.exe", r"C:\a.exe", "x.test", 5, 50),
            ("2026-10-02", "09", "b.exe", r"C:\b.exe", "y.test", 0, 7),
        ])
        db.execute("INSERT INTO completed_connections (id) VALUES ('c1')")
        db.commit()
        db.close()

        storage.init_db(db_file)
        storage.init_db(db_file)  # 幂等：第二次不应重复回填

        db = sqlite3.connect(db_file)
        self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], storage.SCHEMA_VERSION)
        self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertEqual(db.execute(
            "SELECT date_str, process_name, upload_bytes, download_bytes FROM daily_traffic ORDER BY 1").fetchall(),
            [("2026-10-01", "a.exe", 15, 150), ("2026-10-02", "b.exe", 0, 7)])
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        indexes = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        db.close()
        self.assertNotIn("completed_connections", tables)
        self.assertFalse({"idx_proc_date", "idx_proc_name"} & indexes)


class QueryTest(unittest.TestCase):
    def setUp(self):
        self.db_file = os.path.join(tempfile.mkdtemp(), "q.db")
        storage.init_db(self.db_file)
        self.conn = storage.connect(self.db_file)
        self.addCleanup(self.conn.close)

    def write(self, rows, routes=()):
        storage.write_batch(self.conn, rows, routes)

    def test_same_name_different_path_is_ranked_separately(self):
        self.write([
            row("2026-11-06", "10", "updater.exe", "a.test", 300, path=r"C:\Vendor\updater.exe"),
            row("2026-11-06", "10", "updater.exe", "b.test", 200, path=r"C:\Users\x\AppData\updater.exe"),
        ])
        ranking, total = storage.ranking(self.conn, "2026-11-06")
        self.assertEqual(total, 500)
        self.assertEqual([(r["process_path"], r["total"]) for r in ranking],
                         [(r"C:\Vendor\updater.exe", 300), (r"C:\Users\x\AppData\updater.exe", 200)])

    def test_empty_path_does_not_overwrite_known_path(self):
        self.write([row("2026-11-06", "10", "a.exe", "x", 1, path=r"C:\a.exe")])
        self.write([row("2026-11-06", "10", "a.exe", "x", 1, path="")])
        path = self.conn.execute("SELECT process_path FROM process_traffic").fetchone()[0]
        self.assertEqual(path, r"C:\a.exe")

    def test_top_hosts_and_sorting(self):
        self.write([
            row("2026-11-06", "10", "a.exe", "h1", 50, up=500),
            row("2026-11-06", "10", "a.exe", "h2", 40),
            row("2026-11-06", "10", "a.exe", "h3", 30),
            row("2026-11-06", "10", "a.exe", "h4", 20),
            row("2026-11-06", "10", "b.exe", "h9", 400),
        ])
        ranking, _ = storage.ranking(self.conn, "2026-11-06", sort="upload")
        self.assertEqual(ranking[0]["process_name"], "a.exe")
        self.assertEqual(ranking[0]["top_hosts"], ["h1", "h2", "h3"])
        all_days, _ = storage.ranking(self.conn, None)
        self.assertEqual(all_days[0]["process_name"], "a.exe")

    def test_search_treats_wildcards_literally(self):
        self.write([
            row("2026-11-06", "10", "100%_tool.exe", "h", 1),
            row("2026-11-06", "10", "other.exe", "h", 1),
        ])
        names = [r["process_name"] for r in storage.ranking(self.conn, None, search="%_")[0]]
        self.assertEqual(names, ["100%_tool.exe"])

    def test_daily_summary_top_process_and_routes(self):
        self.write([
            row("2026-11-05", "10", "a.exe", "h", 10),
            row("2026-11-06", "10", "a.exe", "h", 10),
            row("2026-11-06", "11", "b.exe", "h", 90),
        ], [("2026-11-06", "11", "proxy", "HK", 0, 90), ("2026-11-06", "10", "direct", "DIRECT", 0, 10)])
        summary = storage.daily_summary(self.conn)
        self.assertEqual([d["date"] for d in summary], ["2026-11-06", "2026-11-05"])
        self.assertEqual((summary[0]["top_process"], summary[0]["total"], summary[0]["proc_count"]), ("b.exe", 100, 2))
        self.assertEqual((summary[0]["proxy"], summary[0]["direct"]), (90, 10))
        self.assertEqual(storage.available_months(self.conn), ["2026-11"])

    def test_month_range_handles_december(self):
        self.assertEqual(storage._month_range("2026-12"), ("2026-12-01", "2027-01-01"))
        self.write([row("2026-12-31", "23", "a.exe", "h", 5), row("2027-01-01", "00", "a.exe", "h", 7)])
        self.assertEqual(storage.month_totals(self.conn, "2026-12")[0], 5)

    def test_prune_keeps_daily_totals_by_default(self):
        self.write([row("2026-01-01", "10", "old.exe", "h", 1), row("2026-11-06", "10", "new.exe", "h", 1)])
        removed = storage.prune(self.conn, date(2026, 11, 6), hourly_days=30, daily_days=0)
        self.assertEqual(removed, 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM process_traffic").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM daily_traffic").fetchone()[0], 2)
        storage.prune(self.conn, date(2026, 11, 6), hourly_days=30, daily_days=90)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM daily_traffic").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
