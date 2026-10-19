// Constants.


// Timestamps the server sends as a UTC string plus a raw epoch under `epochs`.
const TIME_KEYS = ["create_time", "active_time", "update_time"];

// Per-device maps from the SDK; rendered as the data point list, not as fields.
const SPEC_KEYS = ["status", "function", "status_range", "local_strategy"];

const MONO_FIELDS = new Set(["id", "uuid", "local_key", "product_id", "model", "icon",
  "ip", "lat", "lon", "time_zone", "uid", "owner_id", "asset_id", "node_id", "gateway_id",
  "local_ip", "protocol_version"]);

const FIELD_LABELS = {
  name: "Name", id: "Device ID", uuid: "UUID", local_key: "Local key",
  category: "Category", product_id: "Product ID", product_name: "Product name",
  model: "Model", icon: "Icon path", online: "Cloud status", ip: "IP address (cloud)",
  local_ip: "Local IP", protocol_version: "Protocol version", lan_status: "Status",
  lan_checked_at: "Last checked",
  support_local: "Local control", sub: "Sub-device", node_id: "Node ID",
  gateway_id: "Gateway ID", set_up: "Set up", time_zone: "Time zone",
  lat: "Latitude", lon: "Longitude", uid: "User ID", owner_id: "Owner ID",
  asset_id: "Asset ID", biz_type: "Business type",
  create_time: "First paired", active_time: "Last paired", update_time: "Status updated",
};

// Anything Tuya returns that isn't listed here lands in "Other fields".
const DETAIL_SECTIONS = [
  { title: "Identity", keys: ["name", "id", "uuid", "local_key", "category", "product_id", "product_name", "model", "icon"] },
  { title: "Connectivity", keys: ["online", "ip", "support_local", "sub", "node_id", "gateway_id", "set_up", "time_zone", "lat", "lon"] },
  { title: "Account", keys: ["uid", "owner_id", "asset_id", "biz_type"] },
  { title: "Timeline", keys: ["create_time", "active_time", "update_time"] },
];

// ---- LAN scan results ----
// What applyLan() adds to each device, so the filter, the panel and the CSV
// export all see them like any other field.
const LAN_KEYS = ["local_ip", "protocol_version", "device22", "lan_status", "lan_checked_at",
  "lan_gateway_id", "lan_sub_online"];

const LAN_BADGES = {
  ok:           { cls: "on",    text: "reachable" },
  via_gateway:  { cls: "on",    text: "via gateway" },
  busy:         { cls: "warn",  text: "busy" },
  key_mismatch: { cls: "off",   text: "key mismatch" },
  unreachable:  { cls: "off",   text: "unreachable" },
  not_found:    { cls: "muted", text: "not found" },
};

const LAN_EXPLAIN = {
  ok: "Answered on port 6668 to its local key.",
  via_gateway: "A sub-device: it is reached through its gateway, at the gateway's IP and protocol version.",
  busy: "Refused the connection at its last known IP. A device already connected to another local client (Home Assistant, say) refuses new ones. The IP may also have changed.",
  key_mismatch: "Answered at its last known IP, but not to its local key. The key may have changed, so try Refresh. Another device may also have taken that IP.",
  unreachable: "Didn't answer at its last known IP.",
  not_found: "No scanned address answered to its local key.",
};

// tuya-local's names for a version with the device22 quirk.
const TUYA_LOCAL_DEVICE22 = { "3.3": "3.22", "3.4": "3.42", "3.5": "3.52" };

// Status column order: reachable on the LAN first, never seen on it last.
const STATUS_RANK = { ok: 0, via_gateway: 1, cloud_online: 2, check_needed: 2.5, busy: 3,
                      key_mismatch: 4, unreachable: 5, not_found: 6, cloud_offline: 7 };

const CHECK_NEEDED = "The last scan found this gateway's key, but not which of your gateways is " +
  "at which address. Check it at its IP.";

// ---- what changed since the previous list ----
// A rotated key is the change that silently breaks localtuya/tinytuya/tuya-local
// setups, so it outranks a rename on the same row and leads the summary.
// A LAN scan adds its own: a moved protocol version or IP breaks a local
// integration as surely as a new key does, so they rank just below it.
const CHANGE_BADGES = {
  key_changed: "key changed", local_key_failed: "key failed", version_changed: "version changed",
  ip_changed: "IP changed", added: "new", renamed: "renamed",
};

// Manual local setup: values are derived here, without changing device records,
// scan results, CSV exports, or any integration's settings.
const SETUP_TOOLS = [
  { id: "tuya-local", name: "tuya-local", repo: "make-all/tuya-local" },
  { id: "localtuya", name: "localtuya", repo: "xZetsubou/hass-localtuya" },
  { id: "tinytuya", name: "tinytuya", repo: "jasonacox/tinytuya" },
];

const LOCAL_PROTOCOLS = ["3.1", "3.2", "3.3", "3.4", "3.5"];

export {
  CHANGE_BADGES,
  CHECK_NEEDED,
  DETAIL_SECTIONS,
  FIELD_LABELS,
  LAN_BADGES,
  LAN_EXPLAIN,
  LAN_KEYS,
  LOCAL_PROTOCOLS,
  MONO_FIELDS,
  SETUP_TOOLS,
  SPEC_KEYS,
  STATUS_RANK,
  TIME_KEYS,
  TUYA_LOCAL_DEVICE22,
};
