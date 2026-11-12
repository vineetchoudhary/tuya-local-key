#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

PYTHON_BIN="${PYTHON_BIN:-}"
IMAGE_NAME="${IMAGE_NAME:-tuya-local-key}"
CONTAINER_NAME="${CONTAINER_NAME:-tuya-local-key}"
HOST_PORT="${HOST_PORT:-8000}"
QR_SCHEME="${QR_SCHEME:-smartlife}"
CHECK_FOR_UPDATES="${CHECK_FOR_UPDATES:-true}"
SESSION_VOLUME="${SESSION_VOLUME:-tuya-session}"
APP_URL="http://localhost:${HOST_PORT}"

AUTH_USERNAME="${AUTH_USERNAME:-admin}"
AUTH_PASSWORD="${AUTH_PASSWORD:-tuya-local-key}"

usage() {
  cat <<'EOF'
Usage: ./tests/test.sh [--skip-tests] [--auth] [--no-update-check]

Run tests, then build and start the local Docker image.

Options:
  --skip-tests       Skip local dependency installation and tests; build and run Docker directly.
  --auth             Enable Basic Auth using AUTH_USERNAME and AUTH_PASSWORD.
  --no-update-check  Disable all update checks in the Docker container.
  -h, --help         Show this help message.

Python:
  Requires Python 3.10 or newer. Uses a compatible .venv first, then looks
  for python3 or a versioned Python. Base interpreters get a test environment
  under .venv-test/. Set PYTHON_BIN to choose an interpreter explicitly.

Examples:
  ./tests/test.sh
  PYTHON_BIN=python3.12 ./tests/test.sh
  ./tests/test.sh --skip-tests
  ./tests/test.sh --skip-tests --auth
  ./tests/test.sh --skip-tests --auth --no-update-check
EOF
}

SKIP_TESTS=0
AUTH_ENABLED=0
DOCKER_AUTH_ARGS=()
CURL_AUTH_ARGS=()
for arg in "$@"; do
  case "$arg" in
    --skip-tests)
      SKIP_TESTS=1
      ;;
    --auth)
      AUTH_ENABLED=1
      DOCKER_AUTH_ARGS=(-e "AUTH_USERNAME=${AUTH_USERNAME}" -e "AUTH_PASSWORD=${AUTH_PASSWORD}")
      CURL_AUTH_ARGS=(-u "${AUTH_USERNAME}:${AUTH_PASSWORD}")
      ;;
    --no-update-check)
      CHECK_FOR_UPDATES=false
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'Unknown argument: %s\n' "$arg" >&2
      usage >&2
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

supports_python() {
  command -v "$1" >/dev/null 2>&1 &&
    "$1" -c 'import sys; sys.exit(sys.version_info < (3, 10))' >/dev/null 2>&1
}

select_python() {
  local candidate
  if [[ -n "$PYTHON_BIN" ]]; then
    require_command "$PYTHON_BIN"
    if ! supports_python "$PYTHON_BIN"; then
      printf 'Python 3.10 or newer is required by TinyTuya 1.21.1. PYTHON_BIN=%s is not compatible.\n' "$PYTHON_BIN" >&2
      exit 1
    fi
    return
  fi

  for candidate in "$ROOT_DIR/.venv/bin/python" python3 python3.12 python3.13 python3.14 python3.15 python3.11 python3.10; do
    if supports_python "$candidate"; then
      PYTHON_BIN="$candidate"
      return
    fi
  done
  printf 'Python 3.10 or newer is required by TinyTuya 1.21.1. Install it and rerun with PYTHON_BIN=python3.12 ./tests/test.sh.\n' >&2
  exit 1
}

prepare_test_python() {
  local python_version test_venv
  if ! "$PYTHON_BIN" -c 'import sys; sys.exit(sys.prefix == sys.base_prefix)'; then
    python_version="$("$PYTHON_BIN" -c 'import sys; print("%s.%s" % sys.version_info[:2])')"
    test_venv="$ROOT_DIR/.venv-test/$python_version"
    run "$PYTHON_BIN" -m venv "$test_venv"
    PYTHON_BIN="$test_venv/bin/python"
  fi
  log "Using $("$PYTHON_BIN" --version) at $PYTHON_BIN"
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

if [[ "$SKIP_TESTS" == 0 ]]; then
  select_python
fi
require_command docker

if [[ "$SKIP_TESTS" == 1 ]]; then
  log "Skipping local dependency installation and tests"
else
  prepare_test_python
  run "$PYTHON_BIN" -m pip install -r requirements-dev.txt -r requirements-web.txt
  run "$PYTHON_BIN" -m pytest \
    --cov=app \
    --cov=tuya_devices \
    --cov=lan_scan \
    --cov=device_cache \
    --cov-report=term-missing \
    --cov-report=html:htmlcov
  log "Coverage HTML report: ${ROOT_DIR}/htmlcov/index.html"
fi

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
  -e "CHECK_FOR_UPDATES=${CHECK_FOR_UPDATES}" \
  ${DOCKER_AUTH_ARGS[@]+"${DOCKER_AUTH_ARGS[@]}"} \
  "$IMAGE_NAME"

wait_for_app
open_browser

log "Running ${CONTAINER_NAME} at ${APP_URL}"
if [[ "$AUTH_ENABLED" == 1 ]]; then
  log "Basic Auth enabled. Sign in with ${AUTH_USERNAME} / ${AUTH_PASSWORD}"
fi
