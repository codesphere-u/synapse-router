FROM python:3.12-slim

WORKDIR /app

# Copy metadata and source code
COPY pyproject.toml README.md ./
COPY src/ ./src/

# Install package
RUN pip install --no-cache-dir .

# Configuration for remote cloud hosting (Glama.ai, Fly.io, Docker)
ENV HOST=0.0.0.0
ENV PORT=8000

EXPOSE 8000

# Start HTTP/SSE remote MCP server
ENTRYPOINT ["synapse-router", "serve-http", "--host", "0.0.0.0", "--port", "8000"]
