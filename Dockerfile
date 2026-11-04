FROM python:3.14-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATABASE_PATH=/data/stats.db

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 bot \
    && useradd --uid 10001 --gid bot --no-create-home bot \
    && mkdir /data \
    && chown bot:bot /data

COPY main.py quiz.py duel.py progress.py buttons.py view.py storage.py backend.py prefs.py app.py screens.py max_adapter.py questions.json max-ca.pem ./

USER bot:bot
STOPSIGNAL SIGINT
ENTRYPOINT ["python", "main.py"]
