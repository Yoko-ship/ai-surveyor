@echo off
rem Запуск ИИ-сюрвейера. Открыть в браузере: http://localhost:8000
cd /d %~dp0
set PYTHONIOENCODING=utf-8
if not exist data\surveyor.db python tools\db_build.py
sandbox\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
