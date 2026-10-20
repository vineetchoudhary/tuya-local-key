# Using the App

QR-code login uses Tuya's official [`tuya-device-sharing-sdk`](https://github.com/tuya/tuya-device-sharing-sdk) with Home Assistant's public device-sharing app registration. You do not need a Tuya IoT developer account, cloud project, Access ID, or Access Secret.

## Finding Your User Code

In the Smart Life app, go to Me > Settings > Account and Security > User Code.

## Scanning the QR Code

In the Smart Life app, tap + > Scan, point at the QR code, and tap Confirm login.

The app may ask you to confirm login for "Home Assistant". That is expected because this tool signs in through Home Assistant's Tuya app registration. Only confirm if you started the login.

The QR code expires within a minute or two. If it times out, start the login again.

## Web Login Flow

1. Enter your Smart Life user code.
2. Scan the QR code in the Smart Life app.
3. Tap Confirm login in the app.
4. View, filter, copy, refresh, and export your devices.
5. Select a device row to open its details panel.

The web UI caches the device list for 3 days and keeps it across restarts. Click Refresh to get the latest list from Tuya. See [Device List Cache](caching-and-changes.md#device-list-cache).

## Filtering by Home

- Choose **Filter > Home** to show only that home's devices. Home names and device membership come from the same Tuya SDK requests that fetch the device list. If Tuya lists a device under more than one home, it appears in each matching view.

- Select **Clear all** in filter view to reset all filters including excluded devices.

- The home filter combines with search and the active or excluded view. Home filter is remembered in this browser for the current account. Devices without known membership remain available through **All homes** and the unknown-home option when shown.

- Older saved lists do not contain home information. Click **Refresh** once to fetch the home names and membership. A failed refresh keeps the previous saved list.

## Filtering by Room

- Choose **Filter > Room** to show devices assigned to a room in Smart Life or Tuya. The choices follow the selected Home and combine with search and the active or excluded view. Changing Home resets a room choice that no longer applies. Room choices are remembered in this browser for the current account.

- **No room** shows devices Tuya confirms are unassigned. **Unknown room** shows devices whose room information could not be loaded, including excluded devices missing from the current list. A failed room lookup does not hide devices from All rooms or prevent the device list from loading.

- The SDK queries each device's room during a fresh device fetch. Room information is stored with the encrypted device cache. Switching filters or reloading a valid cache makes no additional room requests. Click **Refresh** to pick up room changes or upgrade an older saved list. Only rooms containing accessible devices can be discovered. 

- **Room groups** are not supported. Tuya's SDK returns only the room name for each device, so this tool cannot show a room hierarchy or list all rooms in a group.

## Filtering by Status and Device Category

- Choose **Filter > Status** for cloud online, offline, or unknown status, or a LAN result from the latest saved network scan or device check. Cloud and LAN are separate choices, a device can be cloud-offline while reachable on your network. LAN choices include reachable, busy, key mismatch, unreachable, not found, via gateway, offline as reported by a gateway, gateway not found, check needed, and not checked. A missing gateway does not establish that its sub-devices are offline. Choosing a LAN filter does not start a scan. Use **Scan network** or a device's **Check** action to update the results.

- Choose **Filter > Device category** for a category reported by Tuya. Common codes have readable names, such as **Socket (cz)** and **Light (dj)**, using [Tuya's category definitions](https://developer.tuya.com/en/docs/iot/standarddescription?id=K9i5ql6waswzq). Other codes remain individually selectable. **Unknown category** covers missing category information, including excluded devices absent from the current list. Category choices use the full account list so changing another filter does not remove them. A selected category stays selected if a refresh leaves no matching devices.

- Both filter choices are remembered per account in this browser and combine with Home, Room, search, exclusions, counts, change notices, and CSV export.

## Excluding Devices

Open **Filter > Select devices** when you want to exclude individual devices. Checkboxes appear only in selection mode. Click or tap a device row or its checkbox to select or deselect it; rows do not open device details in this mode. Copy buttons and gateway expand/collapse controls keep their own actions. Choose **Exclude selected** to save, or **Done** to leave selection mode without making changes. The header checkbox selects only the matching rows currently shown, including expanded sub-devices. It leaves collapsed sub-devices and gateways shown only for search context unselected. Selecting a gateway does not select its children.

After a device is excluded, choose **Filter > Excluded devices (N)** to review the excluded list. Enter selection mode through Filter and choose **Restore selected**, or restore all account exclusions. Search, all four filters, and sorting work in either view. Changing the search, a filter, or the view clears the selection. Devices Tuya no longer lists remain in the excluded view by ID, so their preference can still be cleared. After all exclusions are restored, returning to the main list hides the Excluded devices option. The Filter badge always counts all account exclusions, independently of search or the other selected filters.

CSV export follows the current view, search, and sort order. The excluded view downloads `tuya-local-key-excluded.csv`. Entries absent from the current device list are omitted because their device data is unavailable.

Exclusions are saved per account on the server and shared by browsers accessing that installation. They survive restarts, logging out and back into the same account, and disabling the device cache. A different account has its own exclusions. Renaming a device or changing its local key preserves exclusion; re-pairing it with a new ID makes it appear as a new device.

Exclusion changes visibility in this app. Tuya still returns the complete account list, and network scans still use all account devices. The scan summary labels those totals accordingly. Gateway connection details remain available to their active sub-devices, even when the gateway itself is excluded.

## Device Details

Selecting a device row opens a side panel with everything the device-sharing SDK reports for it:

| Section | Contents |
|---|---|
| Identity | Name, device ID, UUID, local key, category, product ID and name, model, icon path. |
| Connectivity | Cloud online status, the IP address Tuya reports (your WAN address, not the device's), local-control support, sub-device flag, node and gateway ids, time zone, coordinates. |
| Local network | What the last [network scan](network-scanning.md) found: local IP, protocol version, status, and when it was checked, with a Check button for this one device. |
| Account | User, owner, and asset ids. |
| Timeline | First paired, last paired, and status-updated times in your local timezone, with the UTC reading and the raw epoch below each one. |
| Data points | Every data point: local dp id, code, current value, type, read/write access, and value range. |
| Raw JSON | The complete device record, with a copy button. |

Fields Tuya returns that are not listed above appear under "Other fields", so nothing is hidden. The dp id shown next to each data point code is the mapping local integrations such as localtuya and tuya-local need.

The device table shows the columns you scan most. UUID, category, IP address, and the last-paired time live in the panel. CSV export and `--json` still include every field.

Timestamps in the web UI use your browser's timezone, so the same list reads differently on different machines. CSV export and the CLI stay in UTC.

### Set up a local integration

Choose **Set up in…** at the top of a device's details panel to open a separate setup view for tuya-local, localtuya and tinytuya.

The separate **Local network** view shows connection problems, the last local result, and an IP address you can edit and check. **Connection checked** means the device answered with the current local key; **Gateway checked** applies only to a sub-device's gateway. The gateway's report of the sub-device is shown separately. Protocol 3.1 status replies do not verify the key, so they show **Device found**. Older saved scans and results from before a key change need another check.

## Bluetooth Devices

Bluetooth-only devices have no local key to display. This is not a bug in this tool. Tuya's device-sharing API does not return a `local_key` for them. The SDK builds each device record straight from Tuya's response, so a field Tuya omits is simply absent. You can confirm this in the Raw JSON section of the details panel, where the `local_key` line is missing entirely rather than empty.

Tuya documents `local_key` as the ["unique encrypted key of the specified device over LAN"](https://developer.tuya.com/en/docs/cloud/9f0ad495f5?id=Kfpa9zysx687w). A Bluetooth-only device has no LAN presence, and Tuya's [Bluetooth pairing docs](https://developer.tuya.com/en/docs/app-development/activator_ble_ios?id=Kcy2u7zj5hwkf) describe the connection as point-to-point between phone and device, so the app-side API this tool logs into has no LAN key to hand out.

### Bluetooth Devices Behind a Gateway

Pairing a Bluetooth device to a Tuya Bluetooth or SigMesh gateway makes a local key appear in its returned data. That key belongs to the gateway, not to the device: Tuya returns the same value on each sub-device under the gateway, because sub-devices are reached over LAN through the gateway. The table shows that key on the gateway's row once identified, and the sub-device's Node ID on its own row. The key remains available as **Gateway local key** in the sub-device's details panel. It will not authenticate a direct Bluetooth connection to the device.
