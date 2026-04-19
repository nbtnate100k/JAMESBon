# One worker only: Telegram long-polling cannot run in multiple processes with the same token.
web: gunicorn pluxo_backend:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 120
