// Manual LAN readings are temporary browser state, separate from cloud data.
import { api } from "../shared/api.js";
import { $ } from "../shared/dom.js";
import { copyable, dash } from "../shared/fields.js";
import { localTime } from "../shared/format.js";
import { esc } from "../shared/html.js";
import { setupConnection } from "../shared/model.js";
import { appState } from "../shared/state.js";
import { dpRange, dpRows } from "./details.js";

let loadDevices, navigatePanel, renderPanel;
let lastRead = null, pending = null;

function signature(d) {
  const s = setupConnection(d);
  return JSON.stringify([appState.accountId, appState.devicesCachedAt, d.id, s.gatewayId, s.host, s.key, s.version, s.nodeId, s.device22]);
}

function missing(d) {
  const s = setupConnection(d);
  if (s.sub && !s.gateway) return "Identify this device's gateway in Local network first.";
  if (!s.key) return "A local key is needed to read this device. Bluetooth-only devices have no LAN key.";
  if (s.sub && !s.nodeId) return "This sub-device needs its Node ID. Refresh the device list to check for it.";
  if (!s.host || !s.version) return "Find the local IP and protocol in Local network first.";
  return "";
}

const ERRORS = {
  connection_failed: "Could not open a local connection. Another local client may be using the device, or its IP may have changed.",
  unreachable: "The device did not reply. Check its power, local IP, and network access.",
  key_or_protocol: "The reply could not be decoded. Check the local key and protocol in Local network.",
  device_mismatch: "The address replied as a different device. Recheck the local IP.",
  no_values: "The device returned no data points for this request. It may be asleep or may not support local status reads.",
  invalid_values: "The device returned values that could not be displayed. Try reading again.",
  scan_running: "A network check is running. Wait for it to finish, then read again.",
  read_running: "Another device read is running. Wait for it to finish, then try again.",
  scanner_unavailable: "The local network library is unavailable. Restart or update the app, then try again.",
  device_changed: "The connection details changed during the read. Read again with the latest details.",
  read_failed: "The local read could not finish. Try again, or check Local network.",
  not_logged_in: "Log in again before reading from the device.",
};

function renderValue(row, value, present, d) {
  if (!present) return dash();
  const text = typeof value === "object" ? JSON.stringify(value) : String(value);
  const raw = copyable(text, true);
  // Apply scaling only to the SDK's explicitly raw numeric mapping.
  const strategy = (d.local_strategy || {})[row.dpId] || {};
  let spec;
  try { spec = JSON.parse(row.values || "{}"); } catch (e) { spec = {}; }
  if (!spec || typeof spec !== "object") spec = {};
  if (typeof value === "number" && strategy.value_convert === "raw" && row.type === "Integer" &&
      Number.isInteger(spec.scale) && spec.scale >= 0 && spec.scale <= 9 && typeof spec.unit === "string" && spec.unit) {
    const scaled = value / 10 ** spec.scale;
    return `<span>${esc(`${scaled} ${spec.unit}`)}</span><small>raw ${raw}</small>`;
  }
  return raw;
}

function valuesSection(d) {
  const key = signature(d), s = setupConnection(d), unavailable = missing(d);
  const reading = pending && pending.key === key;
  const saved = lastRead && lastRead.key === key ? lastRead : null;
  const result = saved && saved.result;
  const dps = result && result.dps || {};
  const rows = dpRows(d), known = new Set(rows.map(row => String(row.dpId)));
  for (const dpId of Object.keys(dps)) if (!known.has(dpId)) rows.push({ dpId, code: `Data point ${dpId}`, hasValue: false });
  const running = appState.localReadPending || $("refreshBtn").disabled || !!(appState.lanData.job && appState.lanData.job.state === "running");
  let html = `<div class="values-card"><p class="values-intro">Read the values this device reports over your local network.</p>`;
  if (s.sub) html += `<p class="values-note">Read from this sub-device through its gateway. A gateway connection check alone does not read these values.</p>`;
  if (unavailable) {
    const needsNode = s.sub && s.gateway && s.key && !s.nodeId;
    html += `<p class="values-notice" role="status">${esc(unavailable)}</p>` +
      `<button type="button" class="values-read" data-values-action="${needsNode ? "refresh" : "network"}"` +
      `${needsNode && running ? " disabled" : ""}>${needsNode ? "Refresh device list" : "Open Local network"}</button>`;
  }
  else html += `<button type="button" class="primary values-read" data-values-action="read"${running ? " disabled" : ""}>` +
    `${reading ? "Reading…" : result ? "Read again" : "Read from device"}</button>`;
  if (reading) html += `<p class="values-note" role="status"><span class="spinner"></span> Waiting for a local reply…</p>`;
  if (saved && saved.error) html += `<p class="values-notice" role="status">${esc(ERRORS[saved.error] || ERRORS.read_failed)}` +
    (result ? " The previous reading is still shown below." : "") + `</p>`;
  html += `<div class="values-times"><p>Cloud snapshot: ${esc(localTime(appState.devicesCachedAt) || "Not available")}</p>` +
    `<p data-values-read-at>Local reading: ${esc(result ? localTime(result.read_at) : "Not read yet")}</p></div>`;
  if (rows.length) {
    html += `<table class="values-table"><thead><tr><th scope="col">Data point</th><th scope="col">Cloud <small>saved</small></th>` +
      `<th scope="col">Local <small>device reply</small></th></tr></thead><tbody>` + rows.map(row => {
        const dpId = String(row.dpId), hasLocal = Object.hasOwn(dps, dpId);
        const name = ((d.function || {})[row.code] || {}).name || row.code;
        return `<tr data-values-dp="${esc(row.dpId ?? row.code)}"><th scope="row">` +
          `<span class="values-code">${esc(name)}</span><small>${row.dpId === undefined ? "No local mapping" : `DP ${esc(row.dpId)}`}</small></th>` +
          `<td data-cloud-value>${renderValue(row, row.value, row.hasValue, d)}</td>` +
          `<td data-local-value>${renderValue(row, dps[dpId], hasLocal, d)}</td></tr>`;
      }).join("") + `</tbody></table>`;
    const specs = rows.map(row => {
      const meta = [row.type, { R: "read-only", W: "write-only", RW: "read/write" }[row.access],
        dpRange(row.values, row.type), row.reportType].filter(Boolean).join(" · ");
      return meta ? `<li data-values-definition="${esc(row.code)}"><b${row.hint ? ` title="${esc(row.hint)}"` : ""}>` +
        `${esc(row.code)}</b>: ${esc(meta)}</li>` : "";
    }).filter(Boolean);
    if (specs.length) html += `<details class="values-specs"><summary>Value definitions</summary><ul>${specs.join("")}</ul></details>`;
  } else html += `<p class="values-note">No saved data points. A local read may return values the cloud does not list.</p>`;
  if (s.version === "3.1") html += `<p class="values-note">Protocol 3.1 status replies do not verify the local key.</p>`;
  return html + `<p class="values-note values-footnote">One read per click. Readings are cleared when you refresh the device list, reload the page, or log out.</p></div>`;
}

function resetValues() {
  lastRead = null; pending = null; appState.localReadPending = false;
}

function repaintRead(deviceId) {
  renderPanel();
  const active = document.activeElement;
  if (appState.selectedId !== deviceId || appState.panelView !== "values" ||
      (active && active !== document.body && active !== $("valuesBody") && active.isConnected)) return;
  const control = $("valuesBody").querySelector("[data-values-action]");
  if (control && !control.disabled) control.focus({ preventScroll: true });
}

async function readValues(d) {
  if (appState.localReadPending || missing(d)) return;
  const key = signature(d), session = appState.accountGeneration, account = appState.accountId;
  const operation = { key };
  pending = operation; appState.localReadPending = true;
  const previous = lastRead && lastRead.key === key ? lastRead.result : null;
  lastRead = { key, result: previous, error: null };
  renderPanel();
  const response = await api("/api/lan/read", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ device_id: d.id, account_id: account }),
  });
  if (pending !== operation) return;
  pending = null; appState.localReadPending = false;
  if (session !== appState.accountGeneration || account !== appState.accountId) return;
  const current = appState.deviceData.find(device => device.id === d.id);
  if (!current || signature(current) !== key) { lastRead = null; renderPanel(); return; }
  const { ok, body } = response;
  if (body.error === "account_changed" || (ok && body.account_id !== account)) {
    lastRead = null; loadDevices(); return;
  }
  if (ok && body.status === "ok" && body.device_id === d.id && body.dps && typeof body.dps === "object" && !Array.isArray(body.dps)) {
    lastRead = { key, result: { dps: body.dps, read_at: body.read_at }, error: null };
  } else lastRead = { key, result: previous, error: body.error || body.status || "read_failed" };
  repaintRead(d.id);
}

let initialized = false;
function initValues(actions = {}) {
  if (initialized) return;
  initialized = true;
  ({ loadDevices, navigatePanel, renderPanel } = actions);
  $("valuesBody").addEventListener("click", async e => {
    const control = e.target.closest("[data-values-action]");
    if (!control || control.disabled) return;
    if (control.dataset.valuesAction === "network") { navigatePanel(appState.selectedId, "network", control); return; }
    if (control.dataset.valuesAction === "refresh") { await loadDevices("Updating device list from Tuya…", true, true); return; }
    const d = appState.deviceData.find(device => device.id === appState.selectedId);
    if (d) await readValues(d);
  });
}

export { initValues, resetValues, valuesSection };
