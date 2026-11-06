@echo off
chcp 65001 >nul
title 设置 NetMonitor 开机自启
echo [*] 正在将 NetMonitor 设置为开机自动启动...
python "%~dp0monitor.py" --autostart
echo 每次登录 Windows 时，NetMonitor 后台服务与悬浮窗将静默启动。
pause
