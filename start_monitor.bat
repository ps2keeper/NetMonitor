@echo off
title NetMonitor
chcp 65001 >nul
echo 正在后台启动 NetMonitor 监控服务并打开仪表盘...

:: 打开 Web 仪表盘
start "" http://127.0.0.1:8899

:: 启动后台监控程序（优先使用 pythonw 静默后台无窗口运行）
where pythonw >nul 2>nul
if %errorlevel% equ 0 (
    start "" pythonw "%~dp0monitor.py"
) else (
    start /b python "%~dp0monitor.py"
)

:: 自动退出命令行窗口
exit
