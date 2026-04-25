@echo off
cd /d C:\Users\DADDY\PycharmProjects\Funding_strategies\MEXC_anal_GPT\mexce

:loop
python -m core.collector >> logs\collector.log 2>&1
timeout /t 15 /nobreak >nul
goto loop