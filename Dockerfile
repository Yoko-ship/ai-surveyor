# Образ ИИ-сюрвейера INSON.
# Порт берётся из переменной окружения PORT (Railway/Render задают его сами), по умолчанию 8000.
FROM python:3.12-slim
WORKDIR /srv
ENV PYTHONIOENCODING=utf-8 PYTHONUNBUFFERED=1 PORT=8000

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app app
COPY db db
COPY docs docs
COPY tools tools
COPY data/inbox data/inbox

# База собирается при сборке образа: справочники нужны сразу.
# На бесплатных тарифах диск не сохраняется между перезапусками — см. docs/Деплой — тестовый запуск.md
RUN python tools/db_build.py

EXPOSE 8000

# Проверка живости: сервер отвечает на /health
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/health',timeout=4)" || exit 1

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
