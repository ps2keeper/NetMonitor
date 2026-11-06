"""
NetMonitor Mini Desktop Widget & APP (桌面网络监控悬浮窗与原生详细面板)
---------------------------------------------------------------------
特性：
1. 极简桌面悬浮窗 (Mini Widget)：
   - 常驻桌面最前端 (Always on Top)
   - 采用「累计用量在前、实时速率在后」的防抖动固定槽位布局：
     左侧固定显示当日累计总流量，右侧显示实时下行/上行速率，数值跳动绝不影响整体布局
   - 内置 6 款精选主题风格（深邃曜石、淡雅素白、莫兰迪青瓷、深海极光、赛博霓虹、暖砂羊皮），悬浮窗与主界面实时联动切换
   - 鼠标任意拖拽、自动记忆坐标并内置防越界保护
   - 单行胶囊模式与双行卡片模式一键切换

2. 双击唤起原生桌面 APP 详情窗口 (Detail Window)：
   - 原生 Windows 桌面窗口（非网页），支持 6 套深浅主题实时一键切换
   - 顶部固定等宽 5 卡片：用量卡片在前（今日总流量、今日代理、今日直连），实时速率卡片在后（实时下行、实时上行），自适应 DPI 高度绝不裁切文字
   - 选项卡默认首屏展示「今日消耗排行 TOP 50」，其次为「代理节点与分流明细」，最后为「实时占用网速进程」
   - 增量原位刷新表格行（In-place Diff Update），消除滚动跳动与闪烁
"""

import ctypes
import ctypes.wintypes as wt
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
import tkinter as tk
from tkinter import ttk

# 嵌入式/隔离模式的 Python 不会自动把脚本目录加入 sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nm_config  # noqa: E402
import nm_windows  # noqa: E402

# 确保附加到用户交互式默认桌面 (WinSta0\Default)，防止在受限桌面或后台环境中隐形
if os.name == "nt":
    try:
        user32 = ctypes.windll.user32
        hdesk = user32.OpenDesktopW("Default", 0, False, 0x01FF)
        if hdesk:
            user32.SetThreadDesktop(hdesk)
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

# 基础配置与文件路径（端口、数据目录与 monitor.py 共用 nm_config）
BASE_DIR = nm_config.APP_DIR
CONFIG_FILE = nm_config.CONFIG_FILE
ICON_FILE = nm_config.ICON_FILE
LOG_FILE = nm_config.WIDGET_LOG
LOG_MAX_BYTES = 1_000_000
API_MINI_URL = os.environ.get("NETMONITOR_MINI_API", f"http://127.0.0.1:{nm_config.WEB_PORT}/api/mini/stats")
API_FULL_URL = os.environ.get("NETMONITOR_API", f"http://127.0.0.1:{nm_config.WEB_PORT}/api/stats")
IPC_PORT = nm_config.IPC_PORT  # 本地进程间通信端口，用于已运行悬浮窗的即时唤醒
IPC_COMMANDS = (b"WAKE_DETAIL", b"EXIT")

# 6 套精选多维主题配色方案（同时驱动桌面悬浮窗、Tooltip 与桌面监控中心 APP）
THEMES = {
    "dark": {
        "name": "🌙 深邃曜石 (默认深色)",
        "short": "🌙 深邃曜石",
        "is_light": False,
        "win_bg": "#070b14",
        "header_bg": "#0d1424",
        "card_bg": "#0d1424",
        "usage_zone_bg": "#131e36",
        "border": "#1e293b",
        "border_hover": "#38bdf8",
        "sep_main": "#26334d",
        "sep_sub": "#1e293b",
        "text_main": "#f8fafc",
        "text_sub": "#94a3b8",
        "text_muted": "#64748b",
        "badge_bg": "#13233f",
        "badge_fg": "#38bdf8",
        "btn_bg": "#1e293b",
        "btn_fg": "#e2e8f0",
        "btn_primary_bg": "#1d4ed8",
        "btn_primary_fg": "#ffffff",
        "tab_bg": "#111a2e",
        "tab_fg": "#94a3b8",
        "tab_sel_bg": "#2563eb",
        "tab_sel_fg": "#ffffff",
        "tree_bg": "#0d1424",
        "tree_even_bg": "#111a2e",
        "tree_fg": "#e2e8f0",
        "tree_head_bg": "#152036",
        "tree_head_fg": "#cbd5e1",
        "c_today": "#fbbf24",
        "c_today_bar": "#f59e0b",
        "c_proxy": "#818cf8",
        "c_proxy_bar": "#6366f1",
        "c_direct": "#34d399",
        "c_direct_bar": "#10b981",
        "c_down": "#10b981",
        "c_down_bar": "#059669",
        "c_up": "#38bdf8",
        "c_up_bar": "#0284c7",
        "c_speed_val": "#f1f5f9",
    },
    "light": {
        "name": "☀️ 淡雅素白 (清爽浅色)",
        "short": "☀️ 淡雅素白",
        "is_light": True,
        "win_bg": "#f1f5f9",
        "header_bg": "#ffffff",
        "card_bg": "#ffffff",
        "usage_zone_bg": "#eff6ff",
        "border": "#cbd5e1",
        "border_hover": "#2563eb",
        "sep_main": "#cbd5e1",
        "sep_sub": "#e2e8f0",
        "text_main": "#0f172a",
        "text_sub": "#475569",
        "text_muted": "#64748b",
        "badge_bg": "#dbeafe",
        "badge_fg": "#1d4ed8",
        "btn_bg": "#e2e8f0",
        "btn_fg": "#1e293b",
        "btn_primary_bg": "#2563eb",
        "btn_primary_fg": "#ffffff",
        "tab_bg": "#e2e8f0",
        "tab_fg": "#475569",
        "tab_sel_bg": "#2563eb",
        "tab_sel_fg": "#ffffff",
        "tree_bg": "#ffffff",
        "tree_even_bg": "#f8fafc",
        "tree_fg": "#1e293b",
        "tree_head_bg": "#f1f5f9",
        "tree_head_fg": "#334155",
        "c_today": "#d97706",
        "c_today_bar": "#f59e0b",
        "c_proxy": "#4f46e5",
        "c_proxy_bar": "#6366f1",
        "c_direct": "#059669",
        "c_direct_bar": "#10b981",
        "c_down": "#059669",
        "c_down_bar": "#10b981",
        "c_up": "#0284c7",
        "c_up_bar": "#0ea5e9",
        "c_speed_val": "#0f172a",
    },
    "morandi": {
        "name": "🍵 莫兰迪青瓷 (温润护眼)",
        "short": "🍵 莫兰迪青瓷",
        "is_light": True,
        "win_bg": "#e8eee6",
        "header_bg": "#f6f9f5",
        "card_bg": "#f6f9f5",
        "usage_zone_bg": "#dce7d9",
        "border": "#bfd0ba",
        "border_hover": "#2e8b6e",
        "sep_main": "#b8cbb2",
        "sep_sub": "#d3e0cf",
        "text_main": "#1c2b21",
        "text_sub": "#486050",
        "text_muted": "#688070",
        "badge_bg": "#d5e8de",
        "badge_fg": "#1f6f54",
        "btn_bg": "#dce6d9",
        "btn_fg": "#1c2b21",
        "btn_primary_bg": "#2e8b6e",
        "btn_primary_fg": "#ffffff",
        "tab_bg": "#dce6d9",
        "tab_fg": "#486050",
        "tab_sel_bg": "#2e8b6e",
        "tab_sel_fg": "#ffffff",
        "tree_bg": "#f6f9f5",
        "tree_even_bg": "#edf3eb",
        "tree_fg": "#1c2b21",
        "tree_head_bg": "#e2ece0",
        "tree_head_fg": "#2c4233",
        "c_today": "#b45309",
        "c_today_bar": "#d97706",
        "c_proxy": "#4338ca",
        "c_proxy_bar": "#6366f1",
        "c_direct": "#047857",
        "c_direct_bar": "#10b981",
        "c_down": "#047857",
        "c_down_bar": "#10b981",
        "c_up": "#0369a1",
        "c_up_bar": "#0284c7",
        "c_speed_val": "#1c2b21",
    },
    "warm": {
        "name": "☕ 暖砂羊皮 (柔和复古)",
        "short": "☕ 暖砂羊皮",
        "is_light": True,
        "win_bg": "#efeae1",
        "header_bg": "#faf7f0",
        "card_bg": "#faf7f0",
        "usage_zone_bg": "#ece3d2",
        "border": "#d5c9b4",
        "border_hover": "#b45309",
        "sep_main": "#cfc2ab",
        "sep_sub": "#e4dac7",
        "text_main": "#292524",
        "text_sub": "#57534e",
        "text_muted": "#78716c",
        "badge_bg": "#f3e8d2",
        "badge_fg": "#9a3412",
        "btn_bg": "#e6decb",
        "btn_fg": "#292524",
        "btn_primary_bg": "#b45309",
        "btn_primary_fg": "#ffffff",
        "tab_bg": "#e6decb",
        "tab_fg": "#57534e",
        "tab_sel_bg": "#b45309",
        "tab_sel_fg": "#ffffff",
        "tree_bg": "#faf7f0",
        "tree_even_bg": "#f3eee4",
        "tree_fg": "#292524",
        "tree_head_bg": "#eae2d3",
        "tree_head_fg": "#44403c",
        "c_today": "#b45309",
        "c_today_bar": "#d97706",
        "c_proxy": "#4f46e5",
        "c_proxy_bar": "#6366f1",
        "c_direct": "#0f766e",
        "c_direct_bar": "#14b8a6",
        "c_down": "#15803d",
        "c_down_bar": "#22c55e",
        "c_up": "#0369a1",
        "c_up_bar": "#0284c7",
        "c_speed_val": "#292524",
    },
    "ocean": {
        "name": "🌊 深海极光 (科技蓝调)",
        "short": "🌊 深海极光",
        "is_light": False,
        "win_bg": "#04101c",
        "header_bg": "#081b2e",
        "card_bg": "#081b2e",
        "usage_zone_bg": "#0e2944",
        "border": "#163a5f",
        "border_hover": "#38bdf8",
        "sep_main": "#1c456e",
        "sep_sub": "#133252",
        "text_main": "#f0f9ff",
        "text_sub": "#7dd3fc",
        "text_muted": "#528ab5",
        "badge_bg": "#0f2e4d",
        "badge_fg": "#38bdf8",
        "btn_bg": "#123152",
        "btn_fg": "#e0f2fe",
        "btn_primary_bg": "#0284c7",
        "btn_primary_fg": "#ffffff",
        "tab_bg": "#0c2238",
        "tab_fg": "#7dd3fc",
        "tab_sel_bg": "#0284c7",
        "tab_sel_fg": "#ffffff",
        "tree_bg": "#081b2e",
        "tree_even_bg": "#0b2239",
        "tree_fg": "#e0f2fe",
        "tree_head_bg": "#0f2b47",
        "tree_head_fg": "#bae6fd",
        "c_today": "#fde047",
        "c_today_bar": "#eab308",
        "c_proxy": "#a5b4fc",
        "c_proxy_bar": "#818cf8",
        "c_direct": "#2dd4bf",
        "c_direct_bar": "#14b8a6",
        "c_down": "#34d399",
        "c_down_bar": "#10b981",
        "c_up": "#38bdf8",
        "c_up_bar": "#0ea5e9",
        "c_speed_val": "#f0f9ff",
    },
    "cyber": {
        "name": "💜 赛博霓虹 (暗紫流光)",
        "short": "💜 赛博霓虹",
        "is_light": False,
        "win_bg": "#0c0617",
        "header_bg": "#150c28",
        "card_bg": "#150c28",
        "usage_zone_bg": "#231440",
        "border": "#362060",
        "border_hover": "#e879f9",
        "sep_main": "#422773",
        "sep_sub": "#2a184d",
        "text_main": "#faf5ff",
        "text_sub": "#c4b5fd",
        "text_muted": "#8b6fc0",
        "badge_bg": "#2b164f",
        "badge_fg": "#e879f9",
        "btn_bg": "#291749",
        "btn_fg": "#f3e8ff",
        "btn_primary_bg": "#9333ea",
        "btn_primary_fg": "#ffffff",
        "tab_bg": "#1d1036",
        "tab_fg": "#c4b5fd",
        "tab_sel_bg": "#9333ea",
        "tab_sel_fg": "#ffffff",
        "tree_bg": "#150c28",
        "tree_even_bg": "#1b1033",
        "tree_fg": "#f3e8ff",
        "tree_head_bg": "#241542",
        "tree_head_fg": "#e9d5ff",
        "c_today": "#facc15",
        "c_today_bar": "#eab308",
        "c_proxy": "#e879f9",
        "c_proxy_bar": "#d946ef",
        "c_direct": "#2dd4bf",
        "c_direct_bar": "#14b8a6",
        "c_down": "#4ade80",
        "c_down_bar": "#22c55e",
        "c_up": "#38bdf8",
        "c_up_bar": "#0ea5e9",
        "c_speed_val": "#faf5ff",
    },
}

THEME_ORDER = ["dark", "light", "morandi", "warm", "ocean", "cyber"]


def get_theme(theme_key):
    return THEMES.get(theme_key, THEMES["dark"])


def log_event(msg):
    """记录运行时关键诊断日志到 mini_widget.log"""
    try:
        nm_config.ensure_data_dir()
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > LOG_MAX_BYTES:
            os.replace(LOG_FILE, LOG_FILE + ".1")
        t = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{t}] [PID:{os.getpid()}] {msg}\n")
    except Exception:
        pass


DEFAULT_CONFIG = {
    "x": None,
    "y": None,
    "layout": "horizontal",  # 'horizontal' 或 'card'
    "height_mode": "compact",
    "theme": "dark",
    "topmost": True,
    "locked": False,
    "alpha": 0.95
}


def load_config():
    cfg = DEFAULT_CONFIG.copy()
    try:
        nm_config.ensure_data_dir()
    except OSError:
        pass
    cfg.update(nm_config.load_json_config())
    if cfg.get("theme") not in THEMES:
        cfg["theme"] = "dark"
    return cfg


def save_config(cfg):
    try:
        nm_config.save_json_config(cfg)
    except OSError as e:
        log_event(f"保存配置失败: {e}")


def format_speed(bps):
    """格式化网络速率 (bps = 字节/秒)"""
    if bps is None or bps < 0:
        bps = 0
    if bps < 1024:
        return f"{bps:.0f} B/s"
    elif bps < 1024 * 1024:
        return f"{bps / 1024:.1f} KB/s"
    elif bps < 1024 * 1024 * 1024:
        return f"{bps / (1024 * 1024):.2f} MB/s"
    else:
        return f"{bps / (1024 * 1024 * 1024):.2f} GB/s"


def format_bytes(b):
    """格式化累计流量"""
    if b is None or b < 0:
        b = 0
    if b < 1024 * 1024:
        return f"{b / 1024:.1f} KB"
    elif b < 1024 * 1024 * 1024:
        return f"{b / (1024 * 1024):.2f} MB"
    else:
        return f"{b / (1024 * 1024 * 1024):.2f} GB"


def format_bytes_short(b):
    """紧凑格式化流量，用于卡片副标题防裁切"""
    if b is None or b < 0:
        b = 0
    if b < 1024 * 1024:
        return f"{b / 1024:.0f}K"
    elif b < 1024 * 1024 * 1024:
        return f"{b / (1024 * 1024):.1f}M"
    else:
        return f"{b / (1024 * 1024 * 1024):.2f}G"


def open_url_on_default_desktop(url):
    """
    在用户交互桌面 (WinSta0\\Default) 上可靠打开浏览器 URL。
    彻底解决从后台/沙箱拉起的进程调用 os.startfile 时，Chromium 因找不到默认桌面 Singleton 窗口而报 rc=21 退出的问题。
    """
    log_event(f"正在打开网页看板: {url}")
    if os.name == "nt":
        try:
            import winreg
            cmd_template = None
            try:
                with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice"
                ) as k:
                    prog_id, _ = winreg.QueryValueEx(k, "ProgId")
                with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"{prog_id}\shell\open\command") as k2:
                    cmd_template, _ = winreg.QueryValueEx(k2, "")
            except Exception:
                pass

            if cmd_template:
                exe_path = None
                if cmd_template.startswith('"'):
                    end_q = cmd_template.find('"', 1)
                    if end_q > 1:
                        exe_path = cmd_template[1:end_q]
                else:
                    exe_path = cmd_template.split()[0]

                if exe_path and os.path.exists(exe_path):
                    cmd_line = f'"{exe_path}" "{url}"'
                else:
                    cmd_line = f'cmd.exe /c start "" "{url}"'
            else:
                cmd_line = f'cmd.exe /c start "" "{url}"'

            class STARTUPINFOW(ctypes.Structure):
                _fields_ = [
                    ("cb", wt.DWORD), ("lpReserved", wt.LPWSTR), ("lpDesktop", wt.LPWSTR),
                    ("lpTitle", wt.LPWSTR), ("dwX", wt.DWORD), ("dwY", wt.DWORD),
                    ("dwXSize", wt.DWORD), ("dwYSize", wt.DWORD), ("dwXCountChars", wt.DWORD),
                    ("dwYCountChars", wt.DWORD), ("dwFillAttribute", wt.DWORD),
                    ("dwFlags", wt.DWORD), ("wShowWindow", wt.WORD), ("cbReserved2", wt.WORD),
                    ("lpReserved2", ctypes.POINTER(wt.BYTE)), ("hStdInput", wt.HANDLE),
                    ("hStdOutput", wt.HANDLE), ("hStdError", wt.HANDLE),
                ]

            class PROCESS_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ("hProcess", wt.HANDLE), ("hThread", wt.HANDLE),
                    ("dwProcessId", wt.DWORD), ("dwThreadId", wt.DWORD),
                ]

            si = STARTUPINFOW()
            si.cb = ctypes.sizeof(si)
            si.lpDesktop = "WinSta0\\Default"
            pi = PROCESS_INFORMATION()
            buf = ctypes.create_unicode_buffer(cmd_line)
            ok = ctypes.windll.kernel32.CreateProcessW(
                None, buf, None, None, False, 0, None, None, ctypes.byref(si), ctypes.byref(pi)
            )
            if ok:
                ctypes.windll.kernel32.CloseHandle(pi.hThread)
                ctypes.windll.kernel32.CloseHandle(pi.hProcess)
                log_event(f"CreateProcessW(WinSta0\\Default) 成功拉起浏览器: {cmd_line}")
                return
        except Exception as e:
            log_event(f"CreateProcessW 打开浏览器异常，回退 startfile: {e}")

    try:
        os.startfile(url)
    except Exception:
        webbrowser.open(url)


_last_backend_launch_time = 0


def launch_backend_if_needed():
    """如果 NetMonitor 后台采集服务未运行，自动在后台静默启动它（带防抖限频保护）"""
    global _last_backend_launch_time
    now = time.time()
    if now - _last_backend_launch_time < 10.0:
        return
    _last_backend_launch_time = now

    try:
        req = urllib.request.Request(API_MINI_URL)
        with urllib.request.urlopen(req, timeout=1.5):
            return
    except Exception:
        pass

    monitor_py = os.path.join(BASE_DIR, "monitor.py")
    if os.path.exists(monitor_py):
        try:
            python_exe = sys.executable
            pythonw_candidate = os.path.join(os.path.dirname(python_exe), "pythonw.exe")
            target_exe = pythonw_candidate if os.path.exists(pythonw_candidate) else python_exe
            flags = 0x08000000 if os.name == "nt" else 0
            subprocess.Popen([target_exe, monitor_py], cwd=BASE_DIR, creationflags=flags)
            log_event("检测到后台监控服务未响应，已自动拉起 monitor.py 采集服务")
        except Exception as e:
            log_event(f"自动拉起 monitor.py 异常: {e}")


def bring_window_to_foreground(tk_win, is_pinned=False):
    """强制将 Tkinter 窗口置于 Windows 最前台激活并获取焦点"""
    try:
        tk_win.deiconify()
        tk_win.lift()
        if os.name == "nt":
            try:
                hwnd = int(tk_win.wm_frame(), 16) if hasattr(tk_win, "wm_frame") else tk_win.winfo_id()
                ctypes.windll.user32.ShowWindow(hwnd, 9)
                ctypes.windll.user32.SetForegroundWindow(hwnd)
                ctypes.windll.user32.BringWindowToTop(hwnd)
            except Exception as e:
                log_event(f"bring_window_to_foreground win32 error: {e}")
        tk_win.attributes("-topmost", True)
        if not is_pinned:
            def restore_topmost():
                if tk_win.winfo_exists() and not getattr(tk_win, "is_pinned", False):
                    try:
                        tk_win.attributes("-topmost", False)
                    except Exception:
                        pass
            tk_win.after(350, restore_topmost)
        tk_win.focus_force()
    except Exception as e:
        log_event(f"bring_window_to_foreground error: {e}")


class Tooltip:
    """随主题自动变色的悬停提示窗"""
    def __init__(self, app):
        self.app = app
        self.root = app.root
        self.tw = None
        self.after_id = None

    def schedule(self, text_getter, event):
        self.cancel()
        x, y = event.x_root + 14, event.y_root + 16
        self.after_id = self.root.after(320, lambda: self.show(text_getter(), x, y))

    def show(self, text, x, y):
        if not text or self.tw:
            return
        th = get_theme(self.app.cfg.get("theme", "dark"))
        self.tw = tw = tk.Toplevel(self.root)
        tw.wm_overrideredirect(True)
        tw.wm_attributes("-topmost", True)
        tw.geometry(f"+{x}+{y}")

        outer = tk.Frame(tw, bg=th["border_hover"], padx=1, pady=1)
        outer.pack(fill="both", expand=True)
        frame = tk.Frame(outer, bg=th["card_bg"])
        frame.pack(fill="both", expand=True)
        lbl = tk.Label(
            frame,
            text=text,
            justify="left",
            bg=th["card_bg"],
            fg=th["text_main"],
            font=("Microsoft YaHei UI", 9),
            padx=12,
            pady=9
        )
        lbl.pack()

    def hide(self):
        self.cancel()
        if self.tw:
            try:
                self.tw.destroy()
            except Exception:
                pass
            self.tw = None

    def cancel(self):
        if self.after_id:
            try:
                self.root.after_cancel(self.after_id)
            except Exception:
                pass
            self.after_id = None


class DetailWindow:
    """原生桌面 APP 监控详情窗口（双击悬浮窗时弹出）"""
    def __init__(self, parent_app):
        self.app = parent_app
        self.root = tk.Toplevel(parent_app.root)
        self.root.title("NetMonitor - 流量监控中心")
        self.is_pinned = False

        if os.path.exists(ICON_FILE):
            try:
                self.root.iconbitmap(ICON_FILE)
            except Exception:
                pass

        # 居中显示在主屏幕黄金位置，加宽至 920px 确保 5 张卡片文字充裕舒展
        w, h = 920, 620
        self.root.minsize(820, 520)
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        cx = max(20, (sw - w) // 2)
        cy = max(20, (sh - h) // 3)
        self.root.geometry(f"{w}x{h}+{cx}+{cy}")

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.is_pinned = False

        if os.name == "nt":
            try:
                self.root.update_idletasks()
                hwnd = int(self.root.wm_frame(), 16)
                GWL_EXSTYLE = -20
                WS_EX_APPWINDOW = 0x00040000
                cur_style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, cur_style | WS_EX_APPWINDOW)
            except Exception:
                pass

        self.build_ui()
        self.apply_theme()
        self.update_content()

        bring_window_to_foreground(self.root, is_pinned=self.is_pinned)

    def build_ui(self):
        # 1. 顶部 Header
        self.header_wrap = tk.Frame(self.root, pady=0)
        self.header_wrap.pack(fill="x")
        self.header = tk.Frame(self.header_wrap, padx=20, pady=12)
        self.header.pack(fill="x", side="top")
        self.header_sep = tk.Frame(self.header_wrap, height=1)
        self.header_sep.pack(fill="x", side="bottom")

        self.title_box = tk.Frame(self.header)
        self.title_box.pack(side="left")

        self.title_lbl = tk.Label(
            self.title_box,
            text="🌐 NetMonitor 流量监控中心",
            font=("Microsoft YaHei UI", 12, "bold")
        )
        self.title_lbl.pack(side="left")

        self.badge_lbl = tk.Label(
            self.title_box,
            text="TUN 分流精准归因",
            font=("Microsoft YaHei UI", 8, "bold"),
            padx=8,
            pady=2
        )
        self.badge_lbl.pack(side="left", padx=(10, 0))

        # 右侧操作区
        self.btn_box = tk.Frame(self.header)
        self.btn_box.pack(side="right")

        self.pin_var = tk.BooleanVar(value=False)
        self.chk_pin = tk.Checkbutton(
            self.btn_box,
            text="窗口置顶",
            variable=self.pin_var,
            command=self.toggle_pin,
            font=("Microsoft YaHei UI", 9),
            cursor="hand2"
        )
        self.chk_pin.pack(side="left", padx=(0, 10))

        # 主题风格选择按钮（弹出 6 套主题菜单，同时联动悬浮窗）
        self.btn_theme = tk.Button(
            self.btn_box,
            text="🎨 主题: 🌙 深邃曜石 ▾",
            font=("Microsoft YaHei UI", 9),
            bd=0,
            padx=10,
            pady=5,
            cursor="hand2",
            command=self.show_theme_menu
        )
        self.btn_theme.pack(side="left", padx=(0, 8))

        self.btn_browser = tk.Button(
            self.btn_box,
            text="打开网页高级看板 ↗",
            font=("Microsoft YaHei UI", 9, "bold"),
            bd=0,
            padx=12,
            pady=5,
            cursor="hand2",
            command=self.open_web_dashboard
        )
        self.btn_browser.pack(side="left")

        # 2. KPI 卡片容器（严格 5 等分列宽，高度自适应 DPI 绝不裁切底部副标题文字）
        self.kpi_frame = tk.Frame(self.root, padx=16, pady=12)
        self.kpi_frame.pack(fill="x")
        for col_idx in range(5):
            self.kpi_frame.grid_columnconfigure(col_idx, weight=1, uniform="kpi_equal")

        self.card_td = self.create_kpi_card(
            self.kpi_frame, "📊 今日共用总流量", "0.00 MB", "↓ 0.0M  ·  ↑ 0.0M", "today"
        )
        self.card_td.grid(row=0, column=0, sticky="nsew", padx=(0, 5))

        self.card_proxy = self.create_kpi_card(
            self.kpi_frame, "🌐 今日走代理流量", "0.00 MB (0%)", "↓ 0.0M  ·  ↑ 0.0M", "proxy"
        )
        self.card_proxy.grid(row=0, column=1, sticky="nsew", padx=5)

        self.card_direct = self.create_kpi_card(
            self.kpi_frame, "🎯 今日走直连流量", "0.00 MB (0%)", "↓ 0.0M  ·  ↑ 0.0M", "direct"
        )
        self.card_direct.grid(row=0, column=2, sticky="nsew", padx=5)

        self.card_dl = self.create_kpi_card(
            self.kpi_frame, "⚡ 实时下行速率", "↓ 0 B/s", "代 0 B/s · 直 0 B/s", "down"
        )
        self.card_dl.grid(row=0, column=3, sticky="nsew", padx=5)

        self.card_ul = self.create_kpi_card(
            self.kpi_frame, "🚀 实时上行速率", "↑ 0 B/s", "代 0 B/s · 直 0 B/s", "up"
        )
        self.card_ul.grid(row=0, column=4, sticky="nsew", padx=(5, 0))

        self.kpi_cards = [self.card_td, self.card_proxy, self.card_direct, self.card_dl, self.card_ul]

        # 3. Notebook 选项卡
        self.style = ttk.Style(self.root)
        self.style.theme_use("clam")

        self.notebook = ttk.Notebook(self.root, style="Custom.TNotebook")
        self.notebook.pack(fill="both", expand=True, padx=16, pady=(0, 10))

        # Tab 1（首屏默认）：今日应用消耗排行 TOP 50
        self.tab_rank = tk.Frame(self.notebook, highlightthickness=1)
        self.notebook.add(self.tab_rank, text="  📊 今日应用消耗排行 (TOP 50)  ")

        self.tree_rank = ttk.Treeview(
            self.tab_rank,
            columns=("rank", "proc", "total", "proxy", "direct", "down", "up", "pct"),
            show="headings",
            style="Custom.Treeview"
        )
        self.tree_rank.heading("rank", text="排名", anchor="center")
        self.tree_rank.heading("proc", text="进程名称", anchor="w")
        self.tree_rank.heading("total", text="今日总用量", anchor="e")
        self.tree_rank.heading("proxy", text="🌐 走代理用量", anchor="e")
        self.tree_rank.heading("direct", text="🎯 走直连用量", anchor="e")
        self.tree_rank.heading("down", text="下载量", anchor="e")
        self.tree_rank.heading("up", text="上传量", anchor="e")
        self.tree_rank.heading("pct", text="占比", anchor="e")

        self.tree_rank.column("rank", width=48, stretch=False, anchor="center")
        self.tree_rank.column("proc", width=195, stretch=True, anchor="w")
        self.tree_rank.column("total", width=106, stretch=False, anchor="e")
        self.tree_rank.column("proxy", width=106, stretch=False, anchor="e")
        self.tree_rank.column("direct", width=106, stretch=False, anchor="e")
        self.tree_rank.column("down", width=98, stretch=False, anchor="e")
        self.tree_rank.column("up", width=98, stretch=False, anchor="e")
        self.tree_rank.column("pct", width=70, stretch=False, anchor="e")

        rank_scroll = ttk.Scrollbar(self.tab_rank, orient="vertical", command=self.tree_rank.yview)
        self.tree_rank.configure(yscrollcommand=rank_scroll.set)
        self.tree_rank.pack(side="left", fill="both", expand=True)
        rank_scroll.pack(side="right", fill="y")

        # Tab 2：代理节点与分流明细
        self.tab_nodes = tk.Frame(self.notebook, highlightthickness=1)
        self.notebook.add(self.tab_nodes, text="  🌐 代理节点与分流明细  ")

        self.tree_nodes = ttk.Treeview(
            self.tab_nodes,
            columns=("rank", "node", "type", "total", "down", "up", "pct"),
            show="headings",
            style="Custom.Treeview"
        )
        self.tree_nodes.heading("rank", text="#", anchor="center")
        self.tree_nodes.heading("node", text="出站通道 / 代理节点名称", anchor="w")
        self.tree_nodes.heading("type", text="分流类型", anchor="center")
        self.tree_nodes.heading("total", text="今日总用量", anchor="e")
        self.tree_nodes.heading("down", text="下行流量", anchor="e")
        self.tree_nodes.heading("up", text="上传流量", anchor="e")
        self.tree_nodes.heading("pct", text="占今日比", anchor="e")

        self.tree_nodes.column("rank", width=48, stretch=False, anchor="center")
        self.tree_nodes.column("node", width=270, stretch=True, anchor="w")
        self.tree_nodes.column("type", width=90, stretch=False, anchor="center")
        self.tree_nodes.column("total", width=115, stretch=False, anchor="e")
        self.tree_nodes.column("down", width=115, stretch=False, anchor="e")
        self.tree_nodes.column("up", width=115, stretch=False, anchor="e")
        self.tree_nodes.column("pct", width=85, stretch=False, anchor="e")

        nodes_scroll = ttk.Scrollbar(self.tab_nodes, orient="vertical", command=self.tree_nodes.yview)
        self.tree_nodes.configure(yscrollcommand=nodes_scroll.set)
        self.tree_nodes.pack(side="left", fill="both", expand=True)
        nodes_scroll.pack(side="right", fill="y")

        # Tab 3（后置）：实时活跃网速进程
        self.tab_rt = tk.Frame(self.notebook, highlightthickness=1)
        self.notebook.add(self.tab_rt, text="  ⚡ 实时活跃网速进程  ")

        self.tree_rt = ttk.Treeview(
            self.tab_rt,
            columns=("proc", "route", "down", "up"),
            show="headings",
            style="Custom.Treeview"
        )
        self.tree_rt.heading("proc", text="活跃进程名", anchor="w")
        self.tree_rt.heading("route", text="当前分流走向 / 节点", anchor="w")
        self.tree_rt.heading("down", text="实时下行速率", anchor="e")
        self.tree_rt.heading("up", text="实时上行速率", anchor="e")
        self.tree_rt.column("proc", width=240, stretch=True, anchor="w")
        self.tree_rt.column("route", width=270, stretch=True, anchor="w")
        self.tree_rt.column("down", width=145, stretch=False, anchor="e")
        self.tree_rt.column("up", width=145, stretch=False, anchor="e")

        rt_scroll = ttk.Scrollbar(self.tab_rt, orient="vertical", command=self.tree_rt.yview)
        self.tree_rt.configure(yscrollcommand=rt_scroll.set)
        self.tree_rt.pack(side="left", fill="both", expand=True)
        rt_scroll.pack(side="right", fill="y")

        # 4. 底部状态栏
        self.footer = tk.Frame(self.root, padx=18, pady=8, highlightthickness=1)
        self.footer.pack(fill="x")

        self.lbl_status = tk.Label(
            self.footer,
            text="🟢 服务连接正常 · 累计用量与分流数据实时同步",
            font=("Microsoft YaHei UI", 8)
        )
        self.lbl_status.pack(side="left")

        self.btn_close = tk.Button(
            self.footer,
            text="关闭窗口",
            font=("Microsoft YaHei UI", 9),
            bd=0,
            padx=14,
            pady=3,
            cursor="hand2",
            command=self.on_close
        )
        self.btn_close.pack(side="right")

    def create_kpi_card(self, parent, title, initial_val, initial_sub, role):
        """
        创建自适应高度 + 严格锁定列宽的 KPI 卡片：
        - 内部 Label 设置 width=1 防止文字长度撑宽父容器，配合 grid uniform="kpi_equal" 保证 5 列严格等宽；
        - 不锁定固定像素 height，由字体与内边距自然撑开高度，确保在 100%~200% 任意系统缩放下底部文字完整清晰显示！
        """
        card = tk.Frame(parent, highlightthickness=1)
        card.role = role

        top_bar = tk.Frame(card, height=3)
        top_bar.pack(fill="x", side="top")
        card.top_bar = top_bar

        inner = tk.Frame(card, padx=12, pady=10)
        inner.pack(fill="both", expand=True)
        card.inner = inner

        lbl_title = tk.Label(
            inner, text=title, width=1, font=("Microsoft YaHei UI", 8, "bold"), anchor="w"
        )
        lbl_title.pack(fill="x", anchor="w")

        lbl_val = tk.Label(
            inner, text=initial_val, width=1, font=("Consolas", 12, "bold"), anchor="w"
        )
        lbl_val.pack(fill="x", anchor="w", pady=(5, 4))

        lbl_sub = tk.Label(
            inner, text=initial_sub, width=1, font=("Microsoft YaHei UI", 8), anchor="w"
        )
        lbl_sub.pack(fill="x", anchor="w")

        card.lbl_title = lbl_title
        card.lbl_val = lbl_val
        card.lbl_sub = lbl_sub
        return card

    def show_theme_menu(self):
        th = get_theme(self.app.cfg.get("theme", "dark"))
        menu = tk.Menu(
            self.root, tearoff=0,
            bg=th["card_bg"], fg=th["text_main"],
            activebackground=th["tab_sel_bg"], activeforeground=th["tab_sel_fg"],
            font=("Microsoft YaHei UI", 9)
        )
        cur_t = self.app.cfg.get("theme", "dark")
        for t_key in THEME_ORDER:
            t_info = THEMES[t_key]
            prefix = "✓ " if t_key == cur_t else "   "
            menu.add_command(
                label=f"{prefix}{t_info['name']}",
                command=lambda k=t_key: self.app.set_theme(k)
            )
        x = self.btn_theme.winfo_rootx()
        y = self.btn_theme.winfo_rooty() + self.btn_theme.winfo_height() + 2
        menu.tk_popup(x, y)

    def apply_theme(self):
        """将当前所选主题实时应用到监控中心窗口所有组件"""
        if not self.root.winfo_exists():
            return
        t_key = self.app.cfg.get("theme", "dark")
        th = get_theme(t_key)

        self.root.configure(bg=th["win_bg"])
        self.header_wrap.configure(bg=th["border"])
        self.header.configure(bg=th["header_bg"])
        self.header_sep.configure(bg=th["border"])
        self.title_box.configure(bg=th["header_bg"])
        self.title_lbl.configure(bg=th["header_bg"], fg=th["text_main"])
        self.badge_lbl.configure(bg=th["badge_bg"], fg=th["badge_fg"])
        self.btn_box.configure(bg=th["header_bg"])

        self.chk_pin.configure(
            bg=th["header_bg"], fg=th["text_sub"],
            selectcolor=th["card_bg"],
            activebackground=th["header_bg"],
            activeforeground=th["text_main"]
        )
        self.btn_theme.configure(
            text=f"🎨 风格: {th['short']} ▾",
            bg=th["btn_bg"], fg=th["btn_fg"],
            activebackground=th["border"], activeforeground=th["text_main"]
        )
        self.btn_browser.configure(
            bg=th["btn_primary_bg"], fg=th["btn_primary_fg"],
            activebackground=th["tab_sel_bg"], activeforeground="#ffffff"
        )

        self.kpi_frame.configure(bg=th["win_bg"])
        role_color_map = {
            "today": (th["c_today"], th["c_today_bar"]),
            "proxy": (th["c_proxy"], th["c_proxy_bar"]),
            "direct": (th["c_direct"], th["c_direct_bar"]),
            "down": (th["c_down"], th["c_down_bar"]),
            "up": (th["c_up"], th["c_up_bar"]),
        }
        for card in self.kpi_cards:
            val_c, bar_c = role_color_map.get(card.role, (th["text_main"], th["border_hover"]))
            card.configure(bg=th["card_bg"], highlightbackground=th["border"])
            card.top_bar.configure(bg=bar_c)
            card.inner.configure(bg=th["card_bg"])
            card.lbl_title.configure(bg=th["card_bg"], fg=th["text_sub"])
            card.lbl_val.configure(bg=th["card_bg"], fg=val_c)
            card.lbl_sub.configure(bg=th["card_bg"], fg=th["text_muted"])

        # Notebook & Treeview 样式动态刷新
        self.style.configure("Custom.TNotebook", background=th["win_bg"], borderwidth=0, tabmargins=[0, 0, 0, 0])
        self.style.configure(
            "Custom.TNotebook.Tab",
            background=th["tab_bg"],
            foreground=th["tab_fg"],
            padding=[18, 8],
            borderwidth=0,
            font=("Microsoft YaHei UI", 9, "bold")
        )
        self.style.map(
            "Custom.TNotebook.Tab",
            background=[("selected", th["tab_sel_bg"]), ("active", th["border"])],
            foreground=[("selected", th["tab_sel_fg"]), ("active", th["text_main"])]
        )

        self.style.configure(
            "Custom.Treeview",
            background=th["tree_bg"],
            foreground=th["tree_fg"],
            fieldbackground=th["tree_bg"],
            borderwidth=0,
            rowheight=28,
            font=("Consolas", 9)
        )
        self.style.configure(
            "Custom.Treeview.Heading",
            background=th["tree_head_bg"],
            foreground=th["tree_head_fg"],
            font=("Microsoft YaHei UI", 9, "bold"),
            padding=[6, 6],
            relief="flat"
        )
        self.style.map(
            "Custom.Treeview",
            background=[("selected", th["tab_sel_bg"])],
            foreground=[("selected", th["tab_sel_fg"])]
        )
        self.style.map(
            "Custom.Treeview.Heading",
            background=[("active", th["border"])]
        )

        for tab in (self.tab_rank, self.tab_nodes, self.tab_rt):
            tab.configure(bg=th["tree_bg"], highlightbackground=th["border"])

        for tree in (self.tree_rank, self.tree_nodes, self.tree_rt):
            tree.tag_configure("odd", background=th["tree_bg"], foreground=th["tree_fg"])
            tree.tag_configure("even", background=th["tree_even_bg"], foreground=th["tree_fg"])

        self.footer.configure(bg=th["header_bg"], highlightbackground=th["border"])
        self.lbl_status.configure(bg=th["header_bg"], fg=th["c_down"])
        self.btn_close.configure(
            bg=th["btn_bg"], fg=th["btn_fg"],
            activebackground=th["border"], activeforeground=th["text_main"]
        )

    def toggle_pin(self):
        self.is_pinned = self.pin_var.get()
        self.root.is_pinned = self.is_pinned
        self.root.attributes("-topmost", self.is_pinned)

    @staticmethod
    def _sync_treeview(tree, rows):
        """原位增量更新 Treeview 行数据，消除全部删除重建导致的闪烁与滚动条复位"""
        existing = tree.get_children()
        n_exist = len(existing)
        n_new = len(rows)

        for i in range(min(n_exist, n_new)):
            tag = "even" if i % 2 == 1 else "odd"
            tree.item(existing[i], values=rows[i], tags=(tag,))

        if n_new > n_exist:
            for i in range(n_exist, n_new):
                tag = "even" if i % 2 == 1 else "odd"
                tree.insert("", "end", values=rows[i], tags=(tag,))
        elif n_exist > n_new:
            for iid in existing[n_new:]:
                tree.delete(iid)

    def open_web_dashboard(self):
        # 若当前窗口未勾选置顶，先解除临时置顶以免遮挡刚打开的浏览器窗口
        if not self.is_pinned:
            try:
                self.root.attributes("-topmost", False)
            except Exception:
                pass
        t_key = self.app.cfg.get("theme", "dark")
        open_url_on_default_desktop(nm_config.dashboard_url(t_key))

    def update_content(self):
        if not self.root.winfo_exists():
            return

        with self.app.data_lock:
            d = self.app.latest_data.copy()

        th = get_theme(self.app.cfg.get("theme", "dark"))
        rtd = d.get("route_today") or {}
        p_info = rtd.get("proxy") or {}
        d_info = rtd.get("direct") or {}
        p_tot = p_info.get("total", 0)
        d_tot = d_info.get("total", 0)
        rt_sum = (p_tot + d_tot) or 1
        p_pct = p_tot / rt_sum * 100 if (p_tot + d_tot) > 0 else 0.0
        d_pct = d_tot / rt_sum * 100 if (p_tot + d_tot) > 0 else 0.0

        if d.get("connected"):
            # 前 3 张卡片：今日累计用量（总计、代理、直连），底部文字简洁清晰绝不超长裁切
            self.card_td.lbl_val.config(text=format_bytes(d["today_total"]))
            self.card_td.lbl_sub.config(
                text=f"↓ {format_bytes(d['today_dl'])}  ·  ↑ {format_bytes(d['today_ul'])}"
            )

            self.card_proxy.lbl_val.config(text=f"{format_bytes(p_tot)} ({p_pct:.1f}%)")
            self.card_proxy.lbl_sub.config(
                text=f"↓ {format_bytes_short(p_info.get('download', 0))}  ·  ↑ {format_bytes_short(p_info.get('upload', 0))}"
            )

            self.card_direct.lbl_val.config(text=f"{format_bytes(d_tot)} ({d_pct:.1f}%)")
            self.card_direct.lbl_sub.config(
                text=f"↓ {format_bytes_short(d_info.get('download', 0))}  ·  ↑ {format_bytes_short(d_info.get('upload', 0))}"
            )

            # 后 2 张卡片：实时下行与实时上行速率
            self.card_dl.lbl_val.config(text=f"↓ {format_speed(d['down_speed'])}")
            self.card_dl.lbl_sub.config(
                text=f"代 {format_speed(d.get('proxy_down_speed', 0))} · 直 {format_speed(d.get('direct_down_speed', 0))}"
            )

            self.card_ul.lbl_val.config(text=f"↑ {format_speed(d['up_speed'])}")
            self.card_ul.lbl_sub.config(
                text=f"代 {format_speed(d.get('proxy_up_speed', 0))} · 直 {format_speed(d.get('direct_up_speed', 0))}"
            )

            p_spd = format_speed(d.get("proxy_speed", 0))
            d_spd = format_speed(d.get("direct_speed", 0))
            collector = d.get("collector") or {}
            if collector.get("state", "ok") != "ok":
                self.lbl_status.config(text=f"🟠 {collector.get('message', '采集异常')}", fg=th["c_today"])
            elif collector.get("last_write_error"):
                self.lbl_status.config(
                    text=f"🟠 数据库写入失败，{collector.get('pending_rows', 0)} 组数据待重试：{collector['last_write_error']}",
                    fg=th["c_today"])
            else:
                self.lbl_status.config(
                    text=f"🟢 服务连接正常  │  今日总计: {format_bytes(d['today_total'])} (代理 {p_pct:.1f}% / 直连 {d_pct:.1f}%)  │  实时代理: {p_spd} · 实时直连: {d_spd}",
                    fg=th["c_down"]
                )
        else:
            self.card_td.lbl_val.config(text="连接中...")
            self.card_proxy.lbl_val.config(text="-")
            self.card_direct.lbl_val.config(text="-")
            self.card_dl.lbl_val.config(text="↓ 0 B/s")
            self.card_ul.lbl_val.config(text="↑ 0 B/s")
            self.lbl_status.config(text="🟡 正在连接后台监控服务...", fg=th["c_today"])

        # 1. 更新今日排行树
        ranking = d.get("ranking", [])
        rank_rows = []
        for r in ranking[:50]:
            rank_rows.append((
                r.get("rank", "-"),
                r.get("process_name", "-"),
                format_bytes(r.get("total", 0)),
                format_bytes(r.get("proxy", 0)),
                format_bytes(r.get("direct", 0)),
                format_bytes(r.get("download", 0)),
                format_bytes(r.get("upload", 0)),
                f"{r.get('pct', 0):.1f}%" if isinstance(r.get("pct"), (int, float)) else f"{r.get('pct', 0)}%"
            ))
        self._sync_treeview(self.tree_rank, rank_rows)

        # 2. 更新代理节点与分流明细树
        nodes_list = rtd.get("nodes") or []
        all_routes = []
        if d_tot > 0:
            all_routes.append({
                "node": "🎯 DIRECT (本地直连出站)",
                "type": "直连",
                "total": d_tot,
                "download": d_info.get("download", 0),
                "upload": d_info.get("upload", 0)
            })
        for n_item in nodes_list:
            all_routes.append({
                "node": f"🌐 {n_item.get('node', '未知节点')}",
                "type": "代理",
                "total": n_item.get("total", 0),
                "download": n_item.get("download", 0),
                "upload": n_item.get("upload", 0)
            })
        all_routes.sort(key=lambda x: x["total"], reverse=True)
        node_rows = []
        for idx, item in enumerate(all_routes, 1):
            pct_val = (item["total"] / rt_sum * 100) if (p_tot + d_tot) > 0 else 0
            node_rows.append((
                idx,
                item["node"],
                item["type"],
                format_bytes(item["total"]),
                format_bytes(item["download"]),
                format_bytes(item["upload"]),
                f"{pct_val:.1f}%"
            ))
        self._sync_treeview(self.tree_nodes, node_rows)

        # 3. 更新实时进程树
        proc_speeds = d.get("process_speeds", {})
        active_list = sorted(proc_speeds.items(), key=lambda x: (x[1].get("down", 0) + x[1].get("up", 0)), reverse=True)
        rt_rows = []
        if active_list:
            for proc, s in active_list:
                down_str = format_speed(s.get("down", 0))
                up_str = format_speed(s.get("up", 0))
                rt_type = s.get("route", "direct")
                node_str = s.get("node", "DIRECT")
                if rt_type == "proxy":
                    route_disp = f"🌐 代理 · {node_str}"
                elif rt_type == "mixed":
                    route_disp = f"🔀 混合 · {node_str}"
                else:
                    route_disp = "🎯 直连 (DIRECT)"
                rt_rows.append((proc, route_disp, down_str, up_str))
        else:
            rt_rows.append(("当前暂无高频传输进程", "-", "0 B/s", "0 B/s"))
        self._sync_treeview(self.tree_rt, rt_rows)

        self.root.after(1000, self.update_content)

    def on_close(self):
        try:
            self.root.destroy()
        except Exception:
            pass
        self.app.detail_window = None


class MiniWidgetApp:
    def __init__(self):
        self.cfg = load_config()
        try:
            self._cfg_mtime = os.path.getmtime(CONFIG_FILE) if os.path.exists(CONFIG_FILE) else 0
        except Exception:
            self._cfg_mtime = 0
        self.root = tk.Tk()
        self.root.title("NetMonitor Mini")

        self.root.overrideredirect(True)
        self.apply_topmost()
        self.apply_alpha()

        if os.path.exists(ICON_FILE):
            try:
                self.root.iconbitmap(ICON_FILE)
            except Exception:
                pass

        self.detail_window = None

        self.drag_offset_x = 0
        self.drag_offset_y = 0
        self.press_x = 0
        self.press_y = 0
        self.press_win_x = 0
        self.press_win_y = 0
        self.has_dragged = False
        self.is_dragging = False
        self.last_click_time = 0
        self.last_trigger_time = 0

        self.latest_data = {
            "down_speed": 0.0,
            "up_speed": 0.0,
            "proxy_down_speed": 0.0,
            "proxy_up_speed": 0.0,
            "direct_down_speed": 0.0,
            "direct_up_speed": 0.0,
            "proxy_speed": 0.0,
            "direct_speed": 0.0,
            "today_total": 0,
            "today_dl": 0,
            "today_ul": 0,
            "route_today": None,
            "process_speeds": {},
            "ranking": [],
            "collector": {},
            "connected": False
        }
        self.data_lock = threading.Lock()
        self.running = True
        self.consecutive_fails = 0

        self.tooltip = Tooltip(self)

        th = get_theme(self.cfg.get("theme", "dark"))
        self.canvas = tk.Canvas(
            self.root,
            bg=th["card_bg"],
            highlightthickness=1,
            highlightbackground=th["border"],
            bd=0
        )
        self.canvas.pack(fill="both", expand=True)

        self.init_ui_elements()
        self.apply_theme_to_widget()
        self.apply_layout(self.cfg.get("layout", "horizontal"), initial=True)

        self.bind_events()
        self.start_ipc_server()
        try:
            nm_windows.write_pid(nm_config.WIDGET_PID)
        except OSError:
            pass

        self.fetch_thread = threading.Thread(target=self.background_fetch_loop, daemon=True)
        self.fetch_thread.start()

        self.update_gui_loop()
        self.root.after(300, self.open_or_focus_detail_window)

    def start_ipc_server(self):
        """本地 IPC 监听：收到 WAKE_DETAIL 命令时立即弹出并聚焦详情窗口"""
        def ipc_loop():
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            # Windows 的 SO_REUSEADDR 允许多个进程同时绑定，必须用独占绑定才能保证单实例
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                srv.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                srv.bind(("127.0.0.1", IPC_PORT))
                srv.listen(5)
                self.ipc_srv = srv
            except Exception:
                return

            while self.running:
                try:
                    conn, _ = srv.accept()
                    conn.settimeout(1.0)
                    try:
                        data = conn.recv(64)
                    except OSError:
                        data = b""
                    finally:
                        conn.close()
                    # 只接受精确的单行命令：浏览器发来的 HTTP 请求（首行是 GET/POST ...）一律忽略
                    command = data.split(b"\n", 1)[0].strip()
                    if command not in IPC_COMMANDS:
                        continue
                    if command == b"EXIT":
                        log_event("IPC 接收到 EXIT 指令，悬浮窗退出")
                        self.root.after(0, self.close_widget)
                        continue
                    if command == b"WAKE_DETAIL":
                        log_event("IPC 接收到 WAKE_DETAIL 唤醒指令，正在置顶悬浮窗并调出详情窗口")
                        def wake_all():
                            try:
                                self.root.deiconify()
                                self.apply_topmost()
                                self.root.lift()
                            except Exception:
                                pass
                            self.open_or_focus_detail_window()
                        self.root.after(0, wake_all)
                except Exception:
                    break

        t = threading.Thread(target=ipc_loop, daemon=True)
        t.start()

    def apply_topmost(self):
        is_top = self.cfg.get("topmost", True)
        self.root.attributes("-topmost", is_top)

    def apply_alpha(self):
        alpha = float(self.cfg.get("alpha", 0.95))
        try:
            self.root.attributes("-alpha", alpha)
        except Exception:
            pass

    def init_ui_elements(self):
        """初始化 Canvas 上的视觉层与固定槽位文本对象（用量在前，实时数据在后）"""
        c = self.canvas
        self.bg_usage_zone = c.create_rectangle(0, 0, 0, 0, outline="")
        self.top_glow_usage = c.create_line(0, 0, 0, 0, width=1)
        self.top_glow_speed = c.create_line(0, 0, 0, 0, width=1)

        self.item_dot = c.create_oval(0, 0, 0, 0, outline="")
        self.item_td_lbl = c.create_text(0, 0, text="今日", font=("Microsoft YaHei UI", 8, "bold"), anchor="w")
        self.item_td_val = c.create_text(0, 0, text="0.00 MB", font=("Consolas", 9, "bold"), anchor="e")

        self.item_sep1 = c.create_line(0, 0, 0, 0, width=1)

        self.item_dl_icon = c.create_text(0, 0, text="↓", font=("Segoe UI", 9, "bold"), anchor="w")
        self.item_dl_val = c.create_text(0, 0, text="0 B/s", font=("Consolas", 9, "bold"), anchor="e")

        self.item_sep2 = c.create_line(0, 0, 0, 0, width=1)

        self.item_ul_icon = c.create_text(0, 0, text="↑", font=("Segoe UI", 9, "bold"), anchor="w")
        self.item_ul_val = c.create_text(0, 0, text="0 B/s", font=("Consolas", 9, "bold"), anchor="e")

    def apply_theme_to_widget(self):
        """将当前主题应用到桌面悬浮窗 Canvas"""
        th = get_theme(self.cfg.get("theme", "dark"))
        c = self.canvas
        c.config(bg=th["card_bg"], highlightbackground=th["border"])
        c.itemconfigure(self.bg_usage_zone, fill=th["usage_zone_bg"])
        c.itemconfigure(self.top_glow_usage, fill=th["c_today_bar"])
        c.itemconfigure(self.top_glow_speed, fill=th["c_up_bar"])
        c.itemconfigure(self.item_td_lbl, fill=th["text_sub"])
        c.itemconfigure(self.item_td_val, fill=th["c_today"])
        c.itemconfigure(self.item_sep1, fill=th["sep_main"])
        c.itemconfigure(self.item_sep2, fill=th["sep_sub"])
        c.itemconfigure(self.item_dl_icon, fill=th["c_down"])
        c.itemconfigure(self.item_dl_val, fill=th["c_speed_val"])
        c.itemconfigure(self.item_ul_icon, fill=th["c_up"])
        c.itemconfigure(self.item_ul_val, fill=th["c_speed_val"])

    def set_theme(self, theme_key):
        """切换主题风格（同时更新悬浮窗、原生监控中心面板并持久化保存）"""
        if theme_key not in THEMES:
            return
        self.cfg["theme"] = theme_key
        save_config(self.cfg)
        try:
            self._cfg_mtime = os.path.getmtime(CONFIG_FILE)
        except Exception:
            pass
        self.apply_theme_to_widget()
        if self.detail_window and self.detail_window.root.winfo_exists():
            self.detail_window.apply_theme()

    def apply_layout(self, layout_name, initial=False):
        self.cfg["layout"] = layout_name
        height_mode = self.cfg.get("height_mode", "compact")
        c = self.canvas

        if layout_name == "card":
            if height_mode == "standard":
                w, h = 236, 48
                y1, y2 = 13, 36
                sep_y = 24
            else:
                w, h = 232, 44
                y1, y2 = 11, 33
                sep_y = 22

            c.coords(self.bg_usage_zone, 0, 0, w, sep_y)
            c.coords(self.top_glow_usage, 0, 0, w, 0)
            c.coords(self.top_glow_speed, 0, 0, 0, 0)

            c.coords(self.item_dot, 10, y1 - 3, 16, y1 + 3)
            c.coords(self.item_td_lbl, 22, y1)
            c.itemconfigure(self.item_td_lbl, text="今日用量")
            c.coords(self.item_td_val, w - 10, y1)

            c.coords(self.item_sep1, 0, sep_y, w, sep_y)

            mid_x = w // 2
            c.coords(self.item_dl_icon, 10, y2)
            c.coords(self.item_dl_val, mid_x - 8, y2)
            c.coords(self.item_sep2, mid_x, sep_y + 4, mid_x, h - 4)
            c.coords(self.item_ul_icon, mid_x + 8, y2)
            c.coords(self.item_ul_val, w - 10, y2)
        else:
            layout_name = "horizontal"
            self.cfg["layout"] = "horizontal"
            if height_mode == "standard":
                w, h = 348, 28
                y_mid = 14
            else:
                w, h = 342, 24
                y_mid = 12

            s1_end = 124
            s2_end = 233

            c.coords(self.bg_usage_zone, 0, 0, s1_end, h)
            c.coords(self.top_glow_usage, 0, 0, s1_end, 0)
            c.coords(self.top_glow_speed, s1_end + 1, 0, w, 0)

            c.coords(self.item_dot, 8, y_mid - 3, 14, y_mid + 3)
            c.coords(self.item_td_lbl, 19, y_mid)
            c.itemconfigure(self.item_td_lbl, text="今日")
            c.coords(self.item_td_val, s1_end - 8, y_mid)

            c.coords(self.item_sep1, s1_end, 0, s1_end, h)

            c.coords(self.item_dl_icon, s1_end + 8, y_mid)
            c.coords(self.item_dl_val, s2_end - 8, y_mid)

            c.coords(self.item_sep2, s2_end, 4, s2_end, h - 4)

            c.coords(self.item_ul_icon, s2_end + 8, y_mid)
            c.coords(self.item_ul_val, w - 8, y_mid)

        c.config(width=w, height=h)
        self.current_w = w
        self.current_h = h

        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()

        cur_x = self.cfg.get("x")
        cur_y = self.cfg.get("y")

        if cur_x is None:
            cur_x = screen_w - w - 80
            self.cfg["x"] = cur_x
        if cur_y is None:
            cur_y = 80
            self.cfg["y"] = cur_y

        cur_x = max(0, min(screen_w - w, cur_x))
        cur_y = max(0, min(screen_h - h, cur_y))

        self.root.geometry(f"{w}x{h}+{cur_x}+{cur_y}")
        save_config(self.cfg)

    def bind_events(self):
        c = self.canvas
        c.bind("<Button-1>", self.on_mouse_down)
        c.bind("<B1-Motion>", self.on_mouse_drag)
        c.bind("<ButtonRelease-1>", self.on_mouse_up)
        c.bind("<Double-Button-1>", self.on_double_click_handler)
        c.bind("<Double-1>", self.on_double_click_handler)
        c.bind("<Button-3>", self.show_context_menu)

        self.root.bind("<Double-Button-1>", self.on_double_click_handler)
        self.root.bind("<Double-1>", self.on_double_click_handler)

        c.bind("<Enter>", self.on_mouse_enter)
        c.bind("<Leave>", self.on_mouse_leave)

    def on_mouse_down(self, event):
        self.press_x = event.x_root
        self.press_y = event.y_root
        self.press_win_x = self.root.winfo_x()
        self.press_win_y = self.root.winfo_y()
        self.has_dragged = False
        self.is_dragging = True

    def on_mouse_drag(self, event):
        if self.cfg.get("locked", False) or not self.is_dragging:
            return

        dx = event.x_root - self.press_x
        dy = event.y_root - self.press_y

        if not self.has_dragged:
            if abs(dx) + abs(dy) >= 6:
                self.has_dragged = True
                self.tooltip.hide()
            else:
                return

        new_x = self.press_win_x + dx
        new_y = self.press_win_y + dy

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        w = getattr(self, "current_w", self.root.winfo_width())
        h = getattr(self, "current_h", self.root.winfo_height())
        new_x = max(0, min(sw - w, new_x))
        new_y = max(0, min(sh - h, new_y))

        self.root.geometry(f"+{new_x}+{new_y}")

    def on_mouse_up(self, event):
        self.is_dragging = False
        if self.has_dragged:
            self.cfg["x"] = self.root.winfo_x()
            self.cfg["y"] = self.root.winfo_y()
            save_config(self.cfg)
        else:
            now = time.time()
            diff = now - self.last_click_time
            if 0.05 < diff < 0.42:
                self.last_click_time = 0
                log_event(f"on_mouse_up: 手动双击判定触发 (间隔 {diff:.3f}s)")
                self.on_double_click_handler()
                return
            self.last_click_time = now

    def on_double_click_handler(self, event=None):
        now = time.time()
        if now - self.last_trigger_time < 0.35:
            return
        self.last_trigger_time = now
        log_event("on_double_click_handler 触发")

        th = get_theme(self.cfg.get("theme", "dark"))
        try:
            self.canvas.config(highlightbackground=th["border_hover"])
            self.root.after(250, lambda: self.canvas.config(highlightbackground=th["border"]) if self.root.winfo_exists() else None)
        except Exception:
            pass

        self.open_or_focus_detail_window()

    def open_or_focus_detail_window(self):
        log_event("open_or_focus_detail_window 触发")
        try:
            if self.detail_window and self.detail_window.root.winfo_exists():
                log_event("DetailWindow 窗口已存在，置顶激活至前台")
                bring_window_to_foreground(self.detail_window.root, getattr(self.detail_window, "is_pinned", False))
            else:
                log_event("创建全新 DetailWindow 窗口")
                self.detail_window = DetailWindow(self)
        except Exception as e:
            import traceback
            log_event(f"open_or_focus_detail_window 异常: {e}\n{traceback.format_exc()}")

    def on_mouse_enter(self, event):
        th = get_theme(self.cfg.get("theme", "dark"))
        if not self.cfg.get("locked", False):
            self.canvas.config(cursor="fleur", highlightbackground=th["border_hover"])
        else:
            self.canvas.config(cursor="arrow", highlightbackground=th["border_hover"])

        def get_tooltip_text():
            with self.data_lock:
                d = self.latest_data.copy()
            if not d["connected"]:
                return "NetMonitor 服务连接中...\n(后台服务正在启动，稍候即连)"
            collector = d.get("collector") or {}
            if collector.get("state", "ok") != "ok":
                return f"⚠ {collector.get('message', '采集异常')}"

            route_lines = ""
            rtd = d.get("route_today")
            if rtd:
                p_tot = rtd.get("proxy", {}).get("total", 0)
                d_tot = rtd.get("direct", {}).get("total", 0)
                s = (p_tot + d_tot) or 1
                route_lines = (
                    f" 🌐 今日走代理 :  {format_bytes(p_tot)}  ({p_tot / s * 100:.1f}%)\n"
                    f" 🎯 今日走直连 :  {format_bytes(d_tot)}  ({d_tot / s * 100:.1f}%)\n"
                )
            return (
                f"【NetMonitor 流量监控小组件 · {th['short']}】\n"
                f"──────────────────────────\n"
                f" 📊 今日总用量 :  {format_bytes(d['today_total'])}\n"
                f"    ├─ 今日下载 :  {format_bytes(d['today_dl'])}\n"
                f"    └─ 今日上传 :  {format_bytes(d['today_ul'])}\n"
                f"{route_lines}"
                f"──────────────────────────\n"
                f" ⚡ 实时下行   :  {format_speed(d['down_speed'])}\n"
                f" 🚀 实时上行   :  {format_speed(d['up_speed'])}\n"
                f" 🔀 代理 / 直连:  {format_speed(d.get('proxy_speed', 0))} / {format_speed(d.get('direct_speed', 0))}\n"
                f"──────────────────────────\n"
                f"💡 双击：打开桌面监控中心 APP\n"
                f"💡 右键：切换 6 款主题风格与设置"
            )
        self.tooltip.schedule(get_tooltip_text, event)

    def on_mouse_leave(self, event):
        th = get_theme(self.cfg.get("theme", "dark"))
        self.canvas.config(cursor="", highlightbackground=th["border"])
        self.tooltip.hide()

    def show_context_menu(self, event):
        self.tooltip.hide()
        th = get_theme(self.cfg.get("theme", "dark"))
        menu = tk.Menu(
            self.root, tearoff=0,
            bg=th["card_bg"], fg=th["text_main"],
            activebackground=th["tab_sel_bg"], activeforeground=th["tab_sel_fg"],
            font=("Microsoft YaHei UI", 9)
        )

        menu.add_command(label="📊 打开桌面详细窗口 (APP)", command=self.open_or_focus_detail_window)
        menu.add_command(
            label="🌐 打开网页版高级仪表盘",
            command=lambda: open_url_on_default_desktop(nm_config.dashboard_url(self.cfg.get('theme', 'dark')))
        )
        menu.add_separator()

        # 主题风格切换子菜单（6 款主题）
        theme_menu = tk.Menu(
            menu, tearoff=0,
            bg=th["card_bg"], fg=th["text_main"],
            activebackground=th["tab_sel_bg"], activeforeground=th["tab_sel_fg"],
            font=("Microsoft YaHei UI", 9)
        )
        cur_t = self.cfg.get("theme", "dark")
        for t_key in THEME_ORDER:
            t_info = THEMES[t_key]
            prefix = "✓ " if t_key == cur_t else "   "
            theme_menu.add_command(
                label=f"{prefix}{t_info['name']}",
                command=lambda k=t_key: self.set_theme(k)
            )
        menu.add_cascade(label=f"🎨 界面主题风格 ({th['short']})", menu=theme_menu)

        top_text = "✓ 悬浮窗始终置顶" if self.cfg.get("topmost", True) else "   悬浮窗始终置顶"
        menu.add_command(label=top_text, command=self.toggle_topmost)

        lock_text = "✓ 锁定悬浮窗位置" if self.cfg.get("locked", False) else "   锁定悬浮窗位置"
        menu.add_command(label=lock_text, command=self.toggle_locked)

        style_text = "切换为双行卡片模式" if self.cfg.get("layout", "horizontal") == "horizontal" else "切换为单行胶囊模式"
        menu.add_command(label=style_text, command=self.toggle_layout)

        h_menu = tk.Menu(
            menu, tearoff=0,
            bg=th["card_bg"], fg=th["text_main"],
            activebackground=th["tab_sel_bg"], activeforeground=th["tab_sel_fg"],
            font=("Microsoft YaHei UI", 9)
        )
        cur_hm = self.cfg.get("height_mode", "compact")
        lbl_compact = "✓ 极窄精简 (24px 极窄)" if cur_hm == "compact" else "   极窄精简 (24px 极窄)"
        lbl_standard = "✓ 标准舒适 (28px)" if cur_hm == "standard" else "   标准舒适 (28px)"
        h_menu.add_command(label=lbl_compact, command=lambda: self.set_height_mode("compact"))
        h_menu.add_command(label=lbl_standard, command=lambda: self.set_height_mode("standard"))
        menu.add_cascade(label="📏 悬浮窗厚度规格", menu=h_menu)

        alpha_menu = tk.Menu(
            menu, tearoff=0,
            bg=th["card_bg"], fg=th["text_main"],
            activebackground=th["tab_sel_bg"], activeforeground=th["tab_sel_fg"],
            font=("Microsoft YaHei UI", 9)
        )
        for val, lbl in [(1.0, "100% 不透明"), (0.95, "95% 标准"), (0.85, "85% 微透"), (0.7, "70% 半透")]:
            alpha_menu.add_command(label=lbl, command=lambda v=val: self.set_alpha(v))
        menu.add_cascade(label="🌓 悬浮窗透明度", menu=alpha_menu)

        menu.add_separator()
        menu.add_command(label="🔄 立即刷新数据", command=self.trigger_refresh)
        menu.add_command(label="✕ 关闭悬浮窗", command=self.close_widget)
        menu.add_command(label="🛑 彻底退出全部服务", command=self.exit_all)

        menu.tk_popup(event.x_root, event.y_root)

    def toggle_topmost(self):
        cur = self.cfg.get("topmost", True)
        self.cfg["topmost"] = not cur
        self.apply_topmost()
        save_config(self.cfg)

    def toggle_layout(self):
        new_layout = "card" if self.cfg.get("layout", "horizontal") == "horizontal" else "horizontal"
        self.apply_layout(new_layout)

    def set_height_mode(self, mode):
        self.cfg["height_mode"] = mode
        self.apply_layout(self.cfg.get("layout", "horizontal"))
        save_config(self.cfg)

    def toggle_locked(self):
        cur = self.cfg.get("locked", False)
        self.cfg["locked"] = not cur
        if self.cfg["locked"]:
            self.canvas.config(cursor="arrow")
        else:
            self.canvas.config(cursor="fleur")
        save_config(self.cfg)

    def set_alpha(self, val):
        self.cfg["alpha"] = val
        self.apply_alpha()
        save_config(self.cfg)

    def trigger_refresh(self):
        threading.Thread(target=self.fetch_stats_once, daemon=True).start()

    def close_widget(self):
        self.running = False
        nm_windows.remove_pid(nm_config.WIDGET_PID)
        if self.detail_window and self.detail_window.root.winfo_exists():
            try:
                self.detail_window.root.destroy()
            except Exception:
                pass
        self.root.destroy()
        sys.exit(0)

    def exit_all(self):
        self.running = False
        try:
            # 只停止本程序的后台服务（优雅退出 + PID 校验），不会影响其他 Python 进程
            for msg in nm_windows.stop_monitor():
                log_event(msg)
        except Exception as e:
            log_event(f"停止后台服务失败: {e}")
        self.close_widget()

    def fetch_stats_once(self):
        try:
            data = None
            for url in (API_MINI_URL, API_FULL_URL):
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "NetMonitorMini/1.0"})
                    with urllib.request.urlopen(req, timeout=2.5) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
                    break
                except Exception:
                    continue

            if not data:
                raise RuntimeError("无法连接到监控数据接口")

            rt = data.get("realtime", {})
            today = data.get("overview", {}).get("today", {})

            with self.data_lock:
                self.latest_data["down_speed"] = rt.get("current_down_speed", 0.0)
                self.latest_data["up_speed"] = rt.get("current_up_speed", 0.0)
                self.latest_data["proxy_down_speed"] = rt.get("proxy_down_speed", 0.0)
                self.latest_data["proxy_up_speed"] = rt.get("proxy_up_speed", 0.0)
                self.latest_data["direct_down_speed"] = rt.get("direct_down_speed", 0.0)
                self.latest_data["direct_up_speed"] = rt.get("direct_up_speed", 0.0)
                self.latest_data["proxy_speed"] = self.latest_data["proxy_down_speed"] + self.latest_data["proxy_up_speed"]
                self.latest_data["direct_speed"] = self.latest_data["direct_down_speed"] + self.latest_data["direct_up_speed"]
                self.latest_data["today_total"] = today.get("total", 0)
                self.latest_data["today_dl"] = today.get("download", 0)
                self.latest_data["today_ul"] = today.get("upload", 0)
                self.latest_data["process_speeds"] = rt.get("process_speeds", {})
                self.latest_data["ranking"] = data.get("ranking", [])
                self.latest_data["route_today"] = data.get("overview", {}).get("route_today")
                self.latest_data["collector"] = data.get("collector") or {}
                self.latest_data["connected"] = True
                self.consecutive_fails = 0
        except Exception:
            with self.data_lock:
                self.consecutive_fails += 1
                if self.consecutive_fails >= 3:
                    self.latest_data["connected"] = False
                    launch_backend_if_needed()

    def background_fetch_loop(self):
        while self.running:
            self.fetch_stats_once()
            time.sleep(1.0)

    def update_gui_loop(self):
        if not self.running:
            return

        # 检测配置文件是否被网页端同步修改了主题风格
        try:
            if os.path.exists(CONFIG_FILE):
                mtime = os.path.getmtime(CONFIG_FILE)
                if mtime != getattr(self, "_cfg_mtime", 0):
                    self._cfg_mtime = mtime
                    ext_cfg = load_config()
                    ext_theme = ext_cfg.get("theme", "dark")
                    if ext_theme in THEMES and ext_theme != self.cfg.get("theme"):
                        self.cfg["theme"] = ext_theme
                        self.apply_theme_to_widget()
                        if self.detail_window and self.detail_window.root.winfo_exists():
                            self.detail_window.apply_theme()
        except Exception:
            pass

        with self.data_lock:
            d = self.latest_data.copy()

        th = get_theme(self.cfg.get("theme", "dark"))
        c = self.canvas
        if d["connected"]:
            td_str = format_bytes(d["today_total"])
            dl_str = format_speed(d["down_speed"])
            ul_str = format_speed(d["up_speed"])
            collector_ok = (d.get("collector") or {}).get("state", "ok") == "ok"
            c.itemconfigure(self.item_dot, fill=th["c_down"] if collector_ok else th["c_today"])
        else:
            td_str = "连接中..."
            dl_str = "0 B/s"
            ul_str = "0 B/s"
            c.itemconfigure(self.item_dot, fill=th["c_today"])

        c.itemconfigure(self.item_td_val, text=td_str)
        c.itemconfigure(self.item_dl_val, text=dl_str)
        c.itemconfigure(self.item_ul_val, text=ul_str)

        self.root.after(500, self.update_gui_loop)

    def run(self):
        self.root.mainloop()


def check_or_wake_existing():
    """检测是否已有悬浮窗实例在运行。如果已有实例，发送唤醒指令并通知其弹出详情窗口"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        s.connect(("127.0.0.1", IPC_PORT))
        s.sendall(b"WAKE_DETAIL\n")
        s.close()
        log_event(f"成功连接到已有实例({IPC_PORT})，已发送 WAKE_DETAIL 唤醒指令")
        return True
    except Exception:
        log_event(f"未检测到 {IPC_PORT} 端口已有实例，本进程将作为主悬浮窗启动")
        return False


if __name__ == "__main__":
    log_event(f"mini_widget 启动，参数: {sys.argv}")
    if check_or_wake_existing():
        log_event("已有主实例已被唤醒，当前启动器进程正常退出")
        sys.exit(0)

    try:
        app = MiniWidgetApp()
        app.run()
    except Exception as e:
        import traceback
        log_event(f"MiniWidgetApp 异常退出: {e}\n{traceback.format_exc()}")
        raise
