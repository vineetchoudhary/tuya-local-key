# CLI

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
| `--scan TARGETS` | Also find each device's local IP and protocol version, using your IoT VLAN or subnet, e.g. `--scan 192.168.2.0/24`. Adds `local_ip`, `protocol_version`, `device22` and `lan_status` to every output. A sub-device also gets `lan_gateway_id`, the gateway whose IP and version it has, and `lan_sub_online` when that gateway reports whether it is online. A gateway Tuya lists without a key gets `local_key` and `local_key_from` from its sub-device. See [Protocol Version](network-scanning.md). |
| `--relogin` | Ignore the cached session and scan a new QR code. |
| `--logout` | Delete the cached session and exit. |
| `--session PATH` | Use a different session-cache file. |
| `--scheme {tuyaSmart,smartlife}` | QR scheme prefix. |

See [Finding Your User Code](usage.md#finding-your-user-code) and [Scanning the QR Code](usage.md#scanning-the-qr-code) for login help.
