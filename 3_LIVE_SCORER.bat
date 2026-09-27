@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist devdb.sqlite (
  echo [..] первый запуск: засеваю историю девов из dune_out
  python -m live.devdb --seed
)
python live_scorer.py --record raw_sample.jsonl
pause
