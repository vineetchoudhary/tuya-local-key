// Model.
import { CHECK_NEEDED, LAN_BADGES, LAN_EXPLAIN, LAN_KEYS, STATUS_RANK } from "./constants.js";
import { localTime, utcStamp } from "./format.js";
import { badge, esc } from "./html.js";
import { appState } from "./state.js";

function lanStatus(d) {
  return LAN_BADGES[d.lan_status] ? d.lan_status : null;
}

// A sub-device whose gateway the scan didn't find: it says nothing about the
// sub-device itself, so it reads greyed out and sorts with "not found".
function gatewayMissed(d) {
  return d.lan_status === "via_gateway" && !d.local_ip;
}

// What a gateway the scan found reports about its sub-device: true (online),
// false (offline), or null when it didn't say.
function subReport(d) {
  return viaGateway(d) && d.local_ip && typeof d.lan_sub_online === "boolean" ? d.lan_sub_online : null;
}

// The gateway answering says nothing about the sub-device, so one it reports
// offline reads as offline and sorts with "unreachable".
function subOffline(d) {
  return subReport(d) === false;
}

function lanExplain(d) {
  if (gatewayMissed(d)) return "A sub-device: it is reached through its gateway, which the last scan didn't find.";
  if (d.lan_status === "ok" && String(d.protocol_version) === "3.1")
    return "Identified on the local network. Protocol 3.1 status replies do not verify the local key.";
  const report = subReport(d);
  if (report !== null)
    return `A sub-device: it is reached through its gateway, which reports it ${report ? "online" : "offline"}.`;
  return LAN_EXPLAIN[d.lan_status];
}

// What the Status badge says, so the filter matches it too.
function lanBadgeText(d) {
  return subOffline(d) ? "offline" : LAN_BADGES[d.lan_status].text;
}

function statusBadge(d) {
  const status = lanStatus(d);
  if (!status && needsCheck(d))
    return `<span class="badge warn" title="${esc(CHECK_NEEDED)}"><span class="badge-tag">LAN</span> check needed</span>`;
  if (!status) return badge(d.online);
  const when = localTime(d.epochs && d.epochs.lan_checked_at);
  const report = subReport(d);
  const title = `Checked on the local network${when ? ` ${when}` : ""}. ` +
    (report !== null ? `Its gateway reports it ${report ? "online" : "offline"}. ` : "") +
    `The cloud says ${d.online ? "online" : "offline"}.`;
  const cls = gatewayMissed(d) ? "muted" : subOffline(d) ? "off" : LAN_BADGES[status].cls;
  return `<span class="badge ${cls}" title="${esc(title)}">` +
    `<span class="badge-tag">LAN</span> ${lanBadgeText(d)}</span>`;
}

function statusRank(d) {
  const status = lanStatus(d);
  if (status) return gatewayMissed(d) ? STATUS_RANK.not_found
    : subOffline(d) ? STATUS_RANK.unreachable : STATUS_RANK[status];
  if (needsCheck(d)) return STATUS_RANK.check_needed;
  return d.online ? STATUS_RANK.cloud_online : STATUS_RANK.cloud_offline;
}

// For the header count: seen on the LAN (even if busy) counts as online.
function isOnline(d) {
  const status = lanStatus(d);
  if (!status) return !!d.online;
  if (status === "via_gateway") return !!d.local_ip && !subOffline(d);
  return ["ok", "busy", "key_mismatch"].includes(status);
}

// A sub-device has no protocol version of its own. The one the scan gives it
// is its gateway's, so the table leaves it out and the panel labels it.
function viaGateway(d) {
  return d.lan_status === "via_gateway";
}

function protocolCell(d) {
  if (isSubDevice(d)) {
    const version = d.protocol_version && (d.protocol_version + (d.device22 ? " with device22" : ""));
    const tip = version ? `Reached through its gateway, which uses protocol ${version}`
      : "A sub-device has no Tuya local protocol version of its own.";
    return `<span class="dash" title="${esc(tip)}">-</span>`;
  }
  if (!d.protocol_version) return '<span class="dash">-</span>';
  return esc(d.protocol_version) +
    (d.device22 ? '<span class="protocol-note muted">device22</span>' : "");
}

function gatewayIdOf(d) {
  // Tuya's explicit relationship takes precedence over a scan's association.
  const id = d.gateway_id || lanResult(d).gateway_id;
  return id && id !== d.id ? id : null;
}

function isGateway(d) {
  return appState.gatewayCategories.has(d.category) ||
    appState.deviceData.some(s => s.id !== d.id && gatewayIdOf(s) === d.id);
}

function isSubDevice(d) {
  return !isGateway(d) && (!!d.sub || !!gatewayIdOf(d) || viaGateway(d));
}

function lanResult(d) {
  return (appState.lanData.results || {})[d.id] || {};
}

// A gateway Tuya lists without a key answers to the one on its sub-devices.
// The scan names that sub-device (key_from) once the gateway answered to it.
function keySource(d) {
  if (d.local_key) return null;
  const from = lanResult(d).key_from;
  const sub = from && appState.deviceData.find(s => s.id === from);
  return sub && sub.local_key ? sub : null;
}

// A keyless gateway the last scan couldn't tell apart from the others.
function unnamedGateway(d) {
  return isGateway(d) && !d.local_key && !keySource(d) &&
    !appState.deviceData.some(s => s.id !== d.id && s.gateway_id === d.id);
}

function needsCheck(d) {
  return !lanStatus(d) && unnamedLeft().length > 0 && unnamedGateway(d);
}

function localKeyOf(d) {
  const source = keySource(d);
  return d.local_key || (source ? source.local_key : "");
}

// Folds lanData.results into deviceData, replacing what an earlier call added.
function applyLan() {
  for (const d of appState.deviceData) {
    for (const key of LAN_KEYS) delete d[key];
    if (d.epochs) {
      delete d.epochs.lan_checked_at;
      if (!Object.keys(d.epochs).length) delete d.epochs;
    }
    const r = (appState.lanData.results || {})[d.id];
    if (!r) continue;
    d.local_ip = r.ip || "";
    d.protocol_version = r.version || "";
    d.device22 = !!r.device22;
    d.lan_status = r.status || "";
    d.lan_checked_at = utcStamp(r.checked_at);
    if (r.checked_at) d.epochs = { ...(d.epochs || {}), lan_checked_at: r.checked_at };
    // For the CSV: a sub-device's IP and version are this gateway's.
    if (r.gateway_id) d.lan_gateway_id = r.gateway_id;
    if (typeof r.sub_online === "boolean") d.lan_sub_online = r.sub_online;
  }
}

// The gateways the last scan couldn't tell apart, less any a check has named
// since: their sub-devices now know their gateway.
function unnamedLeft() {
  const list = (appState.lanData.summary && appState.lanData.summary.unnamed_gateways) || [];
  return list.filter(g => !(g.sub_devices || []).some(id => (appState.lanData.results || {})[id] &&
    appState.lanData.results[id].gateway_id));
}

function setupConnection(d) {
  const sub = isSubDevice(d), gatewayId = sub ? gatewayIdOf(d) : null;
  const gateway = gatewayId && appState.deviceData.find(g => g.id === gatewayId);
  // Prefer the gateway's own scan. A child's result is useful when its gateway
  // was not listed, but never borrow an address from a different gateway.
  const childScan = lanResult(d);
  const sameGateway = !childScan.gateway_id || childScan.gateway_id === gatewayId;
  const local = sub && gateway && (gateway.local_ip || !sameGateway) ? gateway
    : sub && !sameGateway ? {} : d;
  return {
    sub, gatewayId, gateway, local,
    id: d.id || "",
    host: local.local_ip || "",
    key: (sub && gateway && localKeyOf(gateway)) || localKeyOf(d),
    version: String(local.protocol_version || ""),
    device22: !!local.device22,
    nodeId: d.node_id || "",
  };
}

function networkState(d) {
  const s = setupConnection(d), r = lanResult(s.local), target = s.sub ? s.gateway : d;
  const running = (appState.lanData.job && appState.lanData.job.state === "running" &&
    (appState.lanData.job.kind === "scan" || (target && appState.lanData.job.device_id === target.id))) ||
    (target && appState.panelCheck && appState.panelCheck.id === target.id && appState.panelCheck.kind === "pending");
  const failedAttempt = target && appState.panelCheck && appState.panelCheck.id === target.id && appState.panelCheck.kind === "error" &&
    (!r.checked_at || !appState.panelCheck.finishedAt || r.checked_at <= appState.panelCheck.finishedAt);
  const accepted = ["ok", ...(s.sub ? ["via_gateway"] : [])].includes(r.status) && r.key_current === true;
  let title = "Not checked", tone = "pending";
  const address = s.sub ? "gateway's" : "device's";
  let note = `Scan your network, or enter the ${address} local IP address manually below.`;
  if (running) {
    title = s.sub ? "Checking gateway" : "Checking connection";
    note = "The result will update when the check finishes.";
  } else if (s.sub && !target) {
    title = s.gatewayId ? "Gateway not listed" : "Gateway not identified"; tone = "attention";
    note = s.gatewayId ? "Refresh the device list to look for this sub-device's gateway."
      : "Identify the gateway to check this sub-device's local access.";
  } else if (!s.key && !isGateway(target || d)) {
    title = "Local key unavailable"; tone = "attention";
    note = "A local key is needed to check this device.";
  } else if (failedAttempt) {
    title = "Check failed"; tone = "attention";
    note = "Review the last attempt below, then try again.";
  } else if (["busy", "key_mismatch", "unreachable", "not_found"].includes(r.status)) {
    title = { busy: "Connection refused", key_mismatch: "Key not accepted", unreachable: "No reply", not_found: "Not found" }[r.status];
    tone = "attention";
    note = r.status === "key_mismatch" ? "Refresh the device list, then check again."
      : r.status === "busy" ? "Another local client may already be connected. Review the last result below, then try again."
      : `Scan the network again, or enter the ${address} local IP address manually below.`;
  } else if (["ok", "via_gateway"].includes(r.status) && s.version === "3.1") {
    title = "Device found";
    note = "Protocol 3.1 status replies identify the device but do not verify the local key.";
  } else if (accepted) {
    title = s.sub ? "Gateway checked" : "Connection checked"; tone = "ready";
    note = s.sub ? "The gateway answered with the current local key."
      : "The device answered with the current local key.";
  } else if (["ok", "via_gateway"].includes(r.status) && r.ip) {
    title = "Check again";
    note = "The saved result does not verify the current local key. Check again to update it.";
  } else if (!s.key && isGateway(target || d)) {
    note = "Check the gateway at its IP to identify which sub-device holds its local key.";
  }
  const child = lanResult(d);
  const childCurrent = child.key_current === true && child.ip === s.host && child.version === s.version &&
    (!s.gatewayId || child.gateway_id === s.gatewayId) && (!r.checked_at || child.checked_at >= r.checked_at);
  const childOnline = s.sub && childCurrent && typeof child.sub_online === "boolean" ? child.sub_online : null;
  return { s, r, target, title, tone, note, childOnline };
}

export {
  applyLan,
  gatewayIdOf,
  isGateway,
  isOnline,
  isSubDevice,
  keySource,
  lanBadgeText,
  lanExplain,
  lanStatus,
  localKeyOf,
  needsCheck,
  networkState,
  protocolCell,
  setupConnection,
  statusBadge,
  statusRank,
  unnamedGateway,
  unnamedLeft,
};
