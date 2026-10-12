FROM python:3.12-slim

WORKDIR /app

COPY index.html styles.css app.js *.mjs server.py workspace_store.py resource_cache.py genomecanvas.config.example.json ./
COPY public ./public
COPY vendor ./vendor

RUN mkdir -p /data /app/data /app/.genomecanvas/sessions /app/.genomecanvas/profiles

ENV GENOME_DATA_ROOTS=/data \
    GENOME_CANVAS_HOST=0.0.0.0 \
    GENOME_CANVAS_PORT=8000 \
    PYTHONUNBUFFERED=1

EXPOSE 8000
VOLUME ["/data", "/app/.genomecanvas"]

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)" || exit 1

CMD ["python", "server.py"]
