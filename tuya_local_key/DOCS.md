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

## Security

The app exposes device `localKey` values after login. Keep access restricted to trusted Home Assistant users.

The direct `8000/tcp` port is disabled by default. Use Home Assistant ingress unless you intentionally enable the direct port.
