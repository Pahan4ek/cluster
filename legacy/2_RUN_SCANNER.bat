@echo off
title GEM DEV SCANNER
cd /d "%~dp0"
echo ============================================
echo   SCANNER started. Analyzing devs...
echo ============================================
echo.
python scanner.py
echo.
echo ============================================
echo   Done! Open Google Sheets.
echo ============================================
pause
