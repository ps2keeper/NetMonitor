@echo off
chcp 65001 >nul
title 取消 NetMonitor 开机自启
echo [*] 正在取消 NetMonitor 开机自启...

set "LINK_PATH=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\NetMonitor.lnk"

if exist "%LINK_PATH%" (
    del /f /q "%LINK_PATH%"
    echo [OK] 已成功从启动文件夹移除 NetMonitor 开机自启项！
) else (
    echo [INFO] 未检测到 NetMonitor 开机自启项，当前未开启自启。
)
pause
