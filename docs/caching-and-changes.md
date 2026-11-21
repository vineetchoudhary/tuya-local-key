# Caching and Changes

## Device List Cache

The device list is saved across restarts and reused for up to **3 days**. After that, the app fetches a new list from Tuya when you open it. Use **Refresh** to fetch it sooner. Set `DEVICE_CACHE=off` to keep it in memory only.

These files are stored beside the session file:

| File | Contents |
|---|---|
| `devices.cache` | Encrypted device list. |
| `lan.cache` | Encrypted network scan results. |
| `cache.key` | Key for both caches. |

Logging out deletes the saved login, the caches, and their key. A new QR login also deletes the caches, so scan again afterwards. Each key is stored beside its encrypted file, so protect the data directory and its backups.

### When Tuya Cannot Be Reached

If Tuya is unavailable or your login expires, the saved list stays visible with its age. Retry **Refresh**, or log in again when prompted.

## Saved Device Exclusions

[Device exclusions](usage.md#excluding-devices) are saved per account in `device-preferences.json`. Every browser using the same installation shares them, and they survive restarts and logout. **Restore all** in the excluded-devices view, or **Clear all** in **Filter**, clears them.

Filter choices are remembered in your browser for each account. Use **Refresh** to pick up changes made in Smart Life.

## Change Detection

The summary above the table shows changes since the previous fetch or scan:

- A refresh shows changed keys, renamed devices, and devices added or removed.
- A network scan shows changed IPs, protocol versions, and keys that are no longer accepted.

Select a device in the summary to view its details. If a key or connection setting changed, copy the new value into your local integration. Search for `key changed` to find affected rows.

The first fetch has no comparison. Excluding or restoring devices is not a change. Key values are never shown in the summary.

## Change History

Open **Change history** in a device's options to see the changes recorded for that device and its gateway. In a change notice, a removed device's name opens its history.

History records changes found by device refreshes from Tuya and by network scans or checks:

- Name, device added or removed, home, and room.
- Local key availability and rotation. Key values are never shown.
- Gateway and sub-device relationships. A gateway change is recorded once and also shown on its sub-devices.
- Local IP, protocol version, and connection status. A sub-device is recorded offline only when its gateway reports it offline.

Cache reads, exclusions, filters, and current value readings are not recorded. History makes no extra cloud requests or scans.

The first refresh and the first check set a baseline, so earlier changes cannot be shown. After an upgrade, the saved device list and scan results become that baseline.

Each entry shows when the app detected the change, its source, and the values before and after. The change itself may have happened earlier. A device is recorded as unreachable, busy, or rejecting its key only when its last known address was checked.

Use **Filters** to narrow entries by change, source, and date. **Clear all** resets the filters without deleting anything.

### Storage and Retention

History is encrypted in `change-history.enc`, with its own key in `history.key`, beside the session file. Local keys are never written to it. Run one app process per data directory.

By default, history is kept for 90 days, up to 10,000 entries and 16 MiB. Older entries may be removed sooner to stay within these limits. Use `HISTORY_RETENTION_DAYS` to change the number of days.

History survives restarts, updates, expired logins, and a new QR login to the same account.

- **Dismiss** hides a change notice in that browser. History is kept.
- **Clear history** clears the open device's history only. Other devices keep theirs, including shared gateway entries.
- **Log out**, or logging in to another account, deletes history and its key.
- `CHANGE_HISTORY=off` deletes history and its key when the app starts.
- `DEVICE_CACHE=off` deletes saved history and keeps new history in memory only.

### Recording Problems

If history cannot be saved, the device list still updates and the app shows an error. The next successful refresh or check compares with the last saved state, so changes in between may be missed.

An unreadable history file is kept. **Reset saved history** deletes it for every device. To recover old entries, restore the matching `change-history.enc` and `history.key` from a backup first.
