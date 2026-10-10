FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN mkdir -p /app/data/storage

# Сервис слушает 7860 (по умолчанию); можно переопределить переменной PORT,
# например в docker-compose: PORT=8000 для совместимости с nginx-прокси
ENV PORT=7860
EXPOSE 7860
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
