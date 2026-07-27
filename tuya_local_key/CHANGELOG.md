# Changelog

## 1.3
- Fix HomeAssistant volume mount issue

## 1.2

- Fixed Home Assistant installation.
- Added the Home Assistant app icon.

## 1.1

- Added Home Assistant app support.
- Improved web UI local-key copy behavior.
- Hardened QR login polling and session cleanup.

## 1.0

- Retrieve device localKey values using QR-code login from the Smart Life app.
- No Tuya IoT developer account, Access ID, or Access Secret required.
- Web UI for login, device listing, filtering, copying local keys, refreshing devices, logging out, and exporting CSV.
- CLI with the same QR-code login flow for terminal use.
- Session caching so repeat scans are not required until the login expires.
- Docker and Docker Compose support.
- Multi-architecture Docker images for linux/amd64 and linux/arm64.