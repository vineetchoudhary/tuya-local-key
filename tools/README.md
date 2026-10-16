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
| `changes` | `changes-{light,dark}.png` | [Change Summary](../docs/screenshots.md#change-summary) |
| `scan` | `scan-{light,dark}.png` | [Network Scan](../docs/screenshots.md#network-scan) |
| `details` | `details-{light,dark}.png` | [Device Details](../docs/screenshots.md#device-details) |
| `setup-tuya-local` | `setup-tuya-local-{light,dark}.png` | [tuya-local setup](../docs/screenshots.md#tuya-local) |
| `setup-localtuya` | `setup-localtuya-{light,dark}.png` | [README setup preview](../README.md#set-up-in) and [localtuya setup](../docs/screenshots.md#localtuya) |
| `setup-tinytuya` | `setup-tinytuya-{light,dark}.png` | [tinytuya setup](../docs/screenshots.md#tinytuya) |
| `local-network` | `local-network-{light,dark}.png` | [Local Network](../docs/screenshots.md#local-network) |
| `filter` | `filter-{light,dark}.png` | [Filtering](../docs/screenshots.md#filtering) |

Setup and Local network previews are cropped to the side panel so their values remain readable. Their height adapts to the content; the Local network preview runs the demo scan before capturing its checked connection.

## Dependency Updates

`update_dependencies.py` looks for new releases of the two libraries the app is built on, tinytuya and tuya-device-sharing-sdk, and moves `requirements.txt` and `pyproject.toml` to them. Each keeps its operator: `>=` stays a minimum, and `==` stays a pin.

```bash
python tools/update_dependencies.py check
```

```bash
python tools/update_dependencies.py apply tinytuya==1.21.0
```
