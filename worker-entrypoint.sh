#!/bin/sh
set -e

if [ -z "$DATABASE_URL" ]; then
    echo "ERROR: DATABASE_URL is not set. The worker needs access to the same database as the web service."
    echo "In Railway, add a reference variable pointing to your Postgres service's DATABASE_URL."
    exit 1
fi

echo "Waiting for web service to run migrations..."
sleep 10

echo "Starting SportsEdge scheduler worker..."
exec python scheduler.py
