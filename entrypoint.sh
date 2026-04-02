#!/bin/sh
set -e

echo "Running database migrations..."
python manage.py migrate --no-input

# Seed initial data in the background if the database is empty (first deploy)
if [ "${SEED_ON_FIRST_DEPLOY:-true}" = "true" ]; then
    TEAM_COUNT=$(python -c "
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sports_edge.settings.production')
django.setup()
from sports.models import Team
print(Team.objects.count())
" 2>/dev/null || echo "0")

    if [ "$TEAM_COUNT" = "0" ]; then
        echo "No teams found — running initial data seed in background..."
        python manage.py seed_initial_data &
    else
        echo "Database already seeded ($TEAM_COUNT teams found)."
    fi
fi

echo "Starting gunicorn..."
exec gunicorn sports_edge.wsgi \
    --bind 0.0.0.0:${PORT:-8000} \
    --workers ${GUNICORN_WORKERS:-2} \
    --timeout 120 \
    --access-logfile - \
    --error-logfile -
