@echo off
rem Запуск ИИ-сюрвейера. Открыть в браузере: http://localhost:8000
cd /d %~dp0
set PYTHONIOENCODING=utf-8
rem Режим разработчика: единый вход (app/guard.py) не спрашивает пароль,
rem но только для обращений с этого же компьютера (127.0.0.1). На сервере эту переменную не задают.
set SURVEYOR_DEV=1
if not exist data\surveyor.db python tools\db_build.py
sandbox\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
