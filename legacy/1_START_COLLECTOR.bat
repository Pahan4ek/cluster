@echo off
title PUMPPORTAL COLLECTOR
cd /d "%~dp0"
echo ============================================
echo   COLLECTOR started. Do NOT close!
echo   Press Ctrl+C to stop
echo ============================================
echo.
python collector.py
pause
