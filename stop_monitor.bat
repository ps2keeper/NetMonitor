@echo off
title Stop NetMonitor
python "%~dp0monitor.py" --stop
timeout /t 2 >nul
