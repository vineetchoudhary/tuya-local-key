# Tuya Local Key

Tuya Local Key retrieves Smart Life / Tuya device local keys using QR-code login. You do not need a Tuya IoT developer account, cloud project, Access ID, or Access Secret.

## Usage

1. Start the app.
2. Open the web UI from the Home Assistant sidebar.
3. Enter your Smart Life user code.
4. Scan the QR code with the Smart Life app.
5. Tap Confirm login in the app.
6. View, copy, filter, refresh, or export your devices.

## Finding Your User Code

In the Smart Life app, go to Me > Settings > Account and Security > User Code.

## Configuration

Use the app configuration page to choose the QR scheme. The default is `smartlife`; switch to `tuyaSmart` if scanning or confirmation does not work for your account.

The web UI caches the device list for 24 hours. Click Refresh to get the latest list from Tuya.

## Security

The app exposes device `localKey` values after login. Keep access restricted to trusted Home Assistant users.

The direct `8000/tcp` port is disabled by default. Use Home Assistant ingress unless you intentionally enable the direct port.

Ingress access is already authenticated by Home Assistant. If you enable the direct port (or otherwise reach the app outside ingress), set both `AUTH_USERNAME` and `AUTH_PASSWORD` in the app Configuration to require a login.

> **Warning:** `AUTH_USERNAME` and `AUTH_PASSWORD` are **ignored when the app is opened through Home Assistant ingress** (the sidebar panel). Ingress already authenticates you, and the browser cannot pass Basic Auth credentials through the ingress proxy, so no login prompt appears there. It only applies if you expose the direct port.
