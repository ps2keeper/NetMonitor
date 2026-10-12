import os
import sys

# 确保在 Windows 控制台下输出 UTF-8 中文不乱码
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

def create_desktop_shortcuts():
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    current_dir = os.path.dirname(os.path.abspath(__file__))

    mini_vbs = os.path.join(current_dir, "start_mini_silent.vbs")
    dash_vbs = os.path.join(current_dir, "start_monitor_silent.vbs")
    ico_file = os.path.join(current_dir, "netmonitor.ico")

    # 清理桌面上任何乱码的旧快捷方式
    try:
        for f in os.listdir(desktop):
            if f.endswith(".lnk") and "netmonitor" in f.lower():
                if "悬浮窗" not in f and f != "NetMonitor.lnk":
                    old_path = os.path.join(desktop, f)
                    try:
                        os.remove(old_path)
                        print(f"  [清理] 已移除旧乱码快捷方式: {f}")
                    except Exception:
                        pass
    except Exception:
        pass

    try:
        import win32com.client
        ws = win32com.client.Dispatch("WScript.Shell")

        # 1. 悬浮窗快捷方式
        mini_lnk = os.path.join(desktop, "NetMonitor 悬浮窗.lnk")
        s1 = ws.CreateShortcut(mini_lnk)
        s1.TargetPath = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "wscript.exe")
        s1.Arguments = f'"{mini_vbs}"'
        s1.WorkingDirectory = current_dir
        if os.path.exists(ico_file):
            s1.IconLocation = f"{ico_file},0"
        s1.Description = "NetMonitor 桌面极简置顶悬浮窗与原生监控中心 APP"
        s1.Save()

        # 2. 完整仪表盘快捷方式
        dash_lnk = os.path.join(desktop, "NetMonitor.lnk")
        s2 = ws.CreateShortcut(dash_lnk)
        s2.TargetPath = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "wscript.exe")
        s2.Arguments = f'"{dash_vbs}"'
        s2.WorkingDirectory = current_dir
        if os.path.exists(ico_file):
            s2.IconLocation = f"{ico_file},0"
        s2.Description = "NetMonitor 流量监控中心仪表盘"
        s2.Save()

        print("==================================================")
        print("  [OK] 已成功在 Windows 桌面创建快捷方式！")
        print("==================================================")
        print(f"  - 桌面悬浮窗 APP:  {mini_lnk}")
        print(f"  - 完整 Web 仪表盘: {dash_lnk}")
        print("==================================================")
        return True
    except Exception as e:
        print(f"[ERROR] 创建快捷方式失败: {e}")
        return False

if __name__ == "__main__":
    create_desktop_shortcuts()
