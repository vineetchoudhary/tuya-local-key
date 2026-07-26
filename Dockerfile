FROM python:3.12-slim

ARG BUILD_VERSION=dev

LABEL \
    io.hass.version="${BUILD_VERSION}" \
    io.hass.type="app" \
    io.hass.arch="aarch64|amd64"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SESSION_FILE=/data/session.json \
    PORT=8000

WORKDIR /app

# Dependencies first (better layer caching). cryptography/pillow ship wheels,
# so no build toolchain is needed on slim.
COPY requirements.txt requirements-web.txt ./
RUN pip install -r requirements-web.txt

# App
COPY tuya_devices.py app.py ./
COPY templates ./templates

# Run as a non-root user; /data is a writable volume for the cached session.
RUN useradd --create-home appuser \
    && mkdir -p /data && chown appuser:appuser /data
USER appuser

EXPOSE 8000
VOLUME ["/data"]

# Production WSGI server (waitress) — threaded, handles the login polling fine.
CMD ["sh", "-c", "exec waitress-serve --listen=0.0.0.0:${PORT:-8000} --threads=8 app:app"]
