# Configuration

| Environment variable | Default | Description |
|---|---|---|
| `SESSION_FILE` | `/data/session.json` | Path where the cached login session is stored. |
| `QR_SCHEME` | `smartlife` | QR prefix. Use `tuyaSmart` if scanning or confirmation does not work for your account. |
| `PORT` | `8000` | Port the web UI listens on, in the Docker image and in the Flask development server. |
| `AUTH_USERNAME` | _(unset)_ | Username for optional HTTP Basic Auth. Login is required only when **both** `AUTH_USERNAME` and `AUTH_PASSWORD` are set. Ignored under Home Assistant ingress. |
| `AUTH_PASSWORD` | _(unset)_ | Password for optional HTTP Basic Auth. Ignored under Home Assistant ingress. |
| `DEVICE_CACHE` | `on` | Set to `off` to keep the device list in memory only instead of storing it. See [Device List Cache](caching-and-changes.md#device-list-cache). |
| `CHANGE_HISTORY` | `on` | Record configuration changes from fresh cloud refreshes and network checks. Setting this to `off` hides Change history from device options and notices, and deletes saved history and its encryption key. |
| `HISTORY_RETENTION_DAYS` | `90` | Retain history for 1–3,650 days, subject to the 10,000-entry and file-size limits. |
| `HISTORY_FILE` | `change-history.enc` beside `SESSION_FILE` | Encrypted history and comparison baseline. Must be separate from the other app data files. |
| `HISTORY_KEY_FILE` | `history.key` beside `SESSION_FILE` | Encryption key dedicated to history. Keep it together with its matching encrypted file when backing up. |
| `DEVICE_CACHE_FILE` | `devices.cache` beside `SESSION_FILE` | Path of the encrypted device-list cache. |
| `DEVICE_CACHE_KEY_FILE` | `cache.key` beside `SESSION_FILE` | Path of the key that decrypts the device-list cache and the scan results. |
| `LAN_CACHE_FILE` | `lan.cache` beside `SESSION_FILE` | Path of the encrypted network scan results. |
| `DEVICE_PREFERENCES_FILE` | `device-preferences.json` beside `SESSION_FILE` | Path of account-specific [device exclusions](caching-and-changes.md#saved-device-exclusions), kept independently of the device cache. |
| `UPDATE_CACHE_FILE` | `update-check.json` beside `SESSION_FILE` | Path of the release-check cache. |
| `CHECK_FOR_UPDATES` | `true` | Set to `false` to disable all update checks and hide update controls. |
| `LAN_SUBNET` | _(unset)_ | Your IoT VLAN or subnet, to prefill the Scan network box, e.g. `192.168.2.0/24`. Several can be comma-separated. See [Protocol Version](network-scanning.md). |

## Security

By default the web UI has no authentication, so anyone who can reach the port can see device `localKey` values. Set **both** `AUTH_USERNAME` and `AUTH_PASSWORD` to require a login. This is recommended whenever the port is reachable beyond localhost. Basic Auth sends credentials unencrypted over plain HTTP, so keep it on a trusted network or behind a TLS reverse proxy, and do not expose it directly to the internet.

On Home Assistant, ingress already authenticates access, so these credentials are **ignored for ingress requests**. Set them only if you enable the direct port access and want a separate login there.

The device list is also stored on disk, encrypted. See [Device List Cache](caching-and-changes.md#device-list-cache) for what that does and does not protect.
