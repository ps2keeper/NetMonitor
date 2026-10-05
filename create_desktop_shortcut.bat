@echo off
chcp 65001 >nul
title 创建 NetMonitor 桌面快捷方式
echo [*] 正在为 NetMonitor 创建桌面快捷方式...

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ws = New-Object -ComObject WScript.Shell; ^
   $desktopPath = [System.IO.Path]::Combine([Environment]::GetFolderPath('Desktop'), 'NetMonitor.lnk'); ^
   $shortcut = $ws.CreateShortcut($desktopPath); ^
   $shortcut.TargetPath = 'wscript.exe'; ^
   $shortcut.Arguments = '\"%~dp0start_monitor_silent.vbs\"'; ^
   $shortcut.WorkingDirectory = '%~dp0'; ^
   if (Test-Path '%~dp0netmonitor.ico') { $shortcut.IconLocation = '%~dp0netmonitor.ico,0' }; ^
   $shortcut.Description = 'NetMonitor 流量监控中心'; ^
   $shortcut.Save()"

if exist "%USERPROFILE%\Desktop\NetMonitor.lnk" (
    echo [OK] 已成功在桌面创建 NetMonitor 快捷方式！
    echo 快捷方式位置: %USERPROFILE%\Desktop\NetMonitor.lnk
) else (
    echo [ERROR] 创建桌面快捷方式失败，请检查写入权限。
)
pause
