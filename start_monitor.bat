@echo off
chcp 65001 >nul
title NetMonitor - Clash Verge 流量监控
echo 正在启动 NetMonitor 流量监控服务...
start "" http://127.0.0.1:8899
:run
python "%~dp0monitor.py"
echo.
echo =========================================
echo  NetMonitor 已停止运行。按任意键重新启动...
echo =========================================
pause >nul
goto run
