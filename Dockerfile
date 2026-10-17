# Multi-stage lightweight Dockerfile for UNFOUND FastAPI ONNX backend
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    ENVIRONMENT=production

WORKDIR /app

# Install minimal OS dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency definition
COPY backend/requirements-deploy.txt /app/backend/requirements-deploy.txt

# Install pinned Python dependencies
RUN pip install --no-cache-dir -r backend/requirements-deploy.txt

# Copy backend application code and startup script
COPY backend /app/backend

# Make startup script executable
RUN chmod +x /app/backend/start.sh

# Expose default port
EXPOSE 8000

# Run uvicorn via start script
CMD ["/app/backend/start.sh"]

