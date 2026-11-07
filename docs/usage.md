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

Selecting a device row opens a side panel with a list of options. The first screen contains navigation only. Choose an option to view or work with that device:

| Option | Contents |
|---|---|
| Set up in… | Copyable connection settings for tuya-local, localtuya and tinytuya. |
| Local network | Saved IP, protocol and connection results, with rechecking and troubleshooting when needed. |
| Current values | Saved data points, local IDs and definitions, plus manual LAN readings beside the cloud snapshot. |
| Device information | Identity, keys, connectivity, account, timeline, and additional fields reported by Tuya. |
| Raw JSON | The complete saved device record, with key visibility and a copy button. |
| Download a support report | Save a JSON file containing diagnostic details with private information removed. |

The panel keeps the device name visible on every page. Use **Back** or Escape to return to the previous page, then back to the options list. Each page keeps its scroll position and any input draft during that visit. Closing and reopening a device starts at the options list with local keys hidden. Excluded devices offer **Restore device** in the options list.

**Device information** groups the SDK fields into Identity, Connectivity, Account, Timeline, and Other fields. Identity includes the device ID, UUID, local key, product ID/name, model and icon path. Connectivity includes cloud status, the cloud-reported WAN IP, local-control support, sub-device and gateway IDs, time zone and coordinates. Account includes user, owner and asset IDs. Timeline includes paired and updated times in your local timezone, with UTC and raw epochs below. Extra SDK fields remain available under Other fields.

The DP IDs in **Current values** are the mappings local integrations such as localtuya and tuya-local need. Expand **Value definitions** on that page for types, read/write access, units, ranges and other supplied definitions. Saved values remain available before a local read, and a device without saved data points shows an empty-state explanation. **Raw JSON** opens on its own page.

The device table shows the columns you scan most. UUID, category, IP address, and the last-paired time live in the panel. CSV export and `--json` still include every field.

Timestamps in the web UI use your browser's timezone, so the same list reads differently on different machines. CSV export and the CLI stay in UTC.

### Set up a local integration

Choose **Set up in…** from the device's options list to open the setup view for tuya-local, localtuya and tinytuya.

The separate **Local network** view shows connection problems and the last local result. When the connection details are complete and no failure needs attention, it shows **Recheck connection** instead of an IP form, repair button, or scan button. **Connection checked** means the device answered with the current local key; **Gateway checked** applies only to a sub-device's gateway. The gateway's report of the sub-device is shown separately. Protocol 3.1 status replies do not verify the key, so they show **Device found**. Older saved scans and results from before a key change need another check.

When details are missing or a connection fails, choose **Fix connection** for a [guided connection check](network-scanning.md#fix-connection). It walks through the saved details, an IP check or network scan, and suggested recovery steps. Each screen offers the relevant next action. Use the panel's **Back** button or Escape to return through the steps.

### Download a Support Report

Choose **Download a support report** from a device's options to save a JSON file for troubleshooting. The download includes the app, Python and library versions; product and category details; data point IDs, types and definitions; saved connection evidence; and the latest relevant network check's outcome and timings. A sub-device's gateway information is kept separate from that child's status. Protocol 3.1 is never labelled as key verification.

The report uses the existing snapshot. Downloading it does not refresh Tuya, start a network scan, or read the device. Local keys, credentials, device/account IDs, personal device/home/room names, addresses, coordinates, current values and raw logs are excluded. Extra SDK fields are omitted by default. The file stays in your browser's download location; share it yourself when asking for help.

To include it in a bug report:

1. Select the affected device and choose **Download a support report** in its side panel.
2. Open a [bug report](https://github.com/vineetchoudhary/tuya-local-key/issues/new?template=bug_report.yml).
3. Drag the downloaded `tuya-local-key-support-*.json` file into the **Support report** field, or use its attachment button.

If the app cannot open, no device is listed, or you use the CLI, explain why a report cannot be generated and include the app version or image tag instead.

### Read Current Values

Open a device's options and choose **Current values**, then **Read from device**. Opening the view starts no read. The button requests status over the local network using the saved IP, protocol, and local key, then closes the connection. The read leaves device settings and Tuya's saved cloud data unchanged.

The view keeps **Cloud (saved)** and **Local (device reply)** in separate columns, with a timestamp for each source. These are snapshots taken at different times. The cloud timestamp describes when the app fetched the snapshot, not when the device originally measured a value. Choose **Read again** for another local snapshot. Values returned only over LAN appear by their data point ID, and cloud fields without a local mapping remain visible without an invented local value.

Names and value definitions come from the saved Tuya details. **Value definitions** includes data types, read/write access, ranges, units, scaling and reporting details when supplied. Numeric units and scaling are applied only when an explicitly raw mapping supplies them. The original raw value remains visible and copyable. Other readings keep their raw representation.

If the IP or protocol is missing, use **Open Local network** to find them first. Sub-devices require an identified gateway and a Node ID. The read requests that child's data points through the gateway, and never substitutes the gateway's own values or its online report. A device or gateway that does not return data points shows an explanation instead of cloud values labelled as local.

Reads are manual and use short socket timeouts. A scan and a read cannot run together, and no continuous polling is started. A connection failure explains that another local client may be connected or the device's IP may have changed. A failed retry keeps the previous reading and its timestamp, clearly labelled as the previous result.

Local readings are temporary browser state. They are not added to the encrypted caches, CSV export, or cloud device record. Refreshing the device list, reloading the page, or logging out clears the displayed reading. An in-flight reply is discarded if its account or connection details have changed.

## Bluetooth Devices

Bluetooth-only devices have no local key to display. This is not a bug in this tool. Tuya's device-sharing API does not return a `local_key` for them. The SDK builds each device record straight from Tuya's response, so a field Tuya omits is simply absent. You can confirm this on the **Raw JSON** page, where the `local_key` line is missing entirely rather than empty.

Tuya documents `local_key` as the ["unique encrypted key of the specified device over LAN"](https://developer.tuya.com/en/docs/cloud/9f0ad495f5?id=Kfpa9zysx687w). A Bluetooth-only device has no LAN presence, and Tuya's [Bluetooth pairing docs](https://developer.tuya.com/en/docs/app-development/activator_ble_ios?id=Kcy2u7zj5hwkf) describe the connection as point-to-point between phone and device, so the app-side API this tool logs into has no LAN key to hand out.

### Bluetooth Devices Behind a Gateway

Pairing a Bluetooth device to a Tuya Bluetooth or SigMesh gateway makes a local key appear in its returned data. That key belongs to the gateway, not to the device: Tuya returns the same value on each sub-device under the gateway, because sub-devices are reached over LAN through the gateway. The table shows that key on the gateway's row once identified, and the sub-device's Node ID on its own row. The key remains available as **Gateway local key** in the sub-device's details panel. It will not authenticate a direct Bluetooth connection to the device.
