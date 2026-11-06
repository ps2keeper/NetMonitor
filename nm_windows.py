"""进程管理与 Windows 集成：PID 文件、停止服务、开机自启、桌面快捷方式、错误弹窗。

所有 PowerShell 调用都通过环境变量传递路径，避免路径中的引号、空格破坏命令。
"""
from __future__ import annotations

import ctypes
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request

import nm_config

SHORTCUT_NAMES = ("NetMonitor 悬浮窗.lnk", "NetMonitor.lnk")
AUTOSTART_NAME = "NetMonitor.lnk"


# --- 控制台与错误提示 -----------------------------------------------------------

def hide_console_window() -> None:
    if not nm_config.IS_WINDOWS:
        return
    try:
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
    except (AttributeError, OSError):
        pass


def show_error(message: str) -> None:
    """控制台被隐藏时也让用户看到致命错误。"""
    print(f"[ERROR] {message}", file=sys.stderr)
    if nm_config.IS_WINDOWS:
        try:
            ctypes.windll.user32.MessageBoxW(None, message, "NetMonitor", 0x10)  # MB_ICONERROR
        except (AttributeError, OSError):
            pass


# --- PID 文件 ------------------------------------------------------------------

def write_pid(path: str) -> None:
    nm_config.ensure_data_dir()
    with open(path, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))


def remove_pid(path: str) -> None:
    try:
        if read_pid(path) == os.getpid():
            os.remove(path)
    except OSError:
        pass


def read_pid(path: str) -> int | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _powershell(script: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, encoding="utf-8", errors="replace",
        env={**os.environ, **(env or {})}, creationflags=0x08000000 if nm_config.IS_WINDOWS else 0,
    )


def process_command_line(pid: int) -> str | None:
    if nm_config.IS_WINDOWS:
        res = _powershell("(Get-CimInstance Win32_Process -Filter \"ProcessId=$env:NM_PID\").CommandLine",
                          {"NM_PID": str(int(pid))})
        out = (res.stdout or "").strip()
        return out or None
    res = subprocess.run(["ps", "-o", "command=", "-p", str(int(pid))], capture_output=True, text=True)
    out = (res.stdout or "").strip()
    return out or None


def _is_our_process(pid: int, script: str) -> bool:
    cmd = process_command_line(pid)
    if not cmd:
        return False
    if nm_config.IS_WINDOWS:
        return script.lower() in cmd.lower()
    return script in cmd


def _terminate(pid: int) -> bool:
    try:
        os.kill(pid, signal.SIGTERM)  # Windows 上等同 TerminateProcess
        return True
    except OSError:
        return False


# --- 停止服务 ------------------------------------------------------------------

def request_monitor_shutdown(timeout: float = 2.0) -> bool:
    req = urllib.request.Request(
        f"http://127.0.0.1:{nm_config.WEB_PORT}/api/shutdown", data=b"{}", method="POST",
        headers={"X-NetMonitor": "1", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8")).get("status") == "stopping"
    except (OSError, ValueError):
        return False


def send_widget_command(command: bytes, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", nm_config.IPC_PORT), timeout=timeout) as s:
            s.sendall(command + b"\n")
        return True
    except OSError:
        return False


def _stop_by_pid_file(pid_file: str, script: str) -> list[int]:
    pid = read_pid(pid_file)
    if not pid or pid == os.getpid() or not _is_our_process(pid, script):
        return []
    return [pid] if _terminate(pid) else []


def _stop_legacy_windows(scripts) -> list[int]:
    """旧版本没有 PID 文件：只结束命令行包含本安装目录完整脚本路径的 python 进程。"""
    if not nm_config.IS_WINDOWS:
        return []
    script = r"""
$targets = $env:NM_SCRIPTS.ToLower() -split '\|'
$self = [int]$env:NM_SELF
Get-CimInstance Win32_Process |
  Where-Object { $_.Name -match '^pythonw?\.exe$' -and $_.ProcessId -ne $self -and $_.CommandLine } |
  ForEach-Object {
    $p = $_
    $cl = $p.CommandLine.ToLower()
    if ($cl.Contains('--stop')) { return }
    foreach ($t in $targets) {
      if ($t -and $cl.Contains($t)) {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
        $p.ProcessId
        break
      }
    }
  }
"""
    res = _powershell(script, {"NM_SCRIPTS": "|".join(scripts), "NM_SELF": str(os.getpid())})
    return [int(x) for x in (res.stdout or "").split() if x.strip().isdigit()]


def _pid_alive(pid: int) -> bool:
    if nm_config.IS_WINDOWS:
        return process_command_line(pid) is not None
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _wait_for_exit(pid_file: str, timeout: float) -> bool:
    """等待进程自行退出（PID 文件被删除或进程已不存在）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        pid = read_pid(pid_file)
        if pid is None or not _pid_alive(pid):
            return True
        time.sleep(0.2)
    return False


def stop_monitor() -> list[str]:
    messages = []
    if request_monitor_shutdown():
        messages.append("已通知 NetMonitor 后台服务退出")
        # 服务退出前会写完缓冲中的数据，先给它时间优雅退出
        if _wait_for_exit(nm_config.MONITOR_PID, 5.0):
            return messages
    for pid in _stop_by_pid_file(nm_config.MONITOR_PID, nm_config.MONITOR_SCRIPT):
        messages.append(f"已停止 NetMonitor 后台服务 (PID: {pid})")
    return messages


def stop_widget() -> list[str]:
    messages = []
    if send_widget_command(b"EXIT"):
        messages.append("已通知悬浮窗退出")
        if _wait_for_exit(nm_config.WIDGET_PID, 3.0):
            return messages
    for pid in _stop_by_pid_file(nm_config.WIDGET_PID, nm_config.WIDGET_SCRIPT):
        messages.append(f"已停止悬浮窗 (PID: {pid})")
    return messages


def stop_all() -> list[str]:
    messages = stop_monitor() + stop_widget()
    for pid in _stop_legacy_windows([nm_config.MONITOR_SCRIPT, nm_config.WIDGET_SCRIPT]):
        messages.append(f"已停止旧版本 NetMonitor 进程 (PID: {pid})")
    return messages


# --- 快捷方式与开机自启 ---------------------------------------------------------

def _shell_folder(csidl: int, fallback: str) -> str:
    """通过 SHGetFolderPathW 获取系统目录，兼容 OneDrive 重定向的桌面等。"""
    if nm_config.IS_WINDOWS:
        try:
            buf = ctypes.create_unicode_buffer(260)
            if ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buf) == 0 and buf.value:
                return buf.value
        except (AttributeError, OSError):
            pass
    return fallback


def desktop_dir() -> str:
    return _shell_folder(0x0010, os.path.join(os.path.expanduser("~"), "Desktop"))  # CSIDL_DESKTOPDIRECTORY


def startup_dir() -> str:
    appdata = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
    return _shell_folder(0x0007, os.path.join(appdata, r"Microsoft\Windows\Start Menu\Programs\Startup"))  # CSIDL_STARTUP


def wscript_exe() -> str:
    return os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "wscript.exe")


def create_shortcut(lnk_path: str, target: str, arguments: str, description: str) -> None:
    """用系统自带的 WScript.Shell 创建 .lnk（零依赖，不需要 pywin32）。"""
    script = (
        "$ws = New-Object -ComObject WScript.Shell; "
        "$s = $ws.CreateShortcut($env:NM_LNK); "
        "$s.TargetPath = $env:NM_TARGET; $s.Arguments = $env:NM_ARGS; "
        "$s.WorkingDirectory = $env:NM_WORKDIR; $s.Description = $env:NM_DESC; "
        "if ($env:NM_ICON) { $s.IconLocation = $env:NM_ICON }; $s.Save()"
    )
    icon = f"{nm_config.ICON_FILE},0" if os.path.exists(nm_config.ICON_FILE) else ""
    res = _powershell(script, {"NM_LNK": lnk_path, "NM_TARGET": target, "NM_ARGS": arguments,
                               "NM_WORKDIR": nm_config.APP_DIR, "NM_DESC": description, "NM_ICON": icon})
    if res.returncode != 0 or not os.path.exists(lnk_path):
        raise OSError((res.stderr or "").strip() or f"无法创建快捷方式：{lnk_path}")


def _vbs(name: str) -> str:
    return '"' + os.path.join(nm_config.APP_DIR, name) + '"'


def create_desktop_shortcuts() -> list[str]:
    desktop = desktop_dir()
    created = []
    for name, vbs, desc in (
        (SHORTCUT_NAMES[0], "start_mini_silent.vbs", "NetMonitor 桌面极简置顶悬浮窗与原生监控中心 APP"),
        (SHORTCUT_NAMES[1], "start_monitor_silent.vbs", "NetMonitor 流量监控中心仪表盘"),
    ):
        path = os.path.join(desktop, name)
        create_shortcut(path, wscript_exe(), _vbs(vbs), desc)
        created.append(path)
    return created


def autostart_path() -> str:
    return os.path.join(startup_dir(), AUTOSTART_NAME)


def enable_autostart() -> str:
    path = autostart_path()
    create_shortcut(path, wscript_exe(), _vbs("autostart_silent.vbs"), "NetMonitor 流量监控服务（开机静默启动）")
    return path


def disable_autostart() -> bool:
    path = autostart_path()
    if os.path.exists(path):
        os.remove(path)
        return True
    return False
