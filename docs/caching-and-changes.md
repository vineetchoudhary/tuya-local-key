# Caching and Changes

## Device List Cache

The device list is saved for **3 days** and kept across restarts. Use **Refresh** to fetch the latest details from Tuya. Set `DEVICE_CACHE=off` to keep the list in memory only.

The following files are stored beside the session file:

| File | Contents |
|---|---|
| `devices.cache` | Encrypted device list. |
| `lan.cache` | Encrypted network scan results. |
| `cache.key` | Key for both caches. |

Logging out deletes the saved login, caches, and encryption key. Protect the data directory and its backups, since they contain both the encrypted data and the key needed to read it.

### When Tuya Cannot Be Reached

If Tuya is unavailable or your login expires, the saved list remains visible with its age. Retry **Refresh** or log in again when prompted.

## Saved Device Exclusions

[Device exclusions](usage.md#excluding-devices) are saved per account in `device-preferences.json`. They are shared across browsers using the same installation and survive restarts and logout. Use **Restore all** in the excluded-devices view to clear them.

Home and room filter choices are remembered separately in your browser. Use **Refresh** to pick up changes made in Smart Life.

## Change Detection

The summary above the table highlights changes since the previous fetch or scan:

- Refreshed device lists show changed keys, renamed devices, and devices added or removed.
- Network scans show changed IPs, protocol versions, and keys that are no longer accepted.

Select a device in the summary to view its details. If a key or connection setting changed, copy the new value into your local integration. Search for `key changed` to find affected rows.

The first fetch has no comparison. Excluding or restoring devices does not count as a device change. Key values are kept out of the summary.
