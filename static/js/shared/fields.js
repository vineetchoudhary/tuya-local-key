// Shared device field renderers.
import { FIELD_LABELS, MONO_FIELDS, TUYA_LOCAL_DEVICE22 } from "./constants.js";
import { localTime } from "./format.js";
import { badge, deviceLink, esc, renderLocalKey, renderLocalKeyToggle, renderNodeId } from "./html.js";
import { isSubDevice, keySource, lanExplain, lanStatus, localKeyOf, statusBadge } from "./model.js";
import { appState } from "./state.js";

function labelFor(key) {
  return FIELD_LABELS[key] || key.replace(/_/g, " ").replace(/^./, c => c.toUpperCase());
}

function dash() { return '<span class="dash">-</span>'; }

function section(title, body) {
  return `<h3 class="section-title">${esc(title)}</h3>${body}`;
}

function fieldList(d, keys, labels = {}) {
  return `<dl class="fields">` + keys.map(key =>
    `<dt title="${esc(key)}">${esc(labels[key] || labelFor(key))}</dt><dd>${fieldValue(d, key)}</dd>`
  ).join("") + `</dl>`;
}

function copyable(text, mono) {
  return `<button class="copyable${mono ? " mono" : ""}" type="button" ` +
    `data-copy="${esc(text)}" title="Copy">${esc(text)}</button>`;
}

// Local time reads first, with the server's UTC string and raw epoch below it.
function timeValue(d, key) {
  const epoch = d.epochs[key];
  const local = localTime(epoch);
  if (!local) return copyable(String(d[key]));
  const utc = [d[key], epoch].filter(v => v !== null && v !== undefined && v !== "");
  return copyable(local) + `<div class="field-sub">${utc.map(esc).join(" · ")}</div>`;
}

function fieldValue(d, key) {
  const value = d[key];
  if (key === "gateway_id") {
    const gateway = appState.deviceData.find(g => g.id === value);
    if (gateway) return deviceLink(gateway) + `<div class="field-sub">${copyable(value, true)}</div>`;
  }
  if (key === "local_key") {
    const source = keySource(d);
    return `<span class="key-heading">${renderLocalKey(localKeyOf(d), appState.panelKeyVisible)}${renderLocalKeyToggle("panel")}</span>` +
      (source ? `<div class="field-sub">Tuya lists this key on its sub-device ${deviceLink(source)}, not on ` +
        `the gateway. ${String(d.protocol_version) === "3.1" ? "Protocol 3.1 status replies do not verify the key." : "The gateway answered to it on the local network."}</div>` : "");
  }
  if (key === "node_id" && isSubDevice(d)) return renderNodeId(value);
  if (key === "online") return badge(value);
  if (key === "lan_status") {
    const status = lanStatus(d);
    return status ? statusBadge(d) + `<div class="field-sub">${esc(lanExplain(d))}</div>` : dash();
  }
  if (key === "protocol_version" && value) {
    const name = TUYA_LOCAL_DEVICE22[value];
    return copyable(String(value), true) + (d.device22
      ? `<div class="field-sub">Has the device22 quirk: choose <b>${esc(name || value)}</b> in ` +
        `tuya-local, or dev_type "device22" in tinytuya.</div>`
      : "");
  }
  if (value === null || value === undefined || value === "") return dash();
  if (d.epochs && key in d.epochs) return timeValue(d, key);
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "object") return `<code class="mono">${esc(JSON.stringify(value))}</code>`;
  return copyable(String(value), MONO_FIELDS.has(key));
}

export { copyable, dash, fieldList, section };
