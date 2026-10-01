#!/usr/bin/env bash
# Runs ON the VM (as root), piped over SSH by .github/workflows/deploy.yml.
# Required env: IMAGE (SHA-tagged image ref), PROJECT_ID, REGISTRY_HOST
# Optional env: PORT_BIND (default: keep the existing binding, else 8000:8000)
set -euo pipefail

NAME=kalliope
log() { echo "[deploy] $*"; }

: "${IMAGE:?IMAGE not set}" "${PROJECT_ID:?PROJECT_ID not set}" "${REGISTRY_HOST:?REGISTRY_HOST not set}"

gcloud auth configure-docker "$REGISTRY_HOST" --quiet >/dev/null

# Keep the existing host binding (e.g. 127.0.0.1:8000 behind Caddy) unless overridden.
if [ -z "${PORT_BIND:-}" ]; then
  existing="$(docker port "$NAME" 8000/tcp 2>/dev/null | head -n1 || true)"
  case "$existing" in
    127.0.0.1:*) PORT_BIND="127.0.0.1:${existing##*:}:8000" ;;
    *)           PORT_BIND="8000:8000" ;;
  esac
fi
log "port binding: $PORT_BIND"

log "pulling $IMAGE"
docker pull "$IMAGE"

PREVIOUS_IMAGE="$(docker inspect --format '{{.Config.Image}}' "$NAME" 2>/dev/null || true)"
log "previous image: ${PREVIOUS_IMAGE:-none}"

# Secrets are read here and passed by name, so they never hit argv or a file.
APP_SECRET_KEY="$(gcloud secrets versions access latest --secret=app-secret-key --project="$PROJECT_ID")"
ANTHROPIC_API_KEY="$(gcloud secrets versions access latest --secret=anthropic-api-key --project="$PROJECT_ID")"
export APP_SECRET_KEY ANTHROPIC_API_KEY

run_container() {
  docker run -d --name "$NAME" --restart unless-stopped \
    -p "$PORT_BIND" \
    -v /var/lib/kalliope:/data \
    -e DATA_DIR=/data \
    -e APP_SECRET_KEY -e ANTHROPIC_API_KEY \
    "$1" >/dev/null
}

healthy() {
  for _ in $(seq 1 30); do
    if curl -fsS http://127.0.0.1:8000/api/health 2>/dev/null | grep -Eq '"status"[[:space:]]*:[[:space:]]*"ok"'; then
      return 0
    fi
    sleep 2
  done
  return 1
}

log "REMOVING old container $NAME (data in /var/lib/kalliope is kept)"
docker rm -f "$NAME" >/dev/null 2>&1 || true
run_container "$IMAGE"

if healthy; then
  log "OK: $IMAGE is healthy"
  exit 0
fi

log "FAILED: $IMAGE did not report status ok within 60s. Container logs:"
docker logs --tail 50 "$NAME" || true
if [ -n "$PREVIOUS_IMAGE" ]; then
  log "ROLLING BACK to $PREVIOUS_IMAGE"
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  run_container "$PREVIOUS_IMAGE" && log "previous container restarted" || log "ROLLBACK FAILED, $NAME is not running"
else
  log "no previous image to roll back to; $NAME is left running unhealthy"
fi
exit 1
