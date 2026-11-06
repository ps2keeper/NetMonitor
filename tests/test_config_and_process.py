from tests import support  # noqa: F401

import importlib
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import nm_config
import nm_windows


def reload_config(**env):
    with mock.patch.dict(os.environ, env):
        return importlib.reload(nm_config)


class DataLocationTest(unittest.TestCase):
    def tearDown(self):
        importlib.reload(nm_config)

    def test_explicit_db_file_wins(self):
        cfg = reload_config(DB_FILE="/tmp/explicit.db")
        self.assertEqual(cfg.DB_FILE, "/tmp/explicit.db")

    def test_default_lives_in_data_dir_not_a_fixed_drive(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("DB_FILE", None)
            cfg = importlib.reload(nm_config)
            with mock.patch.object(cfg, "LEGACY_DB_FILES", ()):
                self.assertEqual(cfg.resolve_db_file(), os.path.join(cfg.DATA_DIR, "traffic_history.db"))
            self.assertNotIn("E:\\tmp", cfg.DATA_DIR)

    def test_existing_legacy_database_keeps_being_used(self):
        legacy = os.path.join(tempfile.mkdtemp(), "traffic_history.db")
        open(legacy, "w").close()
        with mock.patch.dict(os.environ):
            os.environ.pop("DB_FILE", None)
            with mock.patch.object(nm_config, "LEGACY_DB_FILES", (legacy,)):
                self.assertEqual(nm_config.resolve_db_file(), legacy)


class ClashDetectionTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"APPDATA": self.home, "HOME": self.home,
                                                "XDG_DATA_HOME": self.home, "XDG_CONFIG_HOME": self.home})
        self.env.start()
        self.addCleanup(self.env.stop)
        for name in ("CLASH_API_URL", "CLASH_SECRET"):
            os.environ.pop(name, None)
        self.platform_root = self.home
        if sys.platform == "darwin" and not nm_config.IS_WINDOWS:
            self.platform_root = os.path.join(self.home, "Library", "Application Support")

    def write_verge(self, text, app="io.github.clash-verge-rev.clash-verge-rev"):
        folder = os.path.join(self.platform_root, app)
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "config.yaml"), "w", encoding="utf-8") as f:
            f.write(text)

    def test_reads_controller_and_secret_from_clash_verge(self):
        self.write_verge("mixed-port: 7897\nexternal-controller: 0.0.0.0:9097  # all interfaces\n"
                         "secret: 'Str0ng#Secret'\ntun:\n  enable: true\n")
        found = nm_config.resolve_clash()
        self.assertEqual(found["url"], "http://127.0.0.1:9097")
        self.assertEqual(found["secret"], "Str0ng#Secret")
        self.assertIsNone(found["problem"])

    def test_disabled_controller_is_reported(self):
        self.write_verge("external-controller: ''\nsecret: set-your-secret\n")
        found = nm_config.resolve_clash()
        self.assertEqual(found["problem"], "controller_disabled")

    def test_environment_overrides_detection(self):
        self.write_verge("external-controller: 127.0.0.1:9097\nsecret: fromfile\n")
        with mock.patch.dict(os.environ, {"CLASH_API_URL": "http://127.0.0.1:1234/", "CLASH_SECRET": "fromenv"}):
            found = nm_config.resolve_clash()
        self.assertEqual((found["url"], found["secret"], found["source"]), ("http://127.0.0.1:1234", "fromenv", "env"))

    def test_no_weak_default_secret(self):
        found = nm_config.resolve_clash()
        self.assertEqual(found["secret"], "")
        self.assertEqual(found["source"], "default")


class StopProcessTest(unittest.TestCase):
    """--stop 只结束 PID 文件指向且命令行包含本程序脚本路径的进程。"""

    def spawn(self, *extra):
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", *extra])
        self.addCleanup(lambda: (proc.kill(), proc.wait()))
        time.sleep(0.2)
        return proc

    def pid_file(self, pid):
        path = os.path.join(tempfile.mkdtemp(), "x.pid")
        with open(path, "w") as f:
            f.write(str(pid))
        return path

    def test_unrelated_process_with_similar_name_survives(self):
        other = self.spawn(os.path.join(tempfile.mkdtemp(), "gpu_monitor.py"))
        self.assertEqual(nm_windows._stop_by_pid_file(self.pid_file(other.pid), nm_config.MONITOR_SCRIPT), [])
        self.assertIsNone(other.poll())

    def test_our_process_is_stopped(self):
        ours = self.spawn(nm_config.MONITOR_SCRIPT)
        self.assertEqual(nm_windows._stop_by_pid_file(self.pid_file(ours.pid), nm_config.MONITOR_SCRIPT), [ours.pid])
        ours.wait(timeout=5)

    def test_stale_pid_file_is_harmless(self):
        self.assertEqual(nm_windows._stop_by_pid_file(self.pid_file(999999), nm_config.MONITOR_SCRIPT), [])


if __name__ == "__main__":
    unittest.main()
