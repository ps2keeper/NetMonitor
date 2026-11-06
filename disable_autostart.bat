@echo off
chcp 65001 >nul
title 取消 NetMonitor 开机自启
echo [*] 正在取消 NetMonitor 开机自启...
python "%~dp0monitor.py" --no-autostart
pause
