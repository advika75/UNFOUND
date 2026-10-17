#!/usr/bin/env bash
set -e

# Default port to 8000 or use environment variable PORT
PORT="${PORT:-8000}"
HOST="${HOST:-0.0.0.0}"
WORKERS="${WORKERS:-1}"

echo "Starting UNFOUND FastAPI backend on ${HOST}:${PORT} (workers: ${WORKERS})..."
exec uvicorn backend.app:app --host "${HOST}" --port "${PORT}" --workers "${WORKERS}"
