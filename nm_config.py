"""NetMonitor 共享配置：路径、端口、Clash 外部控制器自动识别。

monitor.py、mini_widget.py 与各启动脚本都从这里取值，环境变量可以覆盖任何默认值。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys

IS_WINDOWS = os.name == "nt"
APP_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(APP_DIR, "static")
MONITOR_SCRIPT = os.path.join(APP_DIR, "monitor.py")
WIDGET_SCRIPT = os.path.join(APP_DIR, "mini_widget.py")
ICON_FILE = os.path.join(APP_DIR, "netmonitor.ico")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


WEB_PORT = _env_int("WEB_PORT", 8899)
IPC_PORT = _env_int("NETMONITOR_IPC_PORT", 8897)
POLL_INTERVAL = max(_env_float("POLL_INTERVAL", 1.0), 0.2)
# 小时粒度明细保留天数（看板只用天粒度汇总，0 表示永久保留）
HOURLY_RETENTION_DAYS = max(_env_int("HOURLY_RETENTION_DAYS", 30), 0)
# 天粒度汇总保留天数（0 表示永久保留）
RETENTION_DAYS = max(_env_int("RETENTION_DAYS", 0), 0)


def _default_data_dir() -> str:
    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
        return os.path.join(base, "NetMonitor")
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "NetMonitor")


DATA_DIR = os.environ.get("NETMONITOR_DATA_DIR") or _default_data_dir()

# 旧版本的默认数据库位置：找到就继续使用，老用户的历史数据不丢。
LEGACY_DB_FILES = (
    (r"E:\tmp\traffic_history.db",) if IS_WINDOWS else ()
) + (os.path.join(APP_DIR, "traffic_history.db"),)


def resolve_db_file() -> str:
    explicit = os.environ.get("DB_FILE")
    if explicit:
        return explicit
    for legacy in LEGACY_DB_FILES:
        if os.path.isfile(legacy):
            return legacy
    return os.path.join(DATA_DIR, "traffic_history.db")


DB_FILE = resolve_db_file()
CONFIG_FILE = os.path.join(DATA_DIR, "mini_config.json")
LEGACY_CONFIG_FILE = os.path.join(APP_DIR, "mini_config.json")
MONITOR_LOG = os.path.join(DATA_DIR, "monitor.log")
WIDGET_LOG = os.path.join(DATA_DIR, "mini_widget.log")
MONITOR_PID = os.path.join(DATA_DIR, "monitor.pid")
WIDGET_PID = os.path.join(DATA_DIR, "mini_widget.pid")

THEMES = ("dark", "light", "morandi", "warm", "ocean", "cyber")


def ensure_data_dir() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    # 把旧版本放在程序目录下的悬浮窗配置迁移到数据目录（只复制一次）
    if os.path.isfile(LEGACY_CONFIG_FILE) and not os.path.exists(CONFIG_FILE):
        try:
            shutil.copy2(LEGACY_CONFIG_FILE, CONFIG_FILE)
        except OSError:
            pass


def load_json_config() -> dict:
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_json_config(data: dict) -> None:
    ensure_data_dir()
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, CONFIG_FILE)


def dashboard_url(theme: str | None = None) -> str:
    url = f"http://127.0.0.1:{WEB_PORT}/"
    if theme in THEMES:
        url += f"?theme={theme}"
    return url


# --- Clash / Mihomo 外部控制器 -------------------------------------------------

# Clash Verge Rev（新、旧包名）与 Clash Verge 的数据目录名
_VERGE_APP_DIRS = (
    "io.github.clash-verge-rev.clash-verge-rev",
    "io.github.clash-verge-rev.clash-verge",
    "io.github.clash-verge",
)
_VERGE_FILES = ("config.yaml", "clash-verge.yaml")
DEFAULT_CLASH_API_URL = "http://127.0.0.1:9097"


def _verge_config_files() -> list[str]:
    roots = []
    if IS_WINDOWS:
        if os.environ.get("APPDATA"):
            roots.append(os.environ["APPDATA"])
    elif sys.platform == "darwin":
        roots.append(os.path.expanduser("~/Library/Application Support"))
    else:
        roots.append(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"))
        roots.append(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"))
    return [os.path.join(root, app, name) for root in roots for app in _VERGE_APP_DIRS for name in _VERGE_FILES]


def _top_level_yaml_value(text: str, key: str) -> str | None:
    """读取 YAML 顶层的简单标量（零依赖，不处理多行或嵌套写法）。"""
    match = re.search(r"^" + re.escape(key) + r"[ \t]*:[ \t]*(.*?)[ \t]*$", text, re.MULTILINE)
    if not match:
        return None
    value = match.group(1)
    if value[:1] in ("'", '"'):
        quote = value[0]
        end = value.find(quote, 1)
        return value[1:end] if end > 0 else value[1:]
    return value.split(" #", 1)[0].strip()


def _controller_url(value: str) -> str:
    value = value.strip()
    if value.startswith(("http://", "https://")):
        return value.rstrip("/")
    host, _, port = value.rpartition(":")
    if host in ("", "0.0.0.0", "::", "[::]"):
        host = "127.0.0.1"
    return f"http://{host}:{port}" if port else f"http://{value}"


def resolve_clash() -> dict:
    """确定 Clash 外部控制器地址与密钥。

    优先级：环境变量 CLASH_API_URL / CLASH_SECRET > Clash Verge 配置文件 > 默认地址（无密钥）。
    返回 {"url", "secret", "source", "problem"}；problem 为 None 或需要提示用户的问题代码。
    """
    url = os.environ.get("CLASH_API_URL")
    secret = os.environ.get("CLASH_SECRET")
    if url and secret is not None:
        return {"url": url.rstrip("/"), "secret": secret, "source": "env", "problem": None}

    for path in _verge_config_files():
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except OSError:
            continue
        controller = _top_level_yaml_value(text, "external-controller")
        file_secret = _top_level_yaml_value(text, "secret")
        problem = None
        if not url and not controller:
            problem = "controller_disabled"
        return {
            "url": (url or (_controller_url(controller) if controller else DEFAULT_CLASH_API_URL)).rstrip("/"),
            "secret": secret if secret is not None else (file_secret or ""),
            "source": path,
            "problem": problem,
        }

    return {"url": (url or DEFAULT_CLASH_API_URL).rstrip("/"), "secret": secret or "",
            "source": "env" if url or secret is not None else "default", "problem": None}
