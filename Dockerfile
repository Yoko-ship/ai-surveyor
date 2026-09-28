# Образ ИИ-сюрвейера INSON.
# Порт берётся из переменной окружения PORT (Railway/Render задают его сами), по умолчанию 8000.
# Что не попадает в образ — см. .dockerignore (база, загрузки, песочница, секреты, library/).
FROM python:3.12-slim
WORKDIR /srv
ENV PYTHONIOENCODING=utf-8 PYTHONUNBUFFERED=1 PORT=8000

# Слои — от редко меняющегося к часто меняющемуся: при правке кода пакеты и отчёты НАПП
# берутся из кэша сборки, заново выполняются только последние шаги.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY data/inbox data/inbox
COPY data/parsed data/parsed
COPY data/dealers_uz.json data/dealers_uz.json
COPY library/catalog.json library/catalog.json
# открытые тексты законов и актов регуляторов (.txt) — по ним отвечает ИИ специалист
COPY library/01_Законодательство library/01_Законодательство
COPY db db
COPY docs docs
COPY tools tools
COPY app app

# База собирается при сборке образа: справочники нужны сразу. STORAGE_DIR при сборке не учитываем:
# база образа — всегда data/surveyor.db; на постоянный диск её переносит сервер при первом старте,
# а при следующих — доводит справочники по отпечатку сборки (app/refsync.py)
# (app/db.py, init_storage), а уже существующую базу на диске не трогает.
RUN env -u STORAGE_DIR python tools/db_build.py && env -u STORAGE_DIR python tools/market_stats.py

EXPOSE 8000

# Проверка живости: сервер отвечает на /health
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/health',timeout=4)" || exit 1

# Ровно один процесс uvicorn (--workers 1): база SQLite и фоновые потоки (НАПП, stat.uz, законы,
# доклад, бот) живут внутри процесса. Два процесса = два комплекта потоков, двойные обновления
# и спор за запись в базу. Подробно — docs/Деплой — тестовый запуск.md, раздел «Один процесс».
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
