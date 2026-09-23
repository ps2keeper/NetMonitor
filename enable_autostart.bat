@echo off
chcp 65001 >nul
title 设置 NetMonitor 开机自启
echo [*] 正在将 NetMonitor 设置为开机自动启动...

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ws = New-Object -ComObject WScript.Shell; ^
   $startupPath = [System.IO.Path]::Combine($env:APPDATA, 'Microsoft\Windows\Start Menu\Programs\Startup\NetMonitor.lnk'); ^
   $shortcut = $ws.CreateShortcut($startupPath); ^
   $shortcut.TargetPath = 'wscript.exe'; ^
   $shortcut.Arguments = '\"%~dp0autostart_silent.vbs\"'; ^
   $shortcut.WorkingDirectory = '%~dp0'; ^
   $shortcut.Description = 'NetMonitor 流量监控服务（开机静默启动）'; ^
   $shortcut.Save()"

if exist "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\NetMonitor.lnk" (
    echo [OK] 已成功创建开机自启快捷方式！
    echo 快捷方式位置: %APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\NetMonitor.lnk
    echo 每次开机登录 Windows 时，NetMonitor 将在后台自动静默拉起。
) else (
    echo [ERROR] 创建开机自启快捷方式失败，请检查写入权限。
)
pause
