@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist logs mkdir logs
python tools\funding_check.py GZaXh31BKnz7Rx9MB63ki3poqd1WRaGUaheLPwuqpump 4T5LrboTSEka5cRjxcr9oe5L9yjm2rhnsm9rKRZsnubi %* > logs\funding_check.log 2>&1
type logs\funding_check.log
echo.
echo Done. Claude reads logs\funding_check.log
pause
