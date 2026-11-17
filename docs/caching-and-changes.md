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

## Persistent Change History

Open **Change history** after **Current values** in a device's options. History opens for that device in the side panel, keeping the device list in place. A removed device's name in a change notice also opens its history.

History records changes detected during fresh device refreshes and local network scans or checks. It includes names, device additions and removals, local-key availability and rotation, home and room assignments, gateway relationships, local IPs, protocol settings, and qualified connection-state changes. Gateway changes are recorded once and linked to affected sub-devices. A child is reported offline only when its gateway explicitly says so.

The first observation establishes a baseline. On upgrade, a valid saved device list can seed that baseline, so the next fresh refresh can detect changes from it. The first network check also imports trustworthy saved local observations where history has no baseline, using their original check times. These must still match the device's keys and relationships; existing history baselines are preserved. This allows the first recheck of a previously reachable device to record a new unreachable, busy, or key-mismatch result. The import itself creates no entries. Earlier events cannot be reconstructed. Cache reads, exclusions, display filters, and device data-point readings create no history entries. No extra cloud requests or automatic network scans are made for history.

Each entry shows when the app **detected** the change, its source, and the changed fields. **Before** and **After** values are visible without expanding the entry; key changes show only the fact that the key changed. Expand **Observation details** for precise timestamps, the previous observation time, and when individual values were last confirmed. The actual change may have happened between observations. Unknown cloud fields, unchecked addresses, incomplete checks, and failed checks at a guessed address do not establish a change. A later successful check compares addresses and protocols against the last confirmed connection even if intervening checks failed. Protocol 3.1 replies identify a responding device but do not authenticate its key.

History starts with its **Filters** section collapsed. Expand it to see **Changes** and **Source** on the first row, and **From** and **Through** on the second. Sources are **All Sources**, **Cloud**, and **Local**. Filters apply only to the open device, including changes inherited from its gateway. An active-filter count stays visible beside **Filters** even while collapsed. **Clear all** beside the Filters heading restores all changes and sources with no date limits; it keeps the disclosure in its current open or closed state and does not delete history. The device name appears once in the panel heading; entries inherited from a gateway identify that gateway. Historical names appear as values when the name itself changed, and dates use your browser's timezone. Raw keys, key fingerprints, login tokens, credentials, and device readings are excluded from history responses. **Back** and Escape return to the selected device's options. Reopening history during the same panel visit restores filters, loaded entries, expanded observation details, and scroll position.

### Storage and Retention

History and its minimal comparison baseline are saved together in `change-history.enc`, encrypted using a dedicated `history.key`. Raw local keys are never written to the journal; comparison fingerprints are private to the encrypted baseline. The files are stored beside `SESSION_FILE` by default. Use one web process per app data directory, as in the bundled Waitress deployment.

The default retention is **90 days**, with at most **10,000 entries** and a **16 MiB plaintext document limit**. The encrypted file is larger. Older events can be removed sooner to stay within these limits. Current comparison baselines remain available; removed-device records expire with retention. Retention runs during history access, recording, and hourly local maintenance after the journal is activated. An app that was stopped prunes its history on the next startup/access. Increasing retention cannot recover deleted entries.

History survives reloads, restarts, updates, expired cloud logins, and QR reauthentication to the same account. **Log out** or logging into a different account deletes the previous journal, baseline, and history key. Device exclusions retain their separate lifecycle.

- **Dismiss** hides the current change notice in that browser without deleting history.
- **Clear history** clears the open device's history, regardless of the selected filters. Other devices' history and all comparison baselines are preserved. Shared gateway events disappear from the cleared device's history while remaining available to the other devices they affected. New changes continue to be recorded. Already-running operations cannot restore the cleared entries.
- `CHANGE_HISTORY=off` stops recording and deletes the journal and its key when the app starts with that setting.
- `DEVICE_CACHE=off` removes saved history and keeps any new history in memory only. The History panel labels it as temporary.

### Recording Problems and Recovery

A recording error does not discard successfully fetched device data. The app reports the error and preserves its last saved comparison baseline. The next successful observation is compared with that baseline; intermediate changes during a storage failure or stopped app may be unavailable.

An unreadable journal, malformed contents, missing key, or unsupported schema is preserved. The History panel offers **Reset saved history** with an explicit confirmation. This recovery action resets the account's unreadable journal and key for every device, as explained in its dialog; it is separate from clearing a device's readable history. Restore the matching journal/key from backup first if you want to recover old entries. A malformed journal does not prevent refreshing current devices.

Treat the data directory and its backups as sensitive. Encryption keys live alongside their encrypted files. Restoring a Home Assistant or Docker backup can restore its older history too; clearing the current journal does not modify existing backups.
