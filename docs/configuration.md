# Configuration

| Environment variable | Default | Description |
|---|---|---|
| `SESSION_FILE` | `/data/session.json` | Path of the saved login. From source, the default is `~/.config/tuya-smartlife/session.json`. |
| `QR_SCHEME` | `smartlife` | QR prefix. Use `tuyaSmart` for the Tuya Smart app, or if scanning or confirmation does not work for your account. |
| `PORT` | `8000` | Port of the web UI in the Docker image and the Flask development server. |
| `AUTH_USERNAME` | _(unset)_ | Username for optional HTTP Basic Auth. Login is required only when **both** `AUTH_USERNAME` and `AUTH_PASSWORD` are set. Ignored under Home Assistant ingress. |
| `AUTH_PASSWORD` | _(unset)_ | Password for optional HTTP Basic Auth. Ignored under Home Assistant ingress. |
| `DEVICE_CACHE` | `on` | Set to `off` to keep the device list, scan results, and change history in memory only, and delete their saved copies. See [Device List Cache](caching-and-changes.md#device-list-cache). |
| `CHANGE_HISTORY` | `on` | Record [change history](caching-and-changes.md#change-history). Set to `off` to hide it and delete saved history and its key. An unrecognized value pauses recording. |
| `HISTORY_RETENTION_DAYS` | `90` | Days to keep history, from 1 to 3,650. History is also limited to 10,000 entries and 16 MiB. Other values, `0` included, pause recording and keep saved history until fixed. |
| `HISTORY_FILE` | `change-history.enc` beside `SESSION_FILE` | Path of the encrypted change history. Must differ from the other data files. |
| `HISTORY_KEY_FILE` | `history.key` beside `SESSION_FILE` | Path of the change history key. Must differ from the other data files. Back it up with `HISTORY_FILE`. |
| `DEVICE_CACHE_FILE` | `devices.cache` beside `SESSION_FILE` | Path of the encrypted device list. |
| `DEVICE_CACHE_KEY_FILE` | `cache.key` beside `SESSION_FILE` | Path of the key for the device list and scan results. |
| `LAN_CACHE_FILE` | `lan.cache` beside `SESSION_FILE` | Path of the encrypted scan results. |
| `DEVICE_PREFERENCES_FILE` | `device-preferences.json` beside `SESSION_FILE` | Path of account-specific [device exclusions](caching-and-changes.md#saved-device-exclusions). |
| `UPDATE_CACHE_FILE` | `update-check.json` beside `SESSION_FILE` | Path of the release-check cache. |
| `CHECK_FOR_UPDATES` | `true` | Checks GitHub for a new release once a day, without sending device or account data. Set to `false` to disable update checks and hide update controls. |
| `LAN_SUBNET` | _(unset)_ | Your IoT VLAN or subnet, e.g. `192.168.2.0/24`, to prefill the Scan network box before your first scan. Several can be comma-separated. See [Network Scanning](network-scanning.md). |

## Security

By default the web UI has no authentication, so anyone who can reach the port can see your local keys. Set **both** `AUTH_USERNAME` and `AUTH_PASSWORD` whenever the port is reachable beyond localhost. Basic Auth is unencrypted over plain HTTP, so use it on a trusted network or behind a TLS reverse proxy, and never expose it directly to the internet.

On Home Assistant, ingress already authenticates access, so these credentials are **ignored for ingress requests**. Set them only if you enable direct port access and want a separate login there.

The app refuses changes, such as logging out, that another website triggers in your browser. This also works behind a reverse proxy that rewrites the Host header. Scripts that send no `Origin` header are not affected.

The device list, scan results, and change history are encrypted on disk. The saved login (`session.json`) is not, so protect the data directory. See [Device List Cache](caching-and-changes.md#device-list-cache).
