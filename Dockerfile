FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Данные (SQLite-файл) — в volume, чтобы не терять сток/заказы при пересборке.
VOLUME ["/app/data"]
ENV DATABASE_URL=sqlite+aiosqlite:////app/data/vallshop.db

# Порт вебхук-сервера (используется только если задан PUBLIC_BASE_URL).
EXPOSE 8080

CMD ["python", "-m", "bot.main"]
