"""在桌面创建「NetMonitor 悬浮窗」与「NetMonitor」两个快捷方式（零依赖，无需 pywin32）。"""
import os
import sys

# 嵌入式/隔离模式的 Python 不会自动把脚本目录加入 sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nm_windows  # noqa: E402

# 确保在 Windows 控制台下输出 UTF-8 中文不乱码
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def create_desktop_shortcuts() -> bool:
    try:
        mini_lnk, dash_lnk = nm_windows.create_desktop_shortcuts()
    except OSError as e:
        print(f"[ERROR] 创建快捷方式失败: {e}")
        return False
    print("==================================================")
    print("  [OK] 已成功在 Windows 桌面创建快捷方式！")
    print("==================================================")
    print(f"  - 桌面悬浮窗 APP:  {mini_lnk}")
    print(f"  - 完整 Web 仪表盘: {dash_lnk}")
    print("==================================================")
    return True


if __name__ == "__main__":
    sys.exit(0 if create_desktop_shortcuts() else 1)
