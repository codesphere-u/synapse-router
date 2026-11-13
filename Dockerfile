FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOST=0.0.0.0 \
    PORT=8000

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/

RUN pip install --no-cache-dir . && \
    groupadd --system --gid 10001 appuser && \
    useradd --system --uid 10001 --gid appuser --create-home --shell /bin/sh appuser && \
    chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os, socket, urllib.request; port = int(os.environ.get('PORT', '8000')); s = socket.socket(); s.settimeout(1.0); up = s.connect_ex(('127.0.0.1', port)) == 0; s.close(); urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=3).read() if up else __import__('synapse_router')"

ENTRYPOINT ["synapse-router"]
CMD ["mcp"]
