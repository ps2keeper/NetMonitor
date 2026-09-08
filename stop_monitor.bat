@echo off
title Stop NetMonitor
chcp 65001 >nul
echo 正在停止 NetMonitor 后台监控服务...
python "%~dp0monitor.py" --stop
echo.
echo ========================================================
echo   NetMonitor 已成功停止。
echo ========================================================
timeout /t 2 >nul
