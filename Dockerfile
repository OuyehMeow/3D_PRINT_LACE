# Production NAS-ready container for 3D_PRINT_LACE
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LACE_DATA_DIR=/data \
    PORT=8000

WORKDIR /app

# Create non-root user and external data mount point
RUN groupadd --gid 1000 lace && \
    useradd --uid 1000 --gid 1000 --create-home lace && \
    mkdir -p /data && chown -R lace:lace /data /app

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

COPY backend /app/backend
COPY frontend /app/frontend
COPY AGENTS.md config.example.yaml /app/

RUN chown -R lace:lace /app
USER lace

VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)" || exit 1

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
