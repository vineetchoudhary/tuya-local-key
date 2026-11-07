# Using the App

Log in with your Smart Life account using a QR code. No Tuya IoT developer account or cloud project is needed.

## Finding Your User Code

In Smart Life, go to **Me > Settings > Account and Security > User Code**.

## Scanning the QR Code

In Smart Life, tap **+ > Scan**, scan the QR code, and confirm login. The confirmation may say "Home Assistant" because this tool uses Home Assistant's Tuya app registration.

If the QR code expires, start the login again.

## Web Login Flow

1. Enter your Smart Life user code.
2. Scan the QR code and confirm login in Smart Life.
3. Select a device to view its details or copy its local integration settings.

The device list is saved for 3 days. Use **Refresh** to fetch the latest details from Tuya. See [Caching and Changes](caching-and-changes.md).

## Filtering by Home

Choose **Filter > Home** to show devices from one home. Filters combine with search. **Clear all** resets filters and search.

## Filtering by Room

Choose **Filter > Room** to show devices in a room within the selected home. **No room** means unassigned, while **Unknown room** means room information could not be loaded. Room groups are not supported.

Use **Refresh** after changing room assignments in Smart Life.

## Filtering by Status and Device Category

Use **Filter > Status** or **Filter > Device category** to narrow the list. Cloud status and local network status are separate, so a cloud-offline device may still be reachable locally. See [network statuses](network-scanning.md#what-the-status-column-shows).

## Excluding Devices

1. Open **Filter > Select devices**.
2. Select the devices to hide, then choose **Exclude selected**.
3. To bring them back, open **Filter > Excluded devices**, select them, and choose **Restore selected**.

Selecting a gateway does not select its sub-devices. Exclusions are saved per account and shared across browsers using the same installation. They hide devices in this app without removing them from Tuya or network scans.

CSV export follows the current view, filters, and sort order.

## Device Details

Select a device to open its options:

| Option | Purpose |
|---|---|
| Set up in… | Connection settings for tuya-local, localtuya, and tinytuya. |
| Local network | Local IP, protocol version, and connection checks. |
| Current values | Saved cloud values, local readings, and data point definitions. |
| Device information | Identity, keys, connectivity, and timestamps. |
| Raw JSON | View or copy the saved device record. |
| Download a support report | Save diagnostics for a bug report. |

Use **Back** or Escape to return to the previous page. Local keys are hidden by default and can still be copied while hidden.

Web timestamps use your timezone. CSV exports and the CLI use UTC.

### Set up a local integration

Open **Set up in…**, choose your integration, and copy the settings into it. If the local IP or protocol is missing, use **Local network** to find them. For connection problems, use [Fix connection](network-scanning.md#fix-connection).

### Download a Support Report

Support reports include app versions, device capabilities, and connection diagnostics. Keys, credentials, personal identifiers, and current readings are excluded. Downloading a report uses saved information without contacting the device.

To report a problem:

1. Select the affected device and choose **Download a support report**.
2. Open a [bug report](https://github.com/vineetchoudhary/tuya-local-key/issues/new?template=bug_report.yml).
3. Attach the downloaded `tuya-local-key-support-*.json` file in the **Support report** field.

If you cannot generate a report, explain why and include the app version or image tag.

### Read Current Values

Open **Current values > Read from device** to compare the saved cloud snapshot with a local reading. These snapshots are taken at different times, so their values may differ. **Value definitions** contains the data point IDs, types, units, and ranges supplied by Tuya.

Local reads use the saved IP, protocol, and key. If these are missing, open **Local network** first. Sub-devices also need an identified gateway and a Node ID.

Reads are manual and do not change device settings. **Read again** takes another snapshot. Local readings are cleared when you refresh the device list, reload the page, or log out.

## Bluetooth Devices

Bluetooth-only devices have no LAN local key in Tuya's response. Use **Raw JSON** to inspect the data Tuya provided.

### Bluetooth Devices Behind a Gateway

For a Bluetooth device paired through a gateway, use the gateway's IP, protocol, and local key together with the sub-device's **Node ID**. The key belongs to the gateway and cannot be used for a direct Bluetooth connection.
