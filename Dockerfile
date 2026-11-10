FROM python:3.12-slim

WORKDIR /app

# Copy packaging metadata, license, and source code
COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/

# Install package in production mode
RUN pip install --no-cache-dir .

# Configuration for remote cloud hosting (Glama.ai, Fly.io, Docker)
ENV HOST=0.0.0.0
ENV PORT=8000

EXPOSE 8000

# Default entrypoint and command for Glama container test & local stdio pipes
ENTRYPOINT ["synapse-router"]
CMD ["mcp"]
