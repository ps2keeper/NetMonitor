@echo off
chcp 65001 >nul
title 创建 NetMonitor 桌面快捷方式
python "%~dp0create_shortcuts.py"
pause
