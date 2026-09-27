@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Скорер 24/7 на ПК: перезапуск после падения, лог по дням в logs\.
rem Сон отключить один раз (от админа): powercfg /change standby-timeout-ac 0
if not exist logs mkdir logs
if not exist devdb.sqlite python -m live.devdb --seed
:loop
for /f %%d in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set D=%%d
echo [%date% %time%] start>> logs\scorer_%D%.log
python -u live_scorer.py >> logs\scorer_%D%.log 2>&1
echo [%date% %time%] exit code %errorlevel%, restart in 10s>> logs\scorer_%D%.log
timeout /t 10 /nobreak >nul
goto loop
