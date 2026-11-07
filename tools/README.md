# Tools

Developer scripts. Nothing here is copied into the Docker image or needed at runtime.

## Screenshots

`screenshots.py` regenerates the images in the [README preview](../README.md) and the [screenshot gallery](../docs/screenshots.md). It runs the real app against the fake account in `demo_fleet.py` (with a stubbed network scan), drives it with Playwright, and writes a light and a dark PNG per shot into `docs/screenshots/`.

```bash
python -m pip install -r requirements-dev.txt
python -m playwright install chromium
```

Chromium is optional. The script falls back to a system Edge or Chrome.

```bash
python tools/screenshots.py
```

```bash
python tools/screenshots.py setup-tuya-local setup-localtuya setup-tinytuya local-network
```

| Shot | Files | Where it appears |
|---|---|---|
| `login` | `login-{light,dark}.png` | [Login](../docs/screenshots.md#login) |
| `qr-login` | `qr-login-{light,dark}.png` | [QR Login](../docs/screenshots.md#qr-login) |
| `devices` | `devices-{light,dark}.png` | [Device Table](../docs/screenshots.md#device-table) |
| `header-devices` | `header-devices-{light,dark}.png` | [README preview](../README.md) |
| `header-update` | `header-update-{light,dark}.png` | [Update Badge](../docs/screenshots.md#update-badge) |
| `update-available` | `update-available-{light,dark}.png` | [Update Available](../docs/screenshots.md#update-available) |
| `no-updates` | `no-updates-{light,dark}.png` | [No Updates Available](../docs/screenshots.md#no-updates-available) |
| `changes` | `changes-{light,dark}.png` | [Change Summary](../docs/screenshots.md#change-summary) |
| `scan` | `scan-{light,dark}.png` | [Network Scan](../docs/screenshots.md#network-scan) |
| `details` | `details-{light,dark}.png` | [Device Options](../docs/screenshots.md#device-options) |
| `device-information` | `device-information-{light,dark}.png` | [Device Information](../docs/screenshots.md#device-information) |
| `raw-json` | `raw-json-{light,dark}.png` | [Raw JSON](../docs/screenshots.md#raw-json) |
| `setup-tuya-local` | `setup-tuya-local-{light,dark}.png` | [tuya-local setup](../docs/screenshots.md#tuya-local) |
| `setup-localtuya` | `setup-localtuya-{light,dark}.png` | [localtuya setup](../docs/screenshots.md#localtuya) |
| `setup-tinytuya` | `setup-tinytuya-{light,dark}.png` | [tinytuya setup](../docs/screenshots.md#tinytuya) |
| `local-network` | `local-network-{light,dark}.png` | [Local Network](../docs/screenshots.md#local-network) |
| `fix-connection-details` | `fix-connection-details-{light,dark}.png` | [Fix connection details](../docs/screenshots.md#details) |
| `fix-connection-test` | `fix-connection-test-{light,dark}.png` | [Fix connection test](../docs/screenshots.md#test) |
| `fix-connection-result` | `fix-connection-result-{light,dark}.png` | [Fix connection next steps](../docs/screenshots.md#next-steps) |
| `current-values` | `current-values-{light,dark}.png` | [Current Values](../docs/screenshots.md#current-values) |
| `support-report` | `support-report-{light,dark}.png` | [Support Report](../docs/screenshots.md#support-report) |
| `filter` | `filter-{light,dark}.png` | [Filtering](../docs/screenshots.md#filtering) |

Device panel previews are cropped to the panel so their contents remain readable. Their height adapts to the content; Raw JSON keeps a scrollable viewport. Local network and the wizard result run a demo check. Current values uses a simulated local reply, and the support report preview exercises the real download with the fake account.

## Dependency Updates

`update_dependencies.py` looks for new releases of the two libraries the app is built on, tinytuya and tuya-device-sharing-sdk, and moves `requirements.txt` and `pyproject.toml` to them. Each keeps its operator: `>=` stays a minimum, and `==` stays a pin.

```bash
python tools/update_dependencies.py check
```

```bash
python tools/update_dependencies.py apply tinytuya==1.21.0
```
