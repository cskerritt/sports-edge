web: gunicorn sports_edge.wsgi --bind 0.0.0.0:$PORT --workers 2 --timeout 120
release: python manage.py migrate --no-input && python manage.py fix_contract_names --apply
