@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist logs mkdir logs
python tools\solami_check.py 40 > logs\solami_check.log 2>&1
type logs\solami_check.log
echo.
echo Done. Claude reads logs\solami_check.log
pause
