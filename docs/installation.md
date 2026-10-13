# Installation

Choose Home Assistant, Docker, or a local source checkout. For terminal use, see the [CLI guide](cli.md).

## Home Assistant App

Home Assistant OS users can install Tuya Local Key as a Home Assistant app.

1. Go to Settings > Apps > App Store.
2. Open the menu in the top-right and choose Repositories.
3. Add this repository URL:

```text
https://github.com/vineetchoudhary/tuya-local-key
```

4. Install Tuya Local Key from the app store and open it from the sidebar.

The app uses Home Assistant ingress by default. The direct `8000/tcp` port is disabled unless you explicitly enable it in the app network settings.

## Web UI with Docker Compose

The included [docker-compose.yml](../docker-compose.yml) runs the published GHCR image and stores the cached login session and device list in a named Docker volume.

Start the app:

```bash
docker compose up -d
```

Open the web UI:

```text
http://localhost:8000
```

If you prefer the `tuyaSmart` QR scheme instead of `smartlife`, edit `QR_SCHEME` in [docker-compose.yml](../docker-compose.yml).

## Web UI with Docker

Run the published image directly:

```bash
docker run -d --name tuya-local-key -p 8000:8000 -v tuya-session:/data ghcr.io/vineetchoudhary/tuya-local-key:latest
```

Build and run locally:

```bash
docker build -t tuya-local-key .
docker run -d --name tuya-local-key -p 8000:8000 -v tuya-session:/data tuya-local-key
```

Then open `http://localhost:8000`.

## Web UI from Source

Set up the local environment, then add the web dependencies:

```bash
./setup.sh
.venv/bin/python -m pip install -r requirements-web.txt
```

Serve it with waitress, the same server the Docker image uses:

```bash
.venv/bin/waitress-serve --listen=127.0.0.1:8000 app:app
```

Then open `http://localhost:8000`. It shares the CLI's saved login in `~/.config/tuya-smartlife/session.json`. To reach it from other devices, listen on `0.0.0.0:8000` instead, and read the security note under [Configuration](configuration.md) first.
