@echo off
title NetMonitor
start "" http://127.0.0.1:8899
where pythonw >nul 2>nul
if %errorlevel% equ 0 (
    start "" pythonw "%~dp0monitor.py"
    start "" pythonw "%~dp0mini_widget.py"
) else (
    start /b python "%~dp0monitor.py"
    start /b python "%~dp0mini_widget.py"
)
exit
