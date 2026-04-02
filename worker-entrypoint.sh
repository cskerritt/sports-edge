#!/bin/sh
set -e

echo "Waiting for web service to run migrations..."
sleep 10

echo "Starting SportsEdge scheduler worker..."
exec python scheduler.py
