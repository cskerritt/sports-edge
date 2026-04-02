#!/bin/sh
set -e

echo "Running database migrations..."
python manage.py migrate --no-input

echo "Starting gunicorn..."
exec gunicorn sports_edge.wsgi \
    --bind 0.0.0.0:${PORT:-8000} \
    --workers ${GUNICORN_WORKERS:-2} \
    --timeout 120 \
    --access-logfile - \
    --error-logfile -
