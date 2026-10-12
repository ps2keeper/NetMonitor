"""
NetMonitor Mini Desktop Widget & APP (桌面网络监控悬浮窗与原生详细面板)
---------------------------------------------------------------------
特性：
1. 极简桌面悬浮窗 (Mini Widget)：
   - 常驻桌面最前端 (Always on Top)
   - 仅显示三项核心指标：当前实时下行速率、上行速率、当日累计总流量
   - 鼠标任意拖拽、自动记忆坐标并内置防越界保护
   - 单行横条模式与双行卡片模式一键切换
   - 防手抖微移保护 (Drag Threshold)

2. 双击唤起原生桌面 APP 详情窗口 (Detail Window)：
   - 原生 Windows 桌面窗口（非网页），深色质感设计
   - 实时展示当前各进程下载/上传网速排行榜（秒级刷新）
   - 展示今日各程序总流量消耗排行榜及占比
   - 提供快捷操作与网页高级分析入口
"""

import ctypes
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

# 基础配置与文件路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "mini_config.json")
ICON_FILE = os.path.join(BASE_DIR, "netmonitor.ico")
LOG_FILE = os.path.join(BASE_DIR, "mini_widget.log")
API_MINI_URL = os.environ.get("NETMONITOR_MINI_API", "http://127.0.0.1:8899/api/mini/stats")
API_FULL_URL = os.environ.get("NETMONITOR_API", "http://127.0.0.1:8899/api/stats")
IPC_PORT = 8897  # 本地进程间通信端口，用于已运行悬浮窗的即时唤醒

def log_event(msg):
    """记录运行时关键诊断日志到 mini_widget.log"""
    try:
        t = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{t}] [PID:{os.getpid()}] {msg}\n")
    except Exception:
        pass

DEFAULT_CONFIG = {
    "x": None,
    "y": None,
    "layout": "horizontal",  # 'horizontal' 或 'card'
    "topmost": True,
    "locked": False,
    "alpha": 0.95
}

def load_config():
    cfg = DEFAULT_CONFIG.copy()
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                if isinstance(saved, dict):
                    cfg.update(saved)
        except Exception:
            pass
    return cfg

def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception:
        pass

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
            flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
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
                ctypes.windll.user32.ShowWindow(hwnd, 9)  # 9 = SW_RESTORE
                ctypes.windll.user32.SetForegroundWindow(hwnd)
                ctypes.windll.user32.BringWindowToTop(hwnd)
            except Exception as e:
                log_event(f"bring_window_to_foreground win32 error: {e}")
        # 短暂启用置顶以击穿 Windows 前台锁定，随后根据置顶状态恢复
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
    """悬停提示窗"""
    def __init__(self, root):
        self.root = root
        self.tw = None
        self.after_id = None

    def schedule(self, text_getter, event):
        self.cancel()
        x, y = event.x_root + 15, event.y_root + 15
        self.after_id = self.root.after(350, lambda: self.show(text_getter(), x, y))

    def show(self, text, x, y):
        if not text or self.tw:
            return
        self.tw = tw = tk.Toplevel(self.root)
        tw.wm_overrideredirect(True)
        tw.wm_attributes("-topmost", True)
        tw.geometry(f"+{x}+{y}")
        
        frame = tk.Frame(tw, bg="#0f172a", highlightthickness=1, highlightbackground="#38bdf8")
        frame.pack(fill="both", expand=True)
        lbl = tk.Label(
            frame,
            text=text,
            justify="left",
            bg="#0f172a",
            fg="#f8fafc",
            font=("Microsoft YaHei UI", 9),
            padx=10,
            pady=8
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
        self.root.configure(bg="#090d16")
        self.is_pinned = False

        if os.path.exists(ICON_FILE):
            try:
                self.root.iconbitmap(ICON_FILE)
            except Exception:
                pass

        # 居中显示在主屏幕黄金位置
        w, h = 680, 560
        self.root.minsize(600, 480)
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        cx = max(20, (sw - w) // 2)
        cy = max(20, (sh - h) // 3)
        self.root.geometry(f"{w}x{h}+{cx}+{cy}")

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.is_pinned = False

        # Windows 下确保详细面板在任务栏拥有独立图标与切换按钮
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

        # 界面构建
        self.build_ui()
        self.update_content()

        # 弹出到最前台
        bring_window_to_foreground(self.root, is_pinned=self.is_pinned)

    def build_ui(self):
        # 1. 顶部 Header
        header = tk.Frame(self.root, bg="#0f172a", padx=18, pady=12)
        header.pack(fill="x")

        title_lbl = tk.Label(
            header,
            text="🌐 NetMonitor 流量监控中心",
            font=("Microsoft YaHei UI", 12, "bold"),
            fg="#f8fafc",
            bg="#0f172a"
        )
        title_lbl.pack(side="left")

        # 右侧操作区
        btn_box = tk.Frame(header, bg="#0f172a")
        btn_box.pack(side="right")

        self.pin_var = tk.BooleanVar(value=False)
        chk_pin = tk.Checkbutton(
            btn_box,
            text="保持置顶",
            variable=self.pin_var,
            command=self.toggle_pin,
            font=("Microsoft YaHei UI", 9),
            bg="#0f172a",
            fg="#cbd5e1",
            selectcolor="#1e293b",
            activebackground="#0f172a",
            activeforeground="#38bdf8",
            cursor="hand2"
        )
        chk_pin.pack(side="left", padx=(0, 10))

        btn_browser = tk.Button(
            btn_box,
            text="打开网页高级看板 ↗",
            font=("Microsoft YaHei UI", 9),
            bg="#1e293b",
            fg="#38bdf8",
            activebackground="#2563eb",
            activeforeground="#ffffff",
            bd=0,
            padx=10,
            pady=4,
            cursor="hand2",
            command=self.open_web_dashboard
        )
        btn_browser.pack(side="left")

        # 2. KPI 卡片容器
        kpi_frame = tk.Frame(self.root, bg="#090d16", padx=16, pady=10)
        kpi_frame.pack(fill="x")

        # 卡片 1：实时下载
        self.card_dl = self.create_kpi_card(kpi_frame, "实时下行速率", "↓ 0.0 KB/s", "#10b981")
        self.card_dl.pack(side="left", fill="both", expand=True, padx=(0, 6))

        # 卡片 2：实时上传
        self.card_ul = self.create_kpi_card(kpi_frame, "实时上行速率", "↑ 0.0 KB/s", "#38bdf8")
        self.card_ul.pack(side="left", fill="both", expand=True, padx=3)

        # 卡片 3：今日总计
        self.card_td = self.create_kpi_card(kpi_frame, "今日共用总流量", "0.0 MB", "#fbbf24")
        self.card_td.pack(side="left", fill="both", expand=True, padx=(6, 0))

        # 3. Notebook 选项卡 (实时速度 vs 今日排行)
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("Dark.TNotebook", background="#090d16", borderwidth=0)
        style.configure("Dark.TNotebook.Tab", background="#1e293b", foreground="#94a3b8", padding=[16, 6], font=("Microsoft YaHei UI", 9))
        style.map("Dark.TNotebook.Tab", background=[("selected", "#2563eb")], foreground=[("selected", "#ffffff")])

        style.configure("Dark.Treeview", background="#0f172a", foreground="#f8fafc", fieldbackground="#0f172a", borderwidth=0, font=("Segoe UI", 9))
        style.configure("Dark.Treeview.Heading", background="#1e293b", foreground="#cbd5e1", font=("Microsoft YaHei UI", 9, "bold"), relief="flat")
        style.map("Dark.Treeview", background=[("selected", "#1d4ed8")], foreground=[("selected", "#ffffff")])

        notebook = ttk.Notebook(self.root, style="Dark.TNotebook")
        notebook.pack(fill="both", expand=True, padx=16, pady=(4, 10))

        # Tab 1: 实时活跃进程
        tab_rt = tk.Frame(notebook, bg="#0f172a")
        notebook.add(tab_rt, text="⚡ 实时占用网速进程")

        self.tree_rt = ttk.Treeview(
            tab_rt,
            columns=("proc", "down", "up"),
            show="headings",
            style="Dark.Treeview"
        )
        self.tree_rt.heading("proc", text="活跃进程名", anchor="w")
        self.tree_rt.heading("down", text="实时下行速率", anchor="e")
        self.tree_rt.heading("up", text="实时上行速率", anchor="e")
        self.tree_rt.column("proc", width=260, anchor="w")
        self.tree_rt.column("down", width=140, anchor="e")
        self.tree_rt.column("up", width=140, anchor="e")

        rt_scroll = ttk.Scrollbar(tab_rt, orient="vertical", command=self.tree_rt.yview)
        self.tree_rt.configure(yscrollcommand=rt_scroll.set)
        self.tree_rt.pack(side="left", fill="both", expand=True)
        rt_scroll.pack(side="right", fill="y")

        # Tab 2: 今日流量消耗排行
        tab_rank = tk.Frame(notebook, bg="#0f172a")
        notebook.add(tab_rank, text="📊 今日消耗排行 TOP 50")

        self.tree_rank = ttk.Treeview(
            tab_rank,
            columns=("rank", "proc", "total", "down", "up", "pct"),
            show="headings",
            style="Dark.Treeview"
        )
        self.tree_rank.heading("rank", text="#", anchor="center")
        self.tree_rank.heading("proc", text="进程名称", anchor="w")
        self.tree_rank.heading("total", text="今日总计", anchor="e")
        self.tree_rank.heading("down", text="下载量", anchor="e")
        self.tree_rank.heading("up", text="上传量", anchor="e")
        self.tree_rank.heading("pct", text="占比", anchor="e")

        self.tree_rank.column("rank", width=40, anchor="center")
        self.tree_rank.column("proc", width=180, anchor="w")
        self.tree_rank.column("total", width=95, anchor="e")
        self.tree_rank.column("down", width=95, anchor="e")
        self.tree_rank.column("up", width=95, anchor="e")
        self.tree_rank.column("pct", width=65, anchor="e")

        rank_scroll = ttk.Scrollbar(tab_rank, orient="vertical", command=self.tree_rank.yview)
        self.tree_rank.configure(yscrollcommand=rank_scroll.set)
        self.tree_rank.pack(side="left", fill="both", expand=True)
        rank_scroll.pack(side="right", fill="y")

        # 4. 底部栏
        footer = tk.Frame(self.root, bg="#0f172a", padx=16, pady=8)
        footer.pack(fill="x")

        self.lbl_status = tk.Label(
            footer,
            text="🟢 服务连接正常 · 数据每秒自动同步",
            font=("Microsoft YaHei UI", 8),
            fg="#10b981",
            bg="#0f172a"
        )
        self.lbl_status.pack(side="left")

        btn_close = tk.Button(
            footer,
            text="关闭面板",
            font=("Microsoft YaHei UI", 9),
            bg="#334155",
            fg="#f8fafc",
            activebackground="#475569",
            bd=0,
            padx=14,
            pady=3,
            cursor="hand2",
            command=self.on_close
        )
        btn_close.pack(side="right")

    def toggle_pin(self):
        self.is_pinned = self.pin_var.get()
        self.root.is_pinned = self.is_pinned
        self.root.attributes("-topmost", self.is_pinned)

    def create_kpi_card(self, parent, title, initial_val, val_color):
        card = tk.Frame(parent, bg="#0f172a", highlightthickness=1, highlightbackground="#1e293b", padx=12, pady=10)
        lbl_title = tk.Label(card, text=title, font=("Microsoft YaHei UI", 8), fg="#94a3b8", bg="#0f172a")
        lbl_title.pack(anchor="w")
        lbl_val = tk.Label(card, text=initial_val, font=("Segoe UI", 12, "bold"), fg=val_color, bg="#0f172a")
        lbl_val.pack(anchor="w", pady=(3, 0))
        card.lbl_val = lbl_val
        return card

    def open_web_dashboard(self):
        try:
            os.startfile("http://127.0.0.1:8899")
        except Exception:
            webbrowser.open("http://127.0.0.1:8899")

    def update_content(self):
        if not self.root.winfo_exists():
            return

        with self.app.data_lock:
            d = self.app.latest_data.copy()

        # 更新卡片数值
        if d.get("connected"):
            self.card_dl.lbl_val.config(text=f"↓ {format_speed(d['down_speed'])}")
            self.card_ul.lbl_val.config(text=f"↑ {format_speed(d['up_speed'])}")
            self.card_td.lbl_val.config(text=format_bytes(d['today_total']))
            self.lbl_status.config(text="🟢 服务连接正常 · 每秒实时同步", fg="#10b981")
        else:
            self.card_dl.lbl_val.config(text="↓ 0.0 KB/s")
            self.card_ul.lbl_val.config(text="↑ 0.0 KB/s")
            self.card_td.lbl_val.config(text="连接中...")
            self.lbl_status.config(text="🟡 正在连接后台监控服务...", fg="#fbbf24")

        # 更新实时进程树
        proc_speeds = d.get("process_speeds", {})
        active_list = sorted(proc_speeds.items(), key=lambda x: (x[1].get("down", 0) + x[1].get("up", 0)), reverse=True)
        
        self.tree_rt.delete(*self.tree_rt.get_children())
        if active_list:
            for proc, s in active_list:
                down_str = format_speed(s.get("down", 0))
                up_str = format_speed(s.get("up", 0))
                self.tree_rt.insert("", "end", values=(proc, down_str, up_str))
        else:
            self.tree_rt.insert("", "end", values=("当前暂无高速传输连接", "-", "-"))

        # 更新今日排行树
        ranking = d.get("ranking", [])
        self.tree_rank.delete(*self.tree_rank.get_children())
        if ranking:
            for r in ranking[:50]:
                self.tree_rank.insert("", "end", values=(
                    r.get("rank", "-"),
                    r.get("process_name", "-"),
                    format_bytes(r.get("total", 0)),
                    format_bytes(r.get("download", 0)),
                    format_bytes(r.get("upload", 0)),
                    f"{r.get('pct', 0)}%"
                ))

        # 定时每秒刷新一次面板
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
        self.root = tk.Tk()
        self.root.title("NetMonitor Mini")
        
        # 无边框与置顶
        self.root.overrideredirect(True)
        self.apply_topmost()
        self.apply_alpha()

        if os.path.exists(ICON_FILE):
            try:
                self.root.iconbitmap(ICON_FILE)
            except Exception:
                pass

        # 详情窗口引用
        self.detail_window = None

        # 拖拽与双击状态
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

        # 最新数据缓存
        self.latest_data = {
            "down_speed": 0.0,
            "up_speed": 0.0,
            "today_total": 0,
            "today_dl": 0,
            "today_ul": 0,
            "process_speeds": {},
            "ranking": [],
            "connected": False
        }
        self.data_lock = threading.Lock()
        self.running = True
        self.consecutive_fails = 0

        # Tooltip
        self.tooltip = Tooltip(self.root)

        # 构建画布与界面
        self.canvas = tk.Canvas(
            self.root,
            bg="#090d16",
            highlightthickness=1,
            highlightbackground="#334155",
            bd=0
        )
        self.canvas.pack(fill="both", expand=True)

        # 绘制文本元素
        self.init_ui_elements()
        self.apply_layout(self.cfg.get("layout", "horizontal"), initial=True)

        # 事件绑定
        self.bind_events()

        # 启动本地 IPC 服务（用于唤醒已运行实例）
        self.start_ipc_server()

        # 启动后台拉取线程
        self.fetch_thread = threading.Thread(target=self.background_fetch_loop, daemon=True)
        self.fetch_thread.start()

        # 定时刷新 GUI（每 500ms 刷新一次界面显示）
        self.update_gui_loop()

        # 启动时自动弹出桌面原生监控详情窗口 (APP)，确保双击桌面图标有直观的窗口展现
        self.root.after(300, self.open_or_focus_detail_window)

    def start_ipc_server(self):
        """本地 IPC 监听：收到 WAKE_DETAIL 命令时立即弹出并聚焦详情窗口"""
        def ipc_loop():
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
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
                    data = conn.recv(1024)
                    conn.close()
                    if b"WAKE_DETAIL" in data:
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
        """初始化 Canvas 上的所有文本与图标对象"""
        c = self.canvas
        # 1. 下载速率
        self.item_dl_icon = c.create_text(0, 0, text="↓", fill="#10b981", font=("Segoe UI", 10, "bold"), anchor="w")
        self.item_dl_val = c.create_text(0, 0, text="0.0 KB/s", fill="#f8fafc", font=("Segoe UI", 9, "bold"), anchor="w")
        
        # 2. 分隔符 1
        self.item_sep1 = c.create_text(0, 0, text="│", fill="#334155", font=("Segoe UI", 9), anchor="w")

        # 3. 上传速率
        self.item_ul_icon = c.create_text(0, 0, text="↑", fill="#38bdf8", font=("Segoe UI", 10, "bold"), anchor="w")
        self.item_ul_val = c.create_text(0, 0, text="0.0 KB/s", fill="#f8fafc", font=("Segoe UI", 9, "bold"), anchor="w")

        # 4. 分隔符 2
        self.item_sep2 = c.create_text(0, 0, text="│", fill="#334155", font=("Segoe UI", 9), anchor="w")

        # 5. 今日流量
        self.item_td_lbl = c.create_text(0, 0, text="今日", fill="#94a3b8", font=("Microsoft YaHei UI", 8), anchor="w")
        self.item_td_val = c.create_text(0, 0, text="0.0 MB", fill="#fbbf24", font=("Segoe UI", 9, "bold"), anchor="w")

    def apply_layout(self, layout_name, initial=False):
        """切换布局：'horizontal' (单行极窄胶囊条) 或 'card' (双行精简卡片)"""
        self.cfg["layout"] = layout_name
        height_mode = self.cfg.get("height_mode", "compact")
        c = self.canvas

        if layout_name == "card":
            if height_mode == "standard":
                w, h = 236, 48
                y1, y2 = 14, 34
            else:
                w, h = 230, 42
                y1, y2 = 12, 30

            # 第一行：下载与上传
            c.coords(self.item_dl_icon, 10, y1)
            c.coords(self.item_dl_val, 22, y1)
            c.coords(self.item_sep1, 114, y1)
            c.itemconfigure(self.item_sep1, state="normal")
            c.coords(self.item_ul_icon, 124, y1)
            c.coords(self.item_ul_val, 136, y1)
            # 第二行：今日总计
            c.itemconfigure(self.item_sep2, state="hidden")
            c.coords(self.item_td_lbl, 10, y2)
            c.itemconfigure(self.item_td_lbl, text="今日流量")
            c.coords(self.item_td_val, 68, y2)
        else:
            layout_name = "horizontal"
            self.cfg["layout"] = "horizontal"
            if height_mode == "standard":
                w, h = 360, 28
                y_mid = 14
            else:
                w, h = 355, 24
                y_mid = 12

            c.coords(self.item_dl_icon, 10, y_mid)
            c.coords(self.item_dl_val, 22, y_mid)
            c.coords(self.item_sep1, 114, y_mid)
            c.itemconfigure(self.item_sep1, state="normal")
            c.coords(self.item_ul_icon, 124, y_mid)
            c.coords(self.item_ul_val, 136, y_mid)
            c.coords(self.item_sep2, 228, y_mid)
            c.itemconfigure(self.item_sep2, state="normal")
            c.coords(self.item_td_lbl, 238, y_mid)
            c.itemconfigure(self.item_td_lbl, text="今日")
            c.coords(self.item_td_val, 268, y_mid)

        c.config(width=w, height=h)
        self.current_w = w
        self.current_h = h

        # 窗口位置恢复与计算
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

        # 防跑出屏幕
        cur_x = max(0, min(screen_w - w, cur_x))
        cur_y = max(0, min(screen_h - h, cur_y))

        self.root.geometry(f"{w}x{h}+{cur_x}+{cur_y}")
        save_config(self.cfg)

    def bind_events(self):
        c = self.canvas
        # 鼠标拖动与点击绑定 (绑定至 Canvas 即可全局捕获所有内部文本元素)
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

        # 防微移与手抖保护：位移必须超过 6 像素才判定为拖拽移动
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
            # 真实拖拽结束，记忆坐标
            self.cfg["x"] = self.root.winfo_x()
            self.cfg["y"] = self.root.winfo_y()
            save_config(self.cfg)
        else:
            # 单击释放判定（双击计时兜底：两次点击释放间隔在 50ms~420ms 之间判定为双击，100% 可靠捕获）
            now = time.time()
            diff = now - self.last_click_time
            if 0.05 < diff < 0.42:
                self.last_click_time = 0
                log_event(f"on_mouse_up: 手动双击判定触发 (间隔 {diff:.3f}s)")
                self.on_double_click_handler()
                return
            self.last_click_time = now

    def on_double_click_handler(self, event=None):
        """双击处理：弹出桌面原生监控详情窗口"""
        now = time.time()
        if now - self.last_trigger_time < 0.35:
            return
        self.last_trigger_time = now
        log_event("on_double_click_handler 触发")

        # 视觉反馈：闪烁天青色高亮边框 250ms
        try:
            self.canvas.config(highlightbackground="#38bdf8")
            self.root.after(250, lambda: self.canvas.config(highlightbackground="#334155") if self.root.winfo_exists() else None)
        except Exception:
            pass

        self.open_or_focus_detail_window()

    def open_or_focus_detail_window(self):
        """打开或激活桌面原生详细面板"""
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
        if not self.cfg.get("locked", False):
            self.canvas.config(cursor="fleur")
        else:
            self.canvas.config(cursor="arrow")

        def get_tooltip_text():
            with self.data_lock:
                d = self.latest_data.copy()
            if not d["connected"]:
                return "NetMonitor 服务连接中...\n(后台服务正在启动，稍候即连)"
            
            return (
                f"【NetMonitor 实时网络小组件】\n"
                f"──────────────────────────\n"
                f" 实时下行速率 :  {format_speed(d['down_speed'])}\n"
                f" 实时上行速率 :  {format_speed(d['up_speed'])}\n"
                f" 今日下载总量 :  {format_bytes(d['today_dl'])}\n"
                f" 今日上传总量 :  {format_bytes(d['today_ul'])}\n"
                f" 今日流量总计 :  {format_bytes(d['today_total'])}\n"
                f"──────────────────────────\n"
                f"💡 双击：打开桌面 APP 详细面板\n"
                f"💡 拖拽：移动位置（松开自动保存）\n"
                f"💡 右键：菜单设置与退出"
            )
        self.tooltip.schedule(get_tooltip_text, event)

    def on_mouse_leave(self, event):
        self.canvas.config(cursor="")
        self.tooltip.hide()

    def show_context_menu(self, event):
        self.tooltip.hide()
        menu = tk.Menu(self.root, tearoff=0, bg="#0f172a", fg="#f8fafc", activebackground="#2563eb", activeforeground="#ffffff")

        menu.add_command(label="📊 打开桌面详细窗口 (APP)", command=self.open_or_focus_detail_window)
        menu.add_command(label="🌐 打开网页版仪表盘", command=lambda: webbrowser.open("http://127.0.0.1:8899"))
        menu.add_separator()

        # 置顶切换
        top_text = "✓ 悬浮窗始终置顶" if self.cfg.get("topmost", True) else "   悬浮窗始终置顶"
        menu.add_command(label=top_text, command=self.toggle_topmost)

        # 锁定位置
        lock_text = "✓ 锁定悬浮窗位置" if self.cfg.get("locked", False) else "   锁定悬浮窗位置"
        menu.add_command(label=lock_text, command=self.toggle_locked)

        # 样式切换
        style_text = "切换为双行卡片模式" if self.cfg.get("layout", "horizontal") == "horizontal" else "切换为单行胶囊模式"
        menu.add_command(label=style_text, command=self.toggle_layout)

        # 尺寸厚度子菜单
        h_menu = tk.Menu(menu, tearoff=0, bg="#0f172a", fg="#f8fafc", activebackground="#2563eb", activeforeground="#ffffff")
        cur_hm = self.cfg.get("height_mode", "compact")
        lbl_compact = "✓ 极窄精简 (24px 极窄)" if cur_hm == "compact" else "   极窄精简 (24px 极窄)"
        lbl_standard = "✓ 标准舒适 (28px)" if cur_hm == "standard" else "   标准舒适 (28px)"
        h_menu.add_command(label=lbl_compact, command=lambda: self.set_height_mode("compact"))
        h_menu.add_command(label=lbl_standard, command=lambda: self.set_height_mode("standard"))
        menu.add_cascade(label="📏 悬浮窗厚度规格", menu=h_menu)

        # 透明度子菜单
        alpha_menu = tk.Menu(menu, tearoff=0, bg="#0f172a", fg="#f8fafc", activebackground="#2563eb", activeforeground="#ffffff")
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
            monitor_py = os.path.join(BASE_DIR, "monitor.py")
            subprocess.run([sys.executable, monitor_py, "--stop"], capture_output=True, timeout=3)
        except Exception:
            pass
        self.close_widget()

    def fetch_stats_once(self):
        try:
            # 优先请求极速专用的 /api/mini/stats (2ms 内极速响应)，旧后端自动回退至 /api/stats
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
                self.latest_data["today_total"] = today.get("total", 0)
                self.latest_data["today_dl"] = today.get("download", 0)
                self.latest_data["today_ul"] = today.get("upload", 0)
                self.latest_data["process_speeds"] = rt.get("process_speeds", {})
                self.latest_data["ranking"] = data.get("ranking", [])
                self.latest_data["connected"] = True
                self.consecutive_fails = 0
        except Exception:
            with self.data_lock:
                self.consecutive_fails += 1
                # 容错缓冲：仅当连续 3 次拉取失败（超过 3 秒失联）才判定为断开连接，彻底消除偶发抖动导致的“连接中...”
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

        with self.data_lock:
            d = self.latest_data.copy()

        c = self.canvas
        if d["connected"]:
            dl_str = format_speed(d["down_speed"])
            ul_str = format_speed(d["up_speed"])
            td_str = format_bytes(d["today_total"])
        else:
            dl_str = "0.0 KB/s"
            ul_str = "0.0 KB/s"
            td_str = "连接中..."

        c.itemconfigure(self.item_dl_val, text=dl_str)
        c.itemconfigure(self.item_ul_val, text=ul_str)
        c.itemconfigure(self.item_td_val, text=td_str)

        # 智能动态防截断保护：如果文本在高DPI或超长数值下接近右边界，自动适配拓宽宽度，绝不篡改高度
        if self.cfg.get("layout", "horizontal") == "horizontal":
            bbox = c.bbox(self.item_td_val)
            if bbox and hasattr(self, "current_w") and hasattr(self, "current_h"):
                right_edge = bbox[2]
                if right_edge > self.current_w - 10:
                    needed_w = right_edge + 14
                    cur_x = self.root.winfo_x()
                    cur_y = self.root.winfo_y()
                    if (cur_x <= 0 or cur_y <= 0) and "x" in self.cfg and "y" in self.cfg:
                        cur_x = self.cfg["x"]
                        cur_y = self.cfg["y"]
                    self.current_w = needed_w
                    self.root.geometry(f"{needed_w}x{self.current_h}+{cur_x}+{cur_y}")
                    c.config(width=needed_w)

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
        log_event("成功连接到已有实例(8897)，已发送 WAKE_DETAIL 唤醒指令")
        return True
    except Exception:
        log_event("未检测到 8897 端口已有实例，本进程将作为主悬浮窗启动")
        return False


if __name__ == "__main__":
    log_event(f"mini_widget 启动，参数: {sys.argv}")
    # 若已有悬浮窗实例在运行，直接唤醒其详情窗口到前台并退出当前进程
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
