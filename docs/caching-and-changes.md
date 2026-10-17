# Caching and Changes

## Device List Cache

The device list is cached for 3 days and stored next to your session file, so restarting the container or updating the Home Assistant app shows your devices immediately instead of re-fetching them from Tuya. Click Refresh at any time to pull the current list.

That cached list contains every local key in your account, so it is encrypted at rest with a key kept beside it:

| File | Contents |
|---|---|
| `devices.cache` | The encrypted device list. |
| `lan.cache` | The encrypted [network scan](network-scanning.md) results. |
| `cache.key` | The key that decrypts both. |

They are written readable only by the user the app runs as. Logging out deletes them all. Deleting the key is the deliberate part: any copy of `devices.cache` that survives somewhere else, in a backup or a volume snapshot, can never be read again.

This protects a cache file that leaks on its own. It is **not** protection against someone who can read the whole data directory, because the key sits next to the file it unlocks. That directory already holds `session.json`, whose tokens can fetch the same local keys from Tuya, so treat the directory itself as the secret either way.

Set `DEVICE_CACHE` to `off` to keep the device list in memory only and never write it to disk. The list is then fetched from Tuya again after every restart.

### When Tuya Cannot Be Reached

If a fetch fails, the saved list is shown instead of an error, labelled as a snapshot with its age.

The same applies when your login expires. Local keys do not expire with the login, so the saved list is still correct: it stays on screen, and the notice offers to log in again rather than dropping you at the login screen with nothing. Logging out is what clears the saved list.

## Saved Device Exclusions

[Device exclusions](usage.md#excluding-devices) are preferences, saved separately from the encrypted device and network caches in `device-preferences.json` beside `SESSION_FILE` (`/data` in Docker and Home Assistant).

The file contains excluded device IDs and a hashed account identity, with no local keys, login tokens, or device snapshots. It has owner-only file permissions. Logging out or setting `DEVICE_CACHE=off` keeps these preferences. Use **Restore all** in the excluded-devices view to clear the current account's exclusions.

The account identity uses the login's client ID, user code, and region endpoint, so a new QR login or token renewal preserves exclusions. A different user code or region has a separate preference set. The preferences are shared by browsers connected to the same installation.

The [home filter](usage.md#filtering-by-home) uses home names and device membership stored with the encrypted device list. Its selected value is a separate browser preference, scoped to the account. An older saved list remains usable without home metadata; Refresh fetches it from Tuya.

## Change Detection

Every refresh is compared against the list you saw before it, and anything that moved is summarised above the table.

- **Local key changed.** This is the one that matters. Tuya rotates a device's local key when the device is re-paired, and sometimes after a firmware update. Nothing announces it, so localtuya, tinytuya, or tuya-local simply stop decrypting that device. The summary names which key moved.
- **Devices added or removed.**
- **Devices renamed**, with the name they had before.

A [network scan](network-scanning.md) is compared against the scan before it in the same way, and its findings join the same summary:

- **Protocol version changed.** A firmware update can move a device from 3.3 to 3.4 or 3.5, and a local integration still set to the old version stops working.
- **Local IP changed**, with the old and new address.
- **Local key no longer accepted.** The device answered at its address but not to its key. If Tuya lists a new key for it, the summary says so. Otherwise, click Refresh to check.

Changed rows are badged in the table so they are findable in a long list, and the filter box matches the badge text: type `key changed` to narrow to just those. Selecting a device name in the summary opens its details panel, where the new key is ready to copy.

The summary is a comparison, so the first list after logging in never has one, and switching accounts does not report every device as new. Device-specific changes follow the selected active or excluded view. Excluding or restoring a device does not create a device-change event. Dismissing the summary hides it until the next refresh finds something. Key values never appear in the summary itself, only the fact that one changed.
