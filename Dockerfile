# ---- Build stage: install dependencies ----
FROM python:3.12-slim AS builder

WORKDIR /app

# Install build dependencies for psycopg2, numpy, scipy, etc.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---- Production stage ----
FROM python:3.12-slim

WORKDIR /app

# Runtime dependency for psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY . .

# Collect static files at build time (uses dummy SECRET_KEY so it doesn't fail)
RUN SECRET_KEY=build-placeholder \
    DATABASE_URL=sqlite:///tmp/placeholder.db \
    python manage.py collectstatic --no-input

# Make entrypoints executable
COPY entrypoint.sh /entrypoint.sh
COPY worker-entrypoint.sh /worker-entrypoint.sh
RUN chmod +x /entrypoint.sh /worker-entrypoint.sh

# Non-root user for security
RUN adduser --disabled-password --gecos "" appuser \
    && mkdir -p /home/appuser \
    && chown appuser:appuser /home/appuser
USER appuser

EXPOSE 8000

CMD ["sh", "/entrypoint.sh"]
