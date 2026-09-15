# syntax=docker/dockerfile:1
#
# One container, no external services (§2, §12.3).
#   docker build -t kalliope .
#   docker run -p 8000:8000 -v kalliope-data:/data --env-file .env kalliope

# ---------------------------------------------------------------- frontend
FROM node:22-alpine AS frontend

WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund 2>/dev/null || npm install --no-audit --no-fund

COPY frontend/ ./
RUN npm run build

# ----------------------------------------------------------------- backend
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATA_DIR=/data \
    UV_SYSTEM_PYTHON=1 \
    UV_LINK_MODE=copy

# libgomp1 is required by PyMuPDF's rendering path; curl backs the healthcheck.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 curl \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY backend/pyproject.toml ./
RUN uv pip install --system --no-cache -r pyproject.toml

COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./alembic.ini
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh

# The built console is served by FastAPI from here — no second process, no
# reverse proxy.
COPY --from=frontend /build/dist ./app/static

RUN chmod +x /usr/local/bin/entrypoint.sh \
 && useradd --create-home --uid 10001 kalliope \
 && mkdir -p /data \
 && chown -R kalliope:kalliope /app /data

USER kalliope
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
