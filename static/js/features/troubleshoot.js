// Guided connection troubleshooting. Network requests stay in network.js.
import { $ } from "../shared/dom.js";
import { fieldList } from "../shared/fields.js";
import { localTime } from "../shared/format.js";
import { deviceLink, esc } from "../shared/html.js";
import { gatewayIdOf, isGateway, isSubDevice, networkState } from "../shared/model.js";
import { appState } from "../shared/state.js";

let cancelScan, checkDevice, loadDevices, navigatePanel, renderPanel, scanFailure, show, startScan, watchScan;

function fixState() {
  const page = appState.panelPage;
  if (!page.state.fix) page.state.fix = { step: 0, showScan: false, scanTargets: null, operation: null };
  return page.state.fix;
}

function connection(d) {
  const n = networkState(d);
  // A keyless gateway can be identified using keys Tuya lists on its children.
  const candidates = n.target && isGateway(n.target) && appState.deviceData.some(child =>
    isSubDevice(child) && child.local_key && (!gatewayIdOf(child) || gatewayIdOf(child) === n.target.id));
  return { ...n, canCheck: !!n.target && (!!n.s.key || !!candidates) };
}

function busy() {
  return (appState.lanData.job && appState.lanData.job.state === "running") ||
    (appState.panelCheck && appState.panelCheck.kind === "pending");
}

function button(action, label, primary = false, disabled = false) {
  return `<button type="button" data-fix-action="${action}"${primary ? ' class="primary"' : ""}` +
    `${disabled ? " disabled" : ""}>${esc(label)}</button>`;
}

function heading(text, note = "") {
  return `<h3 data-fix-heading tabindex="-1">${esc(text)}</h3>` + (note ? `<p>${esc(note)}</p>` : "");
}

function snapshot() {
  if (!appState.snapshotReason) return "";
  const expired = appState.snapshotReason === "session_invalid";
  return `<div class="fix-note" role="status">${expired
    ? "Your Tuya login expired. Saved keys can still be tested locally. Log in again to check for a new key."
    : "Tuya could not be reached. The saved device details are still available for local checks."}</div>`;
}

function details(d, n) {
  const s = n.s, refreshing = $("refreshBtn").disabled;
  let html = heading("Check the saved details", "Review the connection details, test local access, then get the next step for your integration.");
  if (s.sub) {
    html += `<div class="fix-note">This device connects through a gateway. ` +
      (s.gateway ? `We will check ${deviceLink(s.gateway)} first.`
        : s.gatewayId ? "Its gateway is missing from the device list. Refresh from Tuya to look for it."
          : "Its gateway has not been identified. Open the gateway below and check it at the address shown in your router.") + `</div>`;
    if (!s.gateway && !s.gatewayId) {
      const gateways = appState.deviceData.filter(isGateway);
      if (gateways.length) html += `<ul class="network-gateways">${gateways.map(g => `<li>${deviceLink(g)}</li>`).join("")}</ul>`;
    }
  }
  html += `<dl class="fix-details">` +
    `<dt>Local key</dt><dd>${s.key ? "Available" : n.canCheck ? "Look for the gateway key during the check" : "Unavailable"}</dd>` +
    `<dt>${s.sub ? "Gateway IP" : "Local IP"}</dt><dd>${esc(s.host || "Find it in the next step")}</dd>` +
    `<dt>${s.sub ? "Gateway protocol" : "Protocol"}</dt><dd>${esc(s.version || "Detect during the check")}</dd></dl>`;
  if (!n.canCheck && n.target) {
    html += `<div class="fix-note">Tuya has not supplied a usable local key. Refresh from Tuya once to check for updates. ` +
      `Bluetooth-only devices do not have a LAN key. Devices behind a gateway need their gateway's connection details.</div>`;
  }
  html += refreshNote();
  const expired = appState.snapshotReason === "session_invalid";
  return html + `<div class="fix-actions">` +
    (n.canCheck ? button("test", "Continue", true, busy() || refreshing)
      : expired ? button("login", "Log in again", true)
        : button("refresh", refreshing ? "Refreshing…" : "Refresh from Tuya", true, refreshing || busy())) + `</div>` +
    (n.canCheck && expired ? `<p class="fix-alternative">${button("login", "Log in again")}</p>` : "");
}

function refreshNote() {
  const result = fixState().refreshResult;
  if (!result) return "";
  return `<p class="fix-note" role="status">${result === "ok"
    ? "Device list refreshed. Test the connection again with the latest details."
    : "The device list could not be refreshed. Saved details are still shown. Check your login and connection to Tuya."}</p>`;
}

function test(d, n) {
  const state = fixState(), s = n.s;
  if (!n.canCheck) return details(d, n);
  const pending = busy() || $("refreshBtn").disabled;
  const ip = state.addressTarget === n.target.id ? state.address : s.host;
  if (state.showScan) {
    const targets = state.scanTargets ?? (appState.lanData.targets || $("scanTargets").value || "");
    return heading("Find the device's IP", "Enter the network your devices use, such as your IoT VLAN.") +
      `<label class="network-check-label" for="fixTargets">Network range</label>` +
      `<form class="fix-scan" data-fix-scan><input id="fixTargets" type="text" name="targets" value="${esc(targets)}" ` +
      `placeholder="e.g. 192.168.2.0/24" autocomplete="off" spellcheck="false" required>` +
      `<p class="lan-note">Several ranges can be separated by commas, up to 1,024 addresses. ` +
      `The scan checks all account devices, including excluded devices.</p>` +
      `<button type="submit" class="primary"${pending ? " disabled" : ""}>Scan network</button></form>`;
  }
  let html = heading(s.sub ? "Test the gateway connection" : "Test the device connection",
    "Use the local IP address shown in your router. The check detects the protocol, asks for status, then closes the connection.");
  html += refreshNote();
  if (pending) html += `<p class="fix-note" role="status">Another check or refresh is running. Wait for it to finish before starting a test.</p>`;
  html += `<label class="network-check-label" for="fixCheckIP">${s.sub ? "Gateway IP address" : "Local IP address"}</label>` +
    `<form class="lan-check" data-lan-check="${esc(n.target.id)}">` +
    `<input id="fixCheckIP" type="text" name="ip" value="${esc(ip || "")}" placeholder="e.g. 192.168.2.61" ` +
    `autocomplete="off" spellcheck="false" required>` +
    `<button type="submit" class="primary"${pending ? " disabled" : ""}>Test connection</button></form>` +
    `<p class="fix-alternative">${button("scan", "Don't know the IP? Find it", false, pending)}</p>`;
  return html;
}

function advice(title, note, items, tone = "attention") {
  return `<div class="fix-result" data-tone="${tone}" role="status">${heading(title, note)}</div>` +
    (items.length ? `<ol class="fix-advice">${items.map(item => `<li>${esc(item)}</li>`).join("")}</ol>` : "");
}

function changes(d, n) {
  const state = fixState(), previous = (state.operation || {}).before || {}, items = [];
  const ids = new Set([d.id, n.target && n.target.id, n.r.key_from].filter(Boolean));
  if (((appState.deviceChanges || {}).key_changed || []).some(item => ids.has(item.id))) {
    items.push("Tuya supplied a changed local key. Copy the latest key into your integration.");
  }
  if (previous.ip && previous.ip !== n.s.host) items.push(`The IP changed from ${previous.ip} to ${n.s.host}. Update the host address in your integration.`);
  if (previous.version && previous.version !== n.s.version) items.push(`The protocol changed from ${previous.version} to ${n.s.version}. Update the protocol setting in your integration.`);
  return items;
}

function result(d, n) {
  const state = fixState(), operation = state.operation || {}, job = appState.lanData.job;
  const ownJob = operation.id && job && job.id === operation.id;
  if (operation.pending || (ownJob && job.state === "running")) {
    const p = ownJob ? job.progress || {} : {};
    const note = ownJob && job.cancelling ? "Stopping after the checks already under way finish."
      : operation.kind === "scan" ? p.phase === "probe" ? `${p.matched || 0} devices found so far. Checking the remaining addresses.`
        : "Looking for devices in the network range you entered." : "Testing the address with the saved key and detecting the protocol.";
    return `<div class="fix-result" role="status" aria-busy="true"><span class="spinner"></span>` +
      heading(operation.kind === "scan" ? "Scanning the network" : "Checking the connection", note) + `</div>` +
      (operation.cancelFailed ? `<p class="fix-note" role="status">The stop request failed. Try Stop check again, or wait for the check to finish.</p>` : "") +
      (ownJob ? `<div class="fix-actions">${button("cancel", job.cancelling ? "Stopping…" : "Stop check", false, job.cancelling)}</div>` : "");
  }
  const error = operation.error || (ownJob && job.error);
  let html, action = "test", label = "Try again";
  const fresh = ownJob && job.state === "done" && !error && n.r.checked_at >= job.started_at;
  const status = ownJob && job.state === "done" && job.kind === "device" && job.result
    ? job.result.status : fresh ? n.r.status : null;
  if (error === "scanner_unavailable") {
    html = advice("The network checker is unavailable", "The app could not load its network-checking library.", [
      "Restart the app and try again. If it still fails, update the app.", "Your saved device list and local keys are still available.",
    ]);
  } else if (error === "not_logged_in" || error === "session_invalid") {
    action = "login"; label = "Log in again";
    html = advice("Log in again", "A saved login is needed to start this check.", ["Log in with a new QR code, then reopen this device."]);
  } else if (ownJob && job.state === "cancelled") {
    html = advice("Check stopped", "The check was cancelled. Earlier results do not confirm this attempt.", ["Start a new test when you are ready."]);
  } else if (error || (ownJob && job.state === "failed")) {
    html = advice("The check could not finish", operation.message || "The app could not complete this check.", [
      error === "bad_targets" ? "Enter a local IP address for a device test, or a valid network range for a scan." : "Try the test again. Your saved device details are still available.",
    ]);
  } else if (status === "key_mismatch") {
    action = appState.snapshotReason === "session_invalid" ? "login" : "refresh";
    label = action === "login" ? "Log in again" : "Refresh key from Tuya";
    html = advice("The key was not accepted", "The address answered, but the saved key did not work.", [
      "Refresh from Tuya to check for a changed key, then test again.",
      "Confirm the IP still belongs to this device. A different device may have taken its old address.",
    ]);
  } else if (status === "busy") {
    html = advice("The connection was refused", "Another local client may already be connected. The IP may also belong to a different device.", [
      "Confirm the IP in your router's device list.",
      "Temporarily pause the local integration using this device and close Smart Life or Tuya Smart, then retry.",
      "Resume the integration after the test.",
    ]);
  } else if (["unreachable", "not_found"].includes(status)) {
    action = "scan"; label = "Find the device";
    html = advice("No device reply", "This result does not establish whether the device is offline or blocked by the network.", [
      "Check the device's power and its current IP in your router.",
      "Scan the network your devices use, including the correct IoT VLAN.",
      "If a firewall separates the app and device, allow the machine running this app to reach the device on TCP port 6668.",
    ]);
  } else if (!fresh) {
    html = advice("A new test is needed", "This attempt has no confirmed, current result for this device.", [
      "Check the IP in your router, then test that address. A scan may miss a device that is asleep or already connected to another local client.",
    ]);
  } else if (n.s.version === "3.1") {
    action = "setup"; label = "Open setup values";
    html = advice("Device found, key not verified", "Protocol 3.1 status replies identify the device but do not verify its local key.", [
      "Use the detected address and protocol in setup. Your integration will need to test control with the local key.", ...changes(d, n),
    ], "pending");
  } else if (n.tone === "ready") {
    const offline = n.s.sub && n.childOnline === false;
    action = offline ? "test" : "setup";
    label = offline ? "Recheck gateway" : "Open setup values";
    html = advice(offline ? "Gateway works, sub-device reported offline" : n.s.sub ? "Gateway connection checked" : "Connection checked",
      n.s.sub ? "The gateway answered with the current local key. This checks the gateway connection only."
        : "The device answered with the current local key at the address below.", [
        ...changes(d, n),
        ...(offline ? ["Check the sub-device's power and its connection to the gateway."]
          : n.s.sub ? [n.childOnline === true ? "The gateway reports the sub-device online. Setup still needs to test its own response."
            : "The gateway did not confirm the sub-device's status. Check its power and gateway pairing if setup fails."] : []),
        "Open setup values and compare the IP, protocol, and key with your integration's settings.",
      ], offline ? "attention" : "ready");
  } else {
    html = advice("Check again with the current details", "The saved result does not verify the current key and gateway relationship.", [
      "Test again using the latest device details. A refresh during a check can change the key it needs to verify.",
    ], "pending");
  }
  const found = fresh && ["ok", "via_gateway"].includes(status) && (n.tone === "ready" || n.s.version === "3.1");
  const checkedIP = fresh ? n.s.host : ownJob && job.kind === "device" && job.result && job.result.ip;
  if (checkedIP) {
    const labels = found ? n.s.sub ? { local_ip: "Gateway IP", protocol_version: "Gateway protocol" } : {} : { local_ip: "Tested IP" };
    html += fieldList({ local_ip: checkedIP, protocol_version: n.s.version, device22: n.s.device22 },
      found ? ["local_ip", "protocol_version"] : ["local_ip"], labels);
    html += `<p class="lan-note">Checked ${esc(localTime(fresh ? n.r.checked_at : job.finished_at))}.</p>`;
  }
  const refreshing = $("refreshBtn").disabled;
  return html + refreshNote() + `<div class="fix-actions">` +
    button(action, action === "refresh" && refreshing ? "Refreshing…" : label, true,
      !["setup", "login"].includes(action) && (busy() || refreshing)) + `</div>`;
}

function fixBackLabel() {
  const state = appState.panelPage.state.fix;
  if (!state || !state.step) return "";
  return state.step === 2 ? "Back to test" : state.showScan ? "Back to IP address" : "Back to details";
}

function backFix() {
  const state = fixState();
  if (!state.step) return false;
  if (state.step === 2) state.step = 1;
  else if (state.showScan) state.showScan = false;
  else state.step = 0;
  repaint(true);
  return true;
}

function fixSection(d) {
  const state = fixState(), n = connection(d);
  // A refresh may remove a key or change the gateway while the wizard is open.
  const step = state.step === 1 && !n.canCheck ? 0 : state.step;
  const steps = ["Details", "Test", "Next steps"];
  return `<ol class="fix-steps" aria-label="Connection troubleshooting steps">${steps.map((label, i) =>
    `<li${step === i ? ' aria-current="step"' : ""}><span>${i + 1}</span>${label}</li>`).join("")}</ol>` +
    snapshot() + `<div class="fix-card" data-fix-step="${step}">` +
    (step === 0 ? details(d, n) : step === 1 ? test(d, n) : result(d, n)) + `</div>`;
}

function repaint(focus = false) {
  renderPanel();
  if (focus && appState.panelView === "fix") {
    const body = $("fixBody");
    body.scrollTop = 0;
    const target = body.querySelector('[data-fix-step="1"] input') || body.querySelector("[data-fix-heading]");
    if (target) target.focus({ preventScroll: true });
  }
}

async function run(d, kind, value) {
  const state = fixState(), n = connection(d);
  if (busy() || $("refreshBtn").disabled || state.operation && state.operation.pending) return;
  const operation = { kind, pending: true, id: null, before: { ip: n.s.host, version: n.s.version } };
  state.operation = operation; state.step = 2;
  const session = appState.accountGeneration, account = appState.accountId;
  if (kind === "scan") { state.address = null; state.addressTarget = null; }
  repaint(true);
  let response;
  try { response = kind === "scan" ? await startScan({ targets: value }) : await checkDevice(n.target.id, value); }
  catch (err) { response = { ok: false, body: {} }; }
  if (session !== appState.accountGeneration || account !== appState.accountId) return;
  const { ok, body } = response || { ok: false, body: {} };
  operation.pending = false;
  if (ok) {
    operation.id = body.job.id;
    if (kind === "scan") watchScan(false);
  } else {
    operation.error = body.error || "request_failed";
    operation.message = scanFailure(body);
  }
  repaint();
}

let initialized = false;
function initTroubleshoot(actions = {}) {
  if (initialized) return;
  initialized = true;
  ({ cancelScan, checkDevice, loadDevices, navigatePanel, renderPanel, scanFailure, show, startScan, watchScan } = actions);
  $("fixBody").addEventListener("click", async e => {
    const control = e.target.closest("[data-fix-action]");
    if (!control || control.disabled || appState.panelView !== "fix") return;
    const state = fixState(), action = control.dataset.fixAction;
    if (action === "setup") { navigatePanel(appState.selectedId, "setup", control); return; }
    if (action === "login") { show("login"); return; }
    if (action === "cancel") {
      const operation = state.operation;
      const response = await cancelScan();
      if (operation) operation.cancelFailed = !!response && !response.ok;
      repaint(); return;
    }
    if (action === "refresh") {
      if ($("refreshBtn").disabled || busy()) return;
      const page = appState.panelPage, session = appState.accountGeneration;
      state.refreshResult = null;
      const ok = await loadDevices("Updating device list from Tuya…", true, true);
      if (session !== appState.accountGeneration || appState.panelPage !== page) return;
      state.refreshResult = ok ? "ok" : "failed";
      if (ok) {
        const d = appState.deviceData.find(device => device.id === appState.selectedId);
        state.step = d && connection(d).canCheck ? 1 : 0;
        state.showScan = false;
      }
      const active = document.activeElement;
      repaint(!active || active === document.body || $("fixBody").contains(active));
      return;
    }
    state.step = 1;
    state.showScan = action === "scan";
    repaint(true);
  });
  $("fixBody").addEventListener("input", e => {
    const state = fixState();
    if (e.target.id === "fixTargets") state.scanTargets = e.target.value;
    if (e.target.id === "fixCheckIP") {
      state.address = e.target.value;
      state.addressTarget = e.target.form.dataset.lanCheck;
    }
  });
  $("fixBody").addEventListener("submit", async e => {
    const form = e.target.closest("[data-lan-check], [data-fix-scan]");
    if (!form || appState.panelView !== "fix") return;
    e.preventDefault();
    const d = appState.deviceData.find(device => device.id === appState.selectedId);
    if (!d) return;
    const scan = form.hasAttribute("data-fix-scan");
    await run(d, scan ? "scan" : "device", scan ? form.elements.targets.value : form.elements.ip.value);
  });
}

export { backFix, fixBackLabel, fixSection, initTroubleshoot };
