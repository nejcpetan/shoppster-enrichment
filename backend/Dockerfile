FROM python:3.12-slim

WORKDIR /app

# Install system dependencies for psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create config directory (will be overridden by volume mount in production)
RUN mkdir -p config

EXPOSE 8000

# Run with gunicorn managing a single uvicorn worker.
# Single worker required — SSE uses an in-memory event bus (multi-worker breaks real-time updates).
# Gunicorn restarts the worker automatically if it crashes, without taking down the whole server.
CMD ["gunicorn", "main:app",
     "--worker-class", "uvicorn.workers.UvicornWorker",
     "--workers", "1",
     "--bind", "0.0.0.0:8000",
     "--timeout", "0",
     "--keep-alive", "5",
     "--max-requests", "1000",
     "--max-requests-jitter", "100",
     "--log-level", "info"]
