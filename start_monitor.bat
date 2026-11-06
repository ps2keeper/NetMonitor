@echo off
title NetMonitor
cd /d "%~dp0"
where pythonw >nul 2>nul
if %errorlevel% equ 0 (
    start "" pythonw "%~dp0monitor.py"
    start "" pythonw "%~dp0mini_widget.py"
    start "" pythonw "%~dp0monitor.py" --open
) else (
    start /b python "%~dp0monitor.py"
    start /b python "%~dp0mini_widget.py"
    start /b python "%~dp0monitor.py" --open
)
exit
