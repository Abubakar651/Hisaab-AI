# ============================================================================
# HisaabAI — Production Container for Google Cloud Run
# ============================================================================

FROM python:3.12-slim

# Prevent Python from writing .pyc files and buffer stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    APP_ENV=production

WORKDIR /app

# Install runtime dependencies (curl for container health probes)
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies first for optimal Docker layer caching
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code and assets
COPY main.py agent.py analytics.py database.py /app/
COPY templates/ /app/templates/
COPY static/ /app/static/

# Create a non-privileged user and switch to it for defense-in-depth
RUN useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app
USER appuser

# Expose default Cloud Run container port
EXPOSE 8080

# Container liveness check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8080}/healthz || exit 1

# Launch uvicorn listening on Cloud Run dynamically assigned $PORT
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1"]

