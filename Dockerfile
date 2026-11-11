FROM python:3.12-slim

# unbuffered logs stream straight to the platform logger
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY content/ ./content/
COPY web/ ./web/

# Create data directory for SQLite + caches, owned by an unprivileged user
RUN useradd --create-home appuser && mkdir -p data/cache && chown -R appuser:appuser /app
USER appuser

EXPOSE 7860

# Render assigns PORT dynamically; 7860 is the default elsewhere.
# exec makes uvicorn PID 1 so SIGTERM reaches it (graceful shutdown).
CMD sh -c "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860}"
