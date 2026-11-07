# Troubleshooting

- Terminal QR will not scan: open the saved `tuya-login-qr.png` instead.
- Login timed out or QR expired: start the login again and scan promptly.
- `session_invalid` or redirected back to login: the cached login expired. Scan the QR code again.
- No devices found: confirm the devices are paired in the Smart Life app under the same account.
- Local key shows `-`: the device is Bluetooth-only. See [Bluetooth Devices](usage.md#bluetooth-devices).
- A device stopped working with a local integration: its local key may have rotated. Click Refresh and read the change summary. See [Change Detection](caching-and-changes.md#change-detection).
- Local connection fails: use **Local network > Fix connection** in the device panel.
- "Could not reach Tuya" with the list still shown: that is the saved snapshot. Try Refresh again.
- Reporting a device issue: attach a [support report](usage.md#download-a-support-report) to your bug report.
