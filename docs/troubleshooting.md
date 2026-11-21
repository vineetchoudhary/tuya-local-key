# Troubleshooting

- Terminal QR will not scan: open the saved `tuya-login-qr.png` instead.
- Login timed out or QR expired: start the login again and scan promptly.
- **Your login expired**, or you are sent back to the login page: scan the QR code again.
- No devices found: confirm the devices are paired in the Smart Life app under the same account.
- Local key shows `-`: the device is Bluetooth-only, or it is a gateway whose key Tuya lists on its sub-devices. Run **Scan network** to find a gateway's key. See [Bluetooth Devices](usage.md#bluetooth-devices) and [Gateways](network-scanning.md#gateways).
- A device stopped working with a local integration: its local key, IP, or protocol may have changed. Use **Refresh** and **Scan network**, then read the change summary. See [Change Detection](caching-and-changes.md#change-detection).
- Local connection fails: use **Local network > Fix connection** in the device panel.
- A scan finds no devices: let the app's host reach TCP port 6668 on your IoT VLAN or subnet. See [Networks and Firewalls](network-scanning.md#networks-and-firewalls).
- **Could not reach Tuya** with the list still shown: that is the saved snapshot. Try **Refresh** again.
- Change history asks you to check its configuration: set `CHANGE_HISTORY` to `on` or `off` and `HISTORY_RETENTION_DAYS` from 1 to 3,650, and keep the history files apart from the other data files. Saved history is kept until then.
- Reporting a device issue: attach a [support report](usage.md#download-a-support-report) to your bug report.
