# Tuya Local Key

Tuya Local Key helps you retrieve the local keys for devices in your Smart Life / Tuya account, along with device ID, UUID, product details, category, IP address, online status, timestamps, and every data point the device reports.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/header-devices-dark.png">
  <source media="(prefers-color-scheme: light)" srcset="docs/screenshots/header-devices-light.png">
  <img alt="Tuya Local Key device list preview with 60 demo devices" src="docs/screenshots/header-devices-light.png">
</picture>

<br>

It uses QR-code login through Tuya's official [`tuya-device-sharing-sdk`](https://github.com/tuya/tuya-device-sharing-sdk), using Home Assistant's public device-sharing app registration. You do not need a Tuya IoT developer account, cloud project, Access ID, or Access Secret.

Use it as a self-hosted web UI with Docker or as a local CLI tool.

## Features

- Web UI for QR login, device listing, filtering, local key copy, refresh, logout, and CSV export.
- Device details panel with every field the device-sharing SDK returns, including current data point values, their specifications, and the local data point id mapping.
- CLI with the same QR login flow for terminal use.
- Session caching so you do not need to scan a QR code every time.
- Encrypted device-list cache that survives restarts, so the list loads without waiting on Tuya.
- Change detection on every refresh, including the local key rotations that silently break local integrations.
- Saved list stays readable when Tuya is unreachable or your login expires.
- Docker and Docker Compose support.
- Home Assistant app support.
- GHCR publishing workflow for multi-architecture images.

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

The included [docker-compose.yml](docker-compose.yml) runs the published GHCR image and stores the cached login session and device list in a named Docker volume.

Start the app:

```bash
docker compose up -d
```

Open the web UI:

```text
http://localhost:8000
```

If you prefer the `tuyaSmart` QR scheme instead of `smartlife`, edit `QR_SCHEME` in [docker-compose.yml](docker-compose.yml).

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

## Web Login Flow

1. Enter your Smart Life user code.
2. Scan the QR code in the Smart Life app.
3. Tap Confirm login in the app.
4. View, filter, copy, refresh, and export your devices.
5. Select a device row to open its details panel.

The web UI caches the device list for 3 days and keeps it across restarts. Click Refresh to get the latest list from Tuya. See [Device List Cache](#device-list-cache).

## Device Details

Selecting a device row opens a side panel with everything the device-sharing SDK reports for it:

| Section | Contents |
|---|---|
| Identity | Name, device ID, UUID, local key, category, product ID and name, model, icon path. |
| Connectivity | Online status, IP address, local-control support, sub-device flag, node and gateway ids, time zone, coordinates. |
| Account | User, owner, and asset ids. |
| Timeline | First paired, last paired, and status-updated times in your local timezone, with the UTC reading and the raw epoch below each one. |
| Data points | Every data point: local dp id, code, current value, type, read/write access, and value range. |
| Raw JSON | The complete device record, with a copy button. |

Fields Tuya returns that are not listed above appear under "Other fields", so nothing is hidden. The dp id shown next to each data point code is the mapping local integrations such as LocalTuya and tuya-local need.

The device table shows the columns you scan most; UUID, category, IP address, and the last-paired time live in the panel. CSV export and `--json` still include every field.

Timestamps in the web UI use your browser's timezone, so the same list reads differently on different machines. CSV export and the CLI stay in UTC.

## Device List Cache

The device list is cached for 3 days and stored next to your session file, so restarting the container or updating the Home Assistant app shows your devices immediately instead of re-fetching them from Tuya. Click Refresh at any time to pull the current list.

That cached list contains every local key in your account, so it is encrypted at rest with a key kept beside it:

| File | Contents |
|---|---|
| `devices.cache` | The encrypted device list. |
| `cache.key` | The key that decrypts it. |

Both are written readable only by the user the app runs as. Logging out deletes both. Deleting the key is the deliberate part: any copy of `devices.cache` that survives somewhere else, in a backup or a volume snapshot, can never be read again.

This protects a cache file that leaks on its own. It is **not** protection against someone who can read the whole data directory, because the key sits next to the file it unlocks. That directory already holds `session.json`, whose tokens can fetch the same local keys from Tuya, so treat the directory itself as the secret either way.

Set `DEVICE_CACHE` to `off` to keep the device list in memory only and never write it to disk. The list is then fetched from Tuya again after every restart.

### When Tuya Cannot Be Reached

If a fetch fails, the saved list is shown instead of an error, labelled as a snapshot with its age.

The same applies when your login expires. Local keys do not expire with the login, so the saved list is still correct: it stays on screen, and the notice offers to log in again rather than dropping you at the login screen with nothing. Logging out is what clears the saved list.

## Change Detection

Every refresh is compared against the list you saw before it, and anything that moved is summarised above the table.

- **Local key changed.** This is the one that matters. Tuya rotates a device's local key when the device is re-paired, and sometimes after a firmware update. Nothing announces it, so LocalTuya, tinytuya, or tuya-local simply stop decrypting that device. The summary names which key moved.
- **Devices added or removed.**
- **Devices renamed**, with the name they had before.

Changed rows are badged in the table so they are findable in a long list, and the filter box matches the badge text: type `key changed` to narrow to just those. Selecting a device name in the summary opens its details panel, where the new key is ready to copy.

The summary is a comparison, so the first list after logging in never has one, and switching accounts does not report every device as new. Dismissing it hides it until the next refresh finds something. Key values never appear in the summary itself, only the fact that one changed.

## Bluetooth Devices

Bluetooth-only devices show `-` in the Local Key column. This is not a bug in this tool. Tuya's device-sharing API does not return a `local_key` for them, so there is nothing to display. The SDK builds each device record straight from Tuya's response, so a field Tuya omits is simply absent. You can confirm this in the Raw JSON section of the details panel, where the `local_key` line is missing entirely rather than empty.

Tuya documents `local_key` as the ["unique encrypted key of the specified device over LAN"](https://developer.tuya.com/en/docs/cloud/9f0ad495f5?id=Kfpa9zysx687w). A Bluetooth-only device has no LAN presence, and Tuya's [Bluetooth pairing docs](https://developer.tuya.com/en/docs/app-development/activator_ble_ios?id=Kcy2u7zj5hwkf) describe the connection as point-to-point between phone and device, so the app-side API this tool logs into has no LAN key to hand out.

### Bluetooth Devices Behind a Gateway

Pairing a Bluetooth device to a Tuya Bluetooth or SigMesh gateway makes a local key appear. That key belongs to the gateway, not to the device: every sub-device under the same gateway shows the same value, because sub-devices are reached over LAN through the gateway. Treat it as the gateway's key. It will not authenticate a direct Bluetooth connection to the device.

## Configuration

| Environment variable | Default | Description |
|---|---|---|
| `SESSION_FILE` | `/data/session.json` | Path where the cached login session is stored. |
| `QR_SCHEME` | `smartlife` | QR prefix. Use `tuyaSmart` if scanning or confirmation does not work for your account. |
| `PORT` | `8000` | Server port used by the Flask development server. The Docker image listens on `8000`. |
| `AUTH_USERNAME` | _(unset)_ | Username for optional HTTP Basic Auth. Login is required only when **both** `AUTH_USERNAME` and `AUTH_PASSWORD` are set. Ignored under Home Assistant ingress. |
| `AUTH_PASSWORD` | _(unset)_ | Password for optional HTTP Basic Auth. Ignored under Home Assistant ingress. |
| `DEVICE_CACHE` | `on` | Set to `off` to keep the device list in memory only instead of storing it. See [Device List Cache](#device-list-cache). |
| `DEVICE_CACHE_FILE` | `devices.cache` beside `SESSION_FILE` | Path of the encrypted device-list cache. |
| `DEVICE_CACHE_KEY_FILE` | `cache.key` beside `SESSION_FILE` | Path of the key that decrypts the device-list cache. |

> Security note: by default the web UI has no authentication, anyone who can reach the port can see device `localKey` values. Set **both** `AUTH_USERNAME` and `AUTH_PASSWORD` to require a login. This is recommended whenever the port is reachable beyond localhost. Basic Auth sends credentials unencrypted over plain HTTP, so still keep it on a trusted network or behind a TLS reverse proxy, and do not expose it directly to the internet. On Home Assistant, ingress already authenticates access, so these credentials are **ignored for ingress requests**. Set them only if you enable the direct port access and want a separate login there. The device list is also stored on disk, encrypted; see [Device List Cache](#device-list-cache) for what that does and does not protect.

## CLI

Set up the local environment:

```bash
./setup.sh
```

Run the CLI:

```bash
.venv/bin/python tuya_devices.py
```

Install the CLI command into the virtualenv:

```bash
.venv/bin/python -m pip install -e .
.venv/bin/tuya-local-key
```

First run prompts for your Smart Life user code, prints a QR code in the terminal, saves `tuya-login-qr.png` as a fallback, waits for confirmation, and then lists devices. The session is cached at `~/.config/tuya-smartlife/session.json`.

| Flag | Description |
|---|---|
| `--user-code CODE` | Provide the Smart Life user code instead of being prompted. |
| `--json` | Output raw JSON. |
| `--csv PATH` | Also write results to a CSV file. |
| `--relogin` | Ignore the cached session and scan a new QR code. |
| `--logout` | Delete the cached session and exit. |
| `--session PATH` | Use a different session-cache file. |
| `--scheme {tuyaSmart,smartlife}` | QR scheme prefix. |

## Finding Your User Code

In the Smart Life app, go to Me > Settings > Account and Security > User Code.

## Scanning the QR Code

In the Smart Life app, tap + > Scan, point at the QR code, and tap Confirm login.

The app may ask you to confirm login for "Home Assistant". That is expected because this tool signs in through Home Assistant's Tuya app registration. Only confirm if you started the login.

The QR code expires within a minute or two. If it times out, start the login again.


## Demo Screenshots

### Login

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/login-dark.png">
  <source media="(prefers-color-scheme: light)" srcset="docs/screenshots/login-light.png">
  <img alt="Login screen" src="docs/screenshots/login-light.png">
</picture>

### QR Login

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/qr-login-dark.png">
  <source media="(prefers-color-scheme: light)" srcset="docs/screenshots/qr-login-light.png">
  <img alt="QR login screen" src="docs/screenshots/qr-login-light.png">
</picture>

### Device Table

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/devices-dark.png">
  <source media="(prefers-color-scheme: light)" srcset="docs/screenshots/devices-light.png">
  <img alt="Device table with 60 demo devices" src="docs/screenshots/devices-light.png">
</picture>

### Change Summary

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/changes-dark.png">
  <source media="(prefers-color-scheme: light)" srcset="docs/screenshots/changes-light.png">
  <img alt="Change summary above the device table naming a rotated local key, an added device, a removed device, and a renamed device, with the matching rows badged" src="docs/screenshots/changes-light.png">
</picture>

### Device Details

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/details-dark.png">
  <source media="(prefers-color-scheme: light)" srcset="docs/screenshots/details-light.png">
  <img alt="Device details panel showing identity, connectivity, account, timeline, and data points" src="docs/screenshots/details-light.png">
</picture>

### Filtering

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/filter-dark.png">
  <source media="(prefers-color-scheme: light)" srcset="docs/screenshots/filter-light.png">
  <img alt="Filtered device table" src="docs/screenshots/filter-light.png">
</picture>

## Troubleshooting

- Terminal QR will not scan: open the saved `tuya-login-qr.png` instead.
- Login timed out or QR expired: start the login again and scan promptly.
- `session_invalid` or redirected back to login: the cached login expired; scan again.
- No devices found: confirm the devices are paired in the Smart Life app under the same account.
- Local key shows `-`: the device is Bluetooth-only. See [Bluetooth Devices](#bluetooth-devices).
- A device stopped working with a local integration: its local key may have rotated. Click Refresh and read the change summary. See [Change Detection](#change-detection).
- "Could not reach Tuya" with the list still shown: that is the saved snapshot. Try Refresh again.
