# CLI

Requires Python 3.10 or newer. If `python3` is older, select another one with `PYTHON=python3.12 ./setup.sh`.

Set up the local environment:

```bash
./setup.sh
```

Run the CLI:

```bash
.venv/bin/python tuya_devices.py
```

Or install the `tuya-local-key` command into the virtualenv:

```bash
.venv/bin/python -m pip install -e .
.venv/bin/tuya-local-key
```

The first run asks for your Smart Life user code, prints a QR code in the terminal, saves `tuya-login-qr.png` as a fallback, waits for confirmation, and lists your devices. The session is cached at `~/.config/tuya-smartlife/session.json`.

| Flag | Description |
|---|---|
| `--user-code CODE` | Smart Life user code, instead of being prompted. |
| `--json` | Output raw JSON. |
| `--csv PATH` | Also write results to a CSV file. |
| `--scan TARGETS` | Also find local IPs and protocol versions on your IoT VLAN or subnet, or at device IPs, comma-separated, e.g. `--scan 192.168.2.0/24`. Adds `local_ip`, `protocol_version`, `device22`, and `lan_status` (`ok`, `via_gateway`, or `not_found`). Sub-devices also get `lan_gateway_id`, and `lan_sub_online` when their gateway reports it. A gateway Tuya lists without a key gets `local_key` and `local_key_from`. See [Network Scanning](network-scanning.md). |
| `--relogin` | Ignore the cached session and scan a new QR code. |
| `--logout` | Delete the cached session and exit. |
| `--session PATH` | Use a different session cache file. |
| `--qr-png PATH` | Where to save the QR code image. Defaults to `tuya-login-qr.png` in the current folder. |
| `--scheme {tuyaSmart,smartlife}` | QR scheme prefix. Default `smartlife`. |

See [Finding Your User Code](usage.md#finding-your-user-code) and [Scanning the QR Code](usage.md#scanning-the-qr-code) for login help.
