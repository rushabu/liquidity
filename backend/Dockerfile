FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY app ./app
COPY migrations ./migrations
RUN useradd --system --uid 10001 appuser
USER appuser
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"
# RUN_MIGRATIONS=true applies backend/migrations/*.sql (idempotent) before the server starts
CMD ["sh", "-c", "if [ \"$RUN_MIGRATIONS\" = \"true\" ]; then python -m app.db_migrate; fi; exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
