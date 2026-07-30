#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

PYTHON_BIN="${PYTHON_BIN:-python3}"
IMAGE_NAME="${IMAGE_NAME:-tuya-local-key}"
CONTAINER_NAME="${CONTAINER_NAME:-tuya-local-key}"
HOST_PORT="${HOST_PORT:-8000}"
QR_SCHEME="${QR_SCHEME:-smartlife}"
SESSION_VOLUME="${SESSION_VOLUME:-tuya-session}"
APP_URL="http://localhost:${HOST_PORT}"

AUTH_USERNAME="${AUTH_USERNAME:-admin}"
AUTH_PASSWORD="${AUTH_PASSWORD:-tuya-local-key}"

# Enable Auth
AUTH_ENABLED=0
DOCKER_AUTH_ARGS=()
CURL_AUTH_ARGS=()
for arg in "$@"; do
  case "$arg" in
    --auth)
      AUTH_ENABLED=1
      DOCKER_AUTH_ARGS=(-e "AUTH_USERNAME=${AUTH_USERNAME}" -e "AUTH_PASSWORD=${AUTH_PASSWORD}")
      CURL_AUTH_ARGS=(-u "${AUTH_USERNAME}:${AUTH_PASSWORD}")
      ;;
    *)
      printf 'Unknown argument: %s\n' "$arg" >&2
      exit 1
      ;;
  esac
done

log() {
  printf '\n==> %s\n' "$*"
}

run() {
  log "$*"
  "$@"
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    printf 'Missing required command: %s\n' "$1" >&2
    exit 1
  fi
}

open_browser() {
  if command -v open >/dev/null 2>&1; then
    open "$APP_URL"
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$APP_URL" >/dev/null 2>&1 &
  else
    printf 'Open %s in your browser.\n' "$APP_URL"
  fi
}

wait_for_app() {
  if ! command -v curl >/dev/null 2>&1; then
    return 0
  fi

  log "Waiting for ${APP_URL}"
  for _ in {1..30}; do
    if curl -fsS ${CURL_AUTH_ARGS[@]+"${CURL_AUTH_ARGS[@]}"} "${APP_URL}/api/state" >/dev/null; then
      return 0
    fi
    sleep 1
  done

  printf 'Container started, but %s did not respond within 30 seconds.\n' "$APP_URL" >&2
}

cd "$ROOT_DIR"

require_command "$PYTHON_BIN"
require_command docker

run "$PYTHON_BIN" -m pip install -r requirements-dev.txt -r requirements-web.txt
run "$PYTHON_BIN" -m pytest \
  --cov=app \
  --cov=tuya_devices \
  --cov-report=term-missing \
  --cov-report=html:htmlcov
log "Coverage HTML report: ${ROOT_DIR}/htmlcov/index.html"

if docker container inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
  run docker rm -f "$CONTAINER_NAME"
else
  log "No existing ${CONTAINER_NAME} container to stop"
fi

if docker image inspect "$IMAGE_NAME" >/dev/null 2>&1; then
  run docker image rm -f "$IMAGE_NAME"
else
  log "No existing ${IMAGE_NAME} image to delete"
fi

run docker build -t "$IMAGE_NAME" .
run docker run -d \
  --name "$CONTAINER_NAME" \
  -p "${HOST_PORT}:8000" \
  -v "${SESSION_VOLUME}:/data" \
  -e "QR_SCHEME=${QR_SCHEME}" \
  ${DOCKER_AUTH_ARGS[@]+"${DOCKER_AUTH_ARGS[@]}"} \
  "$IMAGE_NAME"

wait_for_app
open_browser

log "Running ${CONTAINER_NAME} at ${APP_URL}"
if [[ "$AUTH_ENABLED" == 1 ]]; then
  log "Basic Auth enabled — sign in with ${AUTH_USERNAME} / ${AUTH_PASSWORD}"
fi