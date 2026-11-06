from tests import support  # noqa: F401  (必须最先导入：隔离数据目录)

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime

import storage
from collector import UNATTRIBUTED, TrafficCollector
from tests.support import FakeMihomo, conn

NOW = datetime(2026, 11, 6, 10, 30)
DAY = "2026-11-06"


def make_collector(db_file, test=None, **kwargs):
    storage.init_db(db_file)
    collector = TrafficCollector(db_file, poll_interval=1.0, **kwargs)
    if test is not None:
        test.addCleanup(collector._reset_db)
    return collector


def totals_by_process(db_file):
    db = sqlite3.connect(db_file)
    try:
        return dict(db.execute(
            "SELECT process_name, SUM(download_bytes) FROM daily_traffic GROUP BY process_name"))
    finally:
        db.close()


class CollectorAccountingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.db")
        self.c = make_collector(self.db, self)

    def snap(self, t, conns, down_total=None):
        data = {"connections": conns}
        if down_total is not None:
            data.update(downloadTotal=down_total, uploadTotal=0)
        self.c.process_snapshot(data, t, NOW)

    def test_first_poll_only_records_baseline(self):
        self.snap(0, [conn("a", 5000)], down_total=5000)
        self.assertEqual(self.c.pending_proc, {})
        self.snap(1, [conn("a", 8000)], down_total=8000)
        self.assertTrue(self.c.flush())
        self.assertEqual(totals_by_process(self.db), {"app.exe": 3000})

    def test_short_connections_and_tail_are_recorded_as_unattributed(self):
        self.snap(0, [conn("long", 0)], down_total=0)
        # 1 秒内：长连接 +1000；一个 300 字节的短连接已经建立又关闭，从未出现在列表中
        self.snap(1, [conn("long", 1000)], down_total=1300)
        # 长连接在下一次轮询前结束，最后 200 字节只体现在内核总量里
        self.snap(2, [], down_total=1500)
        self.assertTrue(self.c.flush())
        self.assertEqual(totals_by_process(self.db), {"app.exe": 1000, UNATTRIBUTED: 500})

    def test_attribution_ahead_of_global_counter_is_not_double_counted(self):
        self.snap(0, [conn("a", 0)], down_total=0)
        self.snap(1, [conn("a", 1000)], down_total=900)   # 计数快照略有先后
        self.snap(2, [conn("a", 1000)], down_total=1000)  # 补上那 100，不应再记为未归属
        self.assertTrue(self.c.flush())
        self.assertEqual(totals_by_process(self.db), {"app.exe": 1000})

    def test_kernel_restart_resets_counters(self):
        self.snap(0, [conn("a", 0)], down_total=10_000)
        self.snap(1, [conn("a", 1000)], down_total=11_000)
        # Clash 重启：旧连接消失，计数从 0 开始
        self.snap(2, [conn("b", 400)], down_total=700)
        self.assertTrue(self.c.flush())
        self.assertEqual(totals_by_process(self.db), {"app.exe": 1400, UNATTRIBUTED: 300})

    def test_realtime_speed_uses_kernel_counter(self):
        self.snap(0, [conn("a", 0)], down_total=0)
        self.snap(2, [conn("a", 1000)], down_total=3000)
        rt = self.c.realtime_snapshot()
        self.assertEqual(rt["current_down_speed"], 1500)
        self.assertEqual(rt["process_speeds"]["app.exe"]["down"], 500)

    def test_without_kernel_counters_falls_back_to_attribution(self):
        self.snap(0, [conn("a", 0)])
        self.snap(1, [conn("a", 700)])
        self.assertTrue(self.c.flush())
        self.assertEqual(totals_by_process(self.db), {"app.exe": 700})

    def test_proxy_and_direct_split(self):
        self.snap(0, [conn("p", 0, chains=("HK-01",)), conn("d", 0)], down_total=0)
        self.snap(1, [conn("p", 600, chains=("HK-01",)), conn("d", 400)], down_total=1000)
        self.assertTrue(self.c.flush())
        db = sqlite3.connect(self.db)
        proxy, direct = db.execute("SELECT SUM(proxy_bytes), SUM(direct_bytes) FROM daily_traffic").fetchone()
        nodes = dict(db.execute("SELECT node, SUM(download_bytes) FROM route_traffic GROUP BY node"))
        db.close()
        self.assertEqual((proxy, direct), (600, 400))
        self.assertEqual(nodes, {"HK-01": 600, "DIRECT": 400})


class CollectorWriteRetryTest(unittest.TestCase):
    def test_locked_database_keeps_data_until_write_succeeds(self):
        tmp = tempfile.mkdtemp()
        db_file = os.path.join(tmp, "t.db")
        c = make_collector(db_file, self)
        c.process_snapshot({"connections": [conn("a", 0)], "downloadTotal": 0, "uploadTotal": 0}, 0, NOW)

        locker = sqlite3.connect(db_file, isolation_level=None)
        locker.execute("BEGIN EXCLUSIVE")
        try:
            storage_connect = storage.connect
            storage.connect = lambda path, timeout=5.0: storage_connect(path, timeout=0.1)
            try:
                for t in range(1, 4):
                    c.process_snapshot({"connections": [conn("a", t * 1000)], "downloadTotal": t * 1000,
                                        "uploadTotal": 0}, t, NOW)
                    self.assertFalse(c.flush())
            finally:
                storage.connect = storage_connect
            self.assertIn("locked", c.status_snapshot()["last_write_error"])
        finally:
            locker.execute("COMMIT")
            locker.close()

        self.assertTrue(c.flush())
        self.assertIsNone(c.status_snapshot()["last_write_error"])
        self.assertEqual(totals_by_process(db_file), {"app.exe": 3000})


class CollectorStatusTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.db")

    def test_wrong_secret_reports_auth_failure(self):
        fake = FakeMihomo(secret="right")
        self.addCleanup(fake.close)
        c = make_collector(self.db, self, resolve_clash=lambda: {"url": fake.url, "secret": "wrong", "source": "test", "problem": None})
        c.poll_once()
        status = c.status_snapshot()
        self.assertEqual(status["state"], "auth_failed")
        self.assertIn("401", status["message"])

    def test_unreachable_and_disabled_controller(self):
        closed_port = "http://127.0.0.1:9"
        c = make_collector(self.db, self, resolve_clash=lambda: {"url": closed_port, "secret": "", "source": "test", "problem": None})
        c.poll_once()
        self.assertEqual(c.status_snapshot()["state"], "unreachable")
        c = make_collector(self.db, self, resolve_clash=lambda: {"url": closed_port, "secret": "", "source": "x", "problem": "controller_disabled"})
        c.poll_once()
        self.assertEqual(c.status_snapshot()["state"], "controller_disabled")

    def test_successful_polls_record_traffic(self):
        fake = FakeMihomo(secret="s3cret")
        self.addCleanup(fake.close)
        c = make_collector(self.db, self, resolve_clash=lambda: {"url": fake.url, "secret": "s3cret", "source": "test", "problem": None})
        fake.connections = [conn("a", 100)]
        fake.download_total = 100
        c.poll_once()
        fake.connections = [conn("a", 2100)]
        fake.download_total = 2600
        c.poll_once()
        self.assertEqual(c.status_snapshot()["state"], "ok")
        totals = totals_by_process(self.db)
        self.assertEqual(totals["app.exe"], 2000)
        self.assertEqual(totals[UNATTRIBUTED], 500)


if __name__ == "__main__":
    unittest.main()
