# Tuya Local Key

Retrieve Smart Life / Tuya device local keys from Home Assistant using QR-code login.

Open the app from the Home Assistant sidebar, enter your Smart Life user code, scan the QR code with the Smart Life app, and confirm login.

The app stores its cached login session in the app data volume at `/data/session.json`.

The web UI caches the device list for 24 hours. Click Refresh to get the latest list from Tuya.
