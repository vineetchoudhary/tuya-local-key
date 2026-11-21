# Tuya Local Key

Retrieve Smart Life / Tuya device local keys from Home Assistant using QR-code login.

Open the app from the Home Assistant sidebar, enter your Smart Life user code, scan the QR code with the Smart Life app, and confirm login.

The app keeps its login, device list, scan results, and change history in `/data`. It reuses the saved device list for up to 3 days. Use Refresh to fetch it sooner.
