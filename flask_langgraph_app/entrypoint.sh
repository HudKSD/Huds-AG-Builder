#!/usr/bin/env sh
set -eu

echo "[flask-langgraph-es-chat] Starting Gunicorn with gevent worker"
exec gunicorn -k gevent -w "${GUNICORN_WORKERS:-1}" -b "0.0.0.0:${APP_PORT:-8000}" app:app
