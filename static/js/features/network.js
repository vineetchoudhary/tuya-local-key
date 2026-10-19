// Network.
import { api } from "../shared/api.js";
import { LAN_BADGES } from "../shared/constants.js";
import { $ } from "../shared/dom.js";
import { fieldList } from "../shared/fields.js";
import { localTime, plural } from "../shared/format.js";
import { deviceLink, esc } from "../shared/html.js";
import { icon } from "../shared/icons.js";
import {
  applyLan,
  gatewayIdOf,
  isGateway,
  isSubDevice,
  keySource,
  lanStatus,
  networkState,
  setupConnection,
  unnamedGateway,
  unnamedLeft,
} from "../shared/model.js";
import { appState } from "../shared/state.js";

// Cross-feature actions are connected by app.js.
let exclusionError, indexChanges, indexDevices, loadDevices, render, renderPanel;

let lanPollTimer = null;

let lanWatch = 0;

async function loadLan() {
  const session = appState.accountGeneration, expectedAccount = appState.accountId;
  const { ok, body } = await api("/api/lan");
  if (!ok || session !== appState.accountGeneration) return false;
  if (expectedAccount && body.account_id !== expectedAccount) {
    // Another browser can replace the saved login between our paired device /
    // preference reads and this request. Never attach its LAN proof to these keys.
    if (typeof body.account_id === "string" && body.account_id) loadDevices();
    else exclusionError("Could not verify the account for network results. Refresh the device list to try again.");
    return false;
  }
  appState.lanData = {
    results: body.results || {}, summary: body.summary || null, changes: body.changes || null,
    changes_at: body.changes_at || null, targets: body.targets || "", job: body.job || null,
  };
  return true;
}

// Re-reads the LAN results and repaints everything that shows them.
async function refreshLan() {
  if (!(await loadLan())) return;
  applyLan(); indexChanges(); indexDevices(); render();
}

// Local IP and protocol version from the LAN scan, and a check for this device.
function lanSection(d) {
  const sub = isSubDevice(d), gateway = isGateway(d);
  const s = setupConnection(d), display = sub && s.local.id ? s.local : d;
  // A gateway Tuya lists without a key is checked with its sub-devices' keys.
  const keyless = !sub && !d.local_key && !gateway;
  // Unless sub-devices name it in gateway_id, nothing says which keys are its.
  const unnamed = unnamedGateway(d);
  const running = !!((appState.lanData.job && appState.lanData.job.state === "running") || (appState.panelCheck && appState.panelCheck.kind === "pending"));
  let html = "";
  if (lanStatus(display)) {
    const keys = ["lan_status", "local_ip", "protocol_version", "lan_checked_at"]
      .filter(key => key === "lan_status" || display[key]);
    const values = { ...display, epochs: {},
      lan_checked_at: localTime(display.epochs && display.epochs.lan_checked_at) || display.lan_checked_at };
    html += fieldList(values, keys, sub
      ? { lan_status: "Last result", local_ip: "Gateway IP", protocol_version: "Gateway protocol" } : { lan_status: "Last result" });
  }
  if (sub) {
    const id = gatewayIdOf(d);
    const via = id && appState.deviceData.find(g => g.id === id);
    if (!via && id) {
      html += `<p class="lan-note">A sub-device is reached through its gateway. Gateway ` +
        `<span class="mono">${esc(id)}</span> is not included in this device list. Refresh devices to look for it.</p>`;
    } else if (!via && d.local_ip) {
      html += `<p class="lan-note">A sub-device is reached through its gateway, which answered ` +
        `at ${esc(d.local_ip)} to this device's key. Tuya doesn't say which gateway that is. ` +
        `Check your gateway at that IP to find out.</p>`;
    } else if (via) html += `<p class="lan-note">Gateway: ${deviceLink(via)}.</p>`;
    else html += `<p class="lan-note">A sub-device is reached through its gateway. Check the gateway instead.</p>`;
    if (via) return html + lanCheckForm(via, s.host, true);
    const gateways = appState.deviceData.filter(isGateway);
    if (!id && gateways.length) html += `<h4>Choose a gateway to check</h4><ul class="network-gateways">` +
      gateways.map(g => `<li>${deviceLink(g)}</li>`).join("") + `</ul>`;
    return html;
  }
  if (unnamed) {
    const found = unnamedLeft();
    html += `<p class="lan-note">Tuya lists this gateway's local key on its sub-devices, not on the ` +
      `gateway, and doesn't say which sub-devices are its own. ` + (found.length
        ? `The last scan found gateways at these addresses, each with the sub-device whose key ` +
          `answered there. Check this one at its IP. When that leaves one gateway, it's checked too.`
        : `Use <b>Scan network</b>, or check it at its IP to find out which key it answers to.`) +
      `</p>`;
    if (found.length) {
      html += `<div class="lan-quick">` + found.map(g => {
        const subs = (g.sub_devices || []).map(id => {
          const sub = appState.deviceData.find(s => s.id === id);
          return sub ? sub.name || sub.id : id;
        }).join(", ");
        return `<button type="button" data-check-ip="${esc(g.ip)}"${running ? " disabled" : ""}>` +
          `Check at <span class="mono">${esc(g.ip)}</span> <span class="muted">(${esc(subs)})</span></button>`;
      }).join("") + `</div>`;
    }
  }
  if (keyless) {
    return html + `<p class="lan-note" data-network-check="local_key" data-state="missing">Tuya doesn't return a local key for this device, so it ` +
      `can't be checked on the local network. Bluetooth-only devices have none.</p>`;
  }
  return html + lanCheckForm(d, d.local_ip || "");
}

function lanCheckForm(d, ip, gateway = false) {
  const running = !!((appState.lanData.job && appState.lanData.job.state === "running") || (appState.panelCheck && appState.panelCheck.kind === "pending"));
  const note = appState.panelCheck && appState.panelCheck.id === d.id ? appState.panelCheck : null;
  let html = `<label class="network-check-label" for="networkCheckIP">${gateway ? "Enter gateway IP address" : "Enter local IP address"}</label>` +
    `<form class="lan-check" data-lan-check="${esc(d.id)}">` +
    `<input id="networkCheckIP" type="text" name="ip" value="${esc(ip)}" placeholder="e.g. 192.168.2.1" ` +
    `aria-label="IP address to check" autocomplete="off" spellcheck="false" required>` +
    `<button type="submit"${running ? " disabled" : ""}>` +
    `${running && ((appState.lanData.job && appState.lanData.job.device_id === d.id) ||
      (appState.panelCheck && appState.panelCheck.id === d.id && appState.panelCheck.kind === "pending")) ? "Checking…" : "Check"}</button></form>`;
  if (note) html += `<p class="lan-note${note.kind === "error" ? " scan-error" : ""}" aria-live="polite">${esc(note.text)}</p>`;
  return html;
}

function networkSection(d) {
  const n = networkState(d);
  const symbol = n.tone === "ready" ? "check" : n.tone === "attention" ? "attention" : "pending";
  const state = n.tone === "ready" ? "ready" : n.tone === "attention" ? "attention" : "pending";
  let child = "";
  if (n.s.sub) {
    const label = n.childOnline === false ? "Gateway reports offline" : n.childOnline === true ? "Gateway reports online" : "Sub-device status unknown";
    const detail = n.childOnline === false ? "Check the sub-device's power and connection to the gateway."
      : n.childOnline === true ? "The sub-device's own response has not been checked."
      : "Check the gateway to update its report. Setup will test the sub-device's own response.";
    child = `<div class="network-child-status" data-network-check="sub_device" data-state="${n.childOnline === false ? "attention" : "pending"}">` +
      `<p>${label}</p><span class="muted">${detail}</span></div>`;
  }
  const busy = (appState.lanData.job && appState.lanData.job.state === "running") || (appState.panelCheck && appState.panelCheck.kind === "pending");
  const refresh = !n.s.key || n.r.status === "key_mismatch" || (n.s.sub && !n.target);
  const refreshing = $("refreshBtn").disabled;
  return `<div class="network-card"><div class="network-summary" data-network-summary data-network-check="connection" ` +
    `data-state="${state}" data-tone="${n.tone}" role="status"><span class="network-symbol">${icon(symbol)}</span>` +
    `<div><h3>${esc(n.title)}</h3><p>${esc(n.note)}</p></div></div>` +
    `${child}${lanSection(d)}<div class="network-actions">` +
    `<button type="button" data-network-action="scan"${busy ? " disabled" : ""}>Scan network</button>` +
    (refresh ? `<button type="button" data-network-action="refresh"${refreshing ? " disabled" : ""}>${refreshing ? "Refreshing…" : "Refresh devices"}</button>` : "") +
    `</div></div>`;
}

// ---- scan network ----
function resetLan() {
  stopScanWatch();
  appState.lanData = { results: {}, summary: null, changes: null, changes_at: null, targets: "", job: null };
  appState.panelCheck = null;
  $("scanTargets").value = "";
  showScanBox(false);
}

function showScanBox(open) {
  $("scanBox").classList.toggle("hidden", !open);
  $("scanBtn").setAttribute("aria-expanded", String(open));
  if (open && !$("scanTargets").value) $("scanTargets").value = appState.lanData.targets || "";
}

function scanError(msg) {
  $("scanError").textContent = msg || "";
  $("scanError").classList.toggle("hidden", !msg);
}

function scanProgress(job) {
  const el = $("scanProgress");
  if (!job) { el.classList.add("hidden"); el.innerHTML = ""; return; }
  const p = job.progress || {};
  let text;
  if (job.state === "running") {
    text = job.cancelling ? "Cancelling… The checks already under way finish first."
      : p.phase === "probe"
      ? `Checking ${plural(p.open || 0, "open address", "open addresses")} for ` +
        `${plural(p.devices || 0, "device")} · ${p.matched || 0} found so far`
      : `Looking for devices on ${plural(p.addresses || 0, "address", "addresses")}…`;
    el.innerHTML = `<span class="spinner"></span><span class="grow">${esc(text)}</span>` +
      `<button type="button" id="scanCancel"${job.cancelling ? " disabled" : ""}>Cancel</button>`;
  } else {
    const s = job.summary || {};
    text = job.state === "done" ? `Done in ${s.duration ?? "?"} s: found ${s.matched ?? 0} of ${plural(s.devices ?? 0, "device")}.`
      : job.state === "cancelled" ? "Scan cancelled."
      : job.error === "scanner_unavailable" ? SCANNER_UNAVAILABLE
      : "The scan stopped with an error. Try again.";
    el.innerHTML = `<span class="grow">${esc(text)}</span>`;
  }
  el.classList.remove("hidden");
  $("scanStart").disabled = job.state === "running";
}

function stopScanWatch() {
  lanWatch++;   // a poll still in flight finds it isn't wanted any more
  clearTimeout(lanPollTimer);
  lanPollTimer = null;
  $("scanStart").disabled = false;
}

// Polls the running job; when it ends, reloads the results and repaints. Each
// poll waits for the one before it, so a slow reply can't overtake a newer one.
function watchScan(showBox) {
  stopScanWatch();
  const watch = lanWatch, session = appState.accountGeneration, expectedAccount = appState.accountId;
  if (showBox) { showScanBox(true); scanProgress(appState.lanData.job); }
  $("scanStart").disabled = true;
  const next = () => { lanPollTimer = setTimeout(poll, 1000); };
  const poll = async () => {
    const { ok, status, body } = await api("/api/lan/scan");
    if (watch !== lanWatch || session !== appState.accountGeneration || expectedAccount !== appState.accountId) return;
    if (!ok && status === 401) { stopScanWatch(); return; }
    if (!ok) { next(); return; }      // a blip: ask again
    if (expectedAccount && body.account_id !== expectedAccount) {
      stopScanWatch(); loadDevices(); return;
    }
    const job = body.job;
    if (!job) { stopScanWatch(); scanProgress(null); return; }
    appState.lanData.job = job;
    if (job.kind === "scan") scanProgress(job);
    if (job.state === "running") { next(); return; }
    stopScanWatch();
    if (job.kind === "device") appState.panelCheck = { ...checkMessage(job), finishedAt: job.finished_at };
    await refreshLan();
  };
  next();
}

const SCANNER_UNAVAILABLE = "The network scan isn't available because its tinytuya library " +
  "couldn't be loaded. The device list and local keys still work. Restarting or updating the app may fix it.";

function checkMessage(job) {
  const r = job.result;
  if (job.error === "scanner_unavailable") return { id: job.device_id, kind: "error", text: SCANNER_UNAVAILABLE };
  if (job.state !== "done") return { id: job.device_id, kind: "error", text: "The check didn't finish. Try again." };
  if (!r) return { id: job.device_id, kind: "error", text: "The check didn't return a result." };
  const device = appState.deviceData.find(d => d.id === job.device_id) || {};
  if (r.status === "ok") {
    let text = `Found at ${r.ip}, protocol ${r.version}${r.device22 ? " (device22)" : ""}.`;
    if (String(r.version) === "3.1") text += " Protocol 3.1 status replies do not verify the local key.";
    const source = r.key_from && appState.deviceData.find(s => s.id === r.key_from);
    if (source && String(r.version) !== "3.1") {
      text += ` It answered to the local key Tuya lists on ${source.name || source.id}.`;
      // lanData still holds what was known before this check.
      const before = keySource(device);
      if (before && before.local_key !== source.local_key)
        text += ` Before, it had the one on ${before.name || before.id}.`;
      const also = job.also || [];
      const other = appState.deviceData.find(g => g.id !== device.id && !g.local_key &&
        (keySource(g) || {}).local_key === source.local_key);
      if (other && !also.some(a => a.device_id === other.id))
        text += ` ${other.name || other.id} had that key, so check it again at its own IP.`;
      for (const a of also) {
        const g = appState.deviceData.find(x => x.id === a.device_id);
        const name = g ? g.name || g.id : a.device_id, ar = a.result || {};
        text += ar.status === "ok"
          ? ` That left one gateway, so ${name} was checked too: found at ${ar.ip}.`
          : ` That left one gateway, so ${name} was checked too, at ${ar.ip || "its last address"}: ` +
            `${(LAN_BADGES[ar.status] || {}).text || "no answer"}.`;
      }
    }
    return { id: job.device_id, text };
  }
  if (r.status === "not_found" && isGateway(device) && !device.local_key)
    return { id: job.device_id, kind: "error",
             text: `Checked ${job.targets}: none of the keys Tuya lists on sub-devices answered there.` };
  const badgeText = (LAN_BADGES[r.status] || {}).text || r.status;
  return { id: job.device_id, kind: "error", text: `Checked ${job.targets}: ${badgeText}.` };
}

function scanFailure(body) {
  if (body.error === "bad_targets") return body.message || "Check the addresses and try again.";
  if (body.error === "scan_running") return "A scan is already running.";
  if (body.error === "no_devices") return "Load the device list first.";
  if (body.error === "scanner_unavailable") return SCANNER_UNAVAILABLE;
  if (body.error === "sub_device") return "Sub-devices are reached through their gateway. Check the gateway instead.";
  if (body.error === "no_local_key") return "This device has no local key, so it can't be checked on the local network.";
  return "Couldn't start the scan. Try again.";
}

async function startScan(payload) {
  const session = appState.accountGeneration, expectedAccount = appState.accountId;
  const { ok, body } = await api("/api/lan/scan", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...payload, account_id: expectedAccount }),
  });
  if (session !== appState.accountGeneration || expectedAccount !== appState.accountId) return { ok: false, body: { error: "account_changed" } };
  if (body.error === "account_changed" || (ok && body.account_id !== expectedAccount)) {
    loadDevices(); return { ok: false, body: { error: "account_changed" } };
  }
  if (ok) appState.lanData.job = body.job;
  return { ok, body };
}

async function checkDevice(id, ip) {
  if ((appState.lanData.job && appState.lanData.job.state === "running") || (appState.panelCheck && appState.panelCheck.kind === "pending")) return;
  const networkId = appState.panelView === "network" ? appState.selectedId : null;
  const keepNetworkFocus = () => {
    const active = document.activeElement;
    const lost = !active || active === document.body || !active.isConnected || active.disabled;
    if (networkId && appState.selectedId === networkId && appState.panelView === "network" && lost) {
      const input = $("networkBody").querySelector("[data-lan-check] input");
      (input || $("networkBody")).focus({ preventScroll: true });
    }
  };
  appState.panelCheck = { id, text: "Checking…", kind: "pending" };
  renderPanel();
  keepNetworkFocus();
  let response;
  try { response = await startScan({ device_id: id, ip }); }
  catch (err) { response = { ok: false, body: {} }; }
  const { ok, body } = response;
  if (body.error === "account_changed") return;
  if (!ok) {
    appState.panelCheck = { id, kind: "error", text: scanFailure(body), finishedAt: Date.now() / 1000 };
    renderPanel();
    keepNetworkFocus();
    return;
  }
  renderPanel();
  keepNetworkFocus();
  watchScan(false);
}

function isWatchingScan() { return !!lanPollTimer; }

let initialized = false;
function initNetwork(actions = {}) {
  if (initialized) return;
  initialized = true;
  ({ exclusionError, indexChanges, indexDevices, loadDevices, render, renderPanel } = actions);
  $("scanBtn").onclick = () => showScanBox($("scanBox").classList.contains("hidden"));
  $("scanClose").onclick = () => showScanBox(false);
  $("scanForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    scanError("");
    const { ok, body } = await startScan({ targets: $("scanTargets").value });
    if (body.error === "account_changed") return;
    if (!ok) {
      scanError(scanFailure(body));
      if (body.error === "scan_running") watchScan(true);
      return;
    }
    watchScan(true);
    renderPanel();   // disables the panel's Check while the scan runs
  });
  $("scanProgress").addEventListener("click", async (e) => {
    if (!e.target.closest("#scanCancel")) return;
    // Say so straight away: the checks under way take a few seconds to finish.
    if (appState.lanData.job) { appState.lanData.job = { ...appState.lanData.job, cancelling: true }; scanProgress(appState.lanData.job); }
    const session = appState.accountGeneration, expectedAccount = appState.accountId;
    const { ok, body } = await api("/api/lan/scan", {
      method: "DELETE", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ account_id: expectedAccount }),
    });
    if (session !== appState.accountGeneration || expectedAccount !== appState.accountId) return;
    if (body.error === "account_changed" || (ok && body.account_id !== expectedAccount)) { stopScanWatch(); loadDevices(); }
  });
  $("networkBody").addEventListener("submit", async (e) => {
    const form = e.target.closest("[data-lan-check]");
    if (!form) return;
    e.preventDefault();
    await checkDevice(form.dataset.lanCheck, form.elements.ip.value);
  });
}

export { initNetwork, isWatchingScan, loadLan, networkSection, resetLan, showScanBox, watchScan };
