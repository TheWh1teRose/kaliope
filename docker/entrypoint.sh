#!/bin/sh
# Container entrypoint: check the environment, migrate, then serve (§12.3).
set -eu

if [ -z "${APP_SECRET_KEY:-}" ]; then
  echo "kalliope: APP_SECRET_KEY is not set." >&2
  echo "  Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(48))\"" >&2
  echo "  Then pass it, e.g. --env-file .env (see .env.example)." >&2
  exit 78  # EX_CONFIG
fi

DATA_DIR="${DATA_DIR:-/data}"
if [ ! -w "$DATA_DIR" ]; then
  echo "kalliope: $DATA_DIR is not writable." >&2
  echo "  Mount a volume there, e.g. -v kalliope-data:/data" >&2
  exit 78
fi

mkdir -p "$DATA_DIR/artifacts" "$DATA_DIR/uploads" "$DATA_DIR/renders" "$DATA_DIR/zone_cache"

echo "kalliope: applying migrations"
alembic upgrade head

exec "$@"
