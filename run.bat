@echo off
setlocal
rem Запуск ИИ-сюрвейера. Открыть в браузере: http://localhost:8000
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
rem Режим разработчика: единый вход (app/guard.py) не спрашивает пароль,
rem но только для обращений с этого же компьютера (127.0.0.1). На сервере эту переменную не задают.
set SURVEYOR_DEV=1
set "SURVEYOR_PY=%~dp0sandbox\.venv\Scripts\python.exe"
if not exist "%SURVEYOR_PY%" (
    echo Project environment is missing. Follow the setup steps in README.md.
    exit /b 1
)
if not exist data\surveyor.db (
    "%SURVEYOR_PY%" tools\db_build.py
    if errorlevel 1 exit /b 1
    "%SURVEYOR_PY%" tools\market_stats.py
    if errorlevel 1 exit /b 1
)
"%SURVEYOR_PY%" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
exit /b %errorlevel%
