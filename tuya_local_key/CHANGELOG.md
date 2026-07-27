# Changelog

## 1.4
- Publishing GHCR images without Buildx provenance/SBOM attestation manifests.
- Local keys are now hidden by default in the web UI. Use the eye toggle in the Local Key header to reveal or hide all keys, while copy-to-clipboard continues to copy the real key value.

**Full Changelog**: https://github.com/vineetchoudhary/tuya-local-key/compare/v1.3...v1.4

## 1.3
- Fix HomeAssistant volume mount issue

**Full Changelog**: https://github.com/vineetchoudhary/tuya-local-key/compare/v1.2...v1.3

## 1.2

- Fixed Home Assistant installation.
- Added the Home Assistant app icon.

**Full Changelog**: https://github.com/vineetchoudhary/tuya-local-key/compare/v1.1...v1.2

## 1.1

- Added Home Assistant app support.
- Improved web UI local-key copy behavior.
- Hardened QR login polling and session cleanup.

**Full Changelog**: https://github.com/vineetchoudhary/tuya-local-key/compare/v1.0...v1.1

## 1.0

- Retrieve device localKey values using QR-code login from the Smart Life app.
- No Tuya IoT developer account, Access ID, or Access Secret required.
- Web UI for login, device listing, filtering, copying local keys, refreshing devices, logging out, and exporting CSV.
- CLI with the same QR-code login flow for terminal use.
- Session caching so repeat scans are not required until the login expires.
- Docker and Docker Compose support.
- Multi-architecture Docker images for linux/amd64 and linux/arm64.