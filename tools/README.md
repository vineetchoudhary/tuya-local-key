# Tools

Developer scripts. Nothing here is copied into the Docker image or needed at runtime.

## Screenshots

`screenshots.py` regenerates every image the README embeds. It runs the real app against the fake account in `demo_fleet.py`, drives it with Playwright, and writes a light and a dark PNG per shot into `docs/screenshots/`.

```bash
python -m pip install -r requirements-dev.txt
python -m playwright install chromium
```

Chromium is optional — the script falls back to a system Edge or Chrome.

```bash
python tools/screenshots.py
```

```bash
python tools/screenshots.py filter details
```

| Shot | Files | Where the README uses it |
|---|---|---|
| `login` | `login-{light,dark}.png` | Login |
| `qr-login` | `qr-login-{light,dark}.png` | QR Login |
| `devices` | `devices-{light,dark}.png` | Device Table |
| `header-devices` | `header-devices-{light,dark}.png` | The preview at the top |
| `changes` | `changes-{light,dark}.png` | Change Summary |
| `details` | `details-{light,dark}.png` | Device Details |
| `filter` | `filter-{light,dark}.png` | Filtering |
