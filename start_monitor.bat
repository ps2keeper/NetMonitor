@echo off
title NetMonitor
echo ========================================================
echo   NetMonitor - Clash Verge Traffic Monitor
echo   Web Dashboard: http://127.0.0.1:8899
echo ========================================================
start "" http://127.0.0.1:8899
:run
python "%~dp0monitor.py"
echo.
echo ========================================================
echo   NetMonitor stopped. Press any key to restart...
echo ========================================================
pause >nul
goto run
