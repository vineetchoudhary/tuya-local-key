// Setup.
import { LOCAL_PROTOCOLS, SETUP_TOOLS, TUYA_LOCAL_DEVICE22 } from "../shared/constants.js";
import { $ } from "../shared/dom.js";
import { plural } from "../shared/format.js";
import { deviceLink, esc, keyDisplay, renderLocalKeyToggle } from "../shared/html.js";
import { icon } from "../shared/icons.js";
import { gatewayIdOf, isGateway, isSubDevice, networkState, setupConnection } from "../shared/model.js";
import { appState } from "../shared/state.js";

// Cross-feature actions are connected by app.js.
let inCurrentView, matchesFilters, renderPanel;

function setupTabKeydown(e) {
  const tab = e.target.closest("[data-setup-tool]");
  if (!tab || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
  e.preventDefault();
  const i = SETUP_TOOLS.findIndex(t => t.id === tab.dataset.setupTool);
  const next = e.key === "Home" ? 0 : e.key === "End" ? SETUP_TOOLS.length - 1
    : (i + (e.key === "ArrowRight" ? 1 : -1) + SETUP_TOOLS.length) % SETUP_TOOLS.length;
  appState.setupTool = SETUP_TOOLS[next].id;
  repaintSetup(`[data-setup-tool="${appState.setupTool}"]`);
}

function setupEditionChange(e) {
  if (!e.target.matches("[data-localtuya-edition]")) return;
  appState.localTuyaEdition = e.target.value;
  repaintSetup("[data-localtuya-edition]");
}

function repaintSetup(focus) {
  const body = $("setupBody");
  const scroll = body.scrollTop;
  renderPanel();
  const control = body.querySelector(focus);
  if (control) control.focus({ preventScroll: true });
  body.scrollTop = scroll;
}

function setupField(key, label, value, options = {}) {
  const available = value !== null && value !== undefined && value !== "";
  const text = available ? String(value) : "";
  const shown = !available ? `<span class="dash">${esc(options.empty || "Not available")}</span>`
    : options.device ? `<button class="link-btn" type="button" data-open-device="${esc(options.device.id)}" ` +
      `aria-label="Set up ${esc(options.device.name || options.device.id)}">${esc(text)}</button>` +
      (appState.excludedIds.has(options.device.id) ? ' <span class="badge muted">Excluded</span>' : "")
    : options.secret ? keyDisplay(text, appState.panelKeyVisible) : esc(text);
  return `<div class="setup-field" data-setup-field="${esc(key)}">` +
    `<div class="setup-label">${esc(label)}</div>` +
    `<div class="setup-value-row"><code class="setup-value mono">${shown}</code>` +
    (options.secret && available ? renderLocalKeyToggle("panel") : "") +
    `<button class="setup-copy" type="button" aria-label="Copy ${esc(label)}"` +
    (available ? ` data-copy="${esc(text)}"` : " disabled") + `>Copy</button></div></div>`;
}

function setupTuyaLocal(d, s) {
  const protocol = s.device22 ? TUYA_LOCAL_DEVICE22[s.version] || s.version : s.version;
  const cid = s.sub ? s.nodeId || d.uuid || "" : "";
  const cidLabel = s.sub && !s.nodeId && cid ? "Sub-device UUID" : "Sub-device node ID";
  return setupField("host", s.sub ? "Gateway IP / hostname" : "IP address / hostname", s.host) +
    setupField("device_id", s.sub ? "Gateway device ID" : "Device ID",
      s.sub ? s.gatewayId : s.id, { device: s.gateway }) +
    setupField("local_key", s.sub ? "Gateway local key" : "Local key", s.key, { secret: true }) +
    setupField("protocol_version", "Protocol version", protocol || "auto") +
    setupField("poll_only", "Poll only", false) +
    setupField("device_cid", cidLabel, cid, { empty: s.sub ? "Not available" : "Leave empty" });
}

function localTuyaEditionSelector() {
  const fork = appState.localTuyaEdition === "xzetsubou";
  return `<div class="setup-edition"><label for="localTuyaEdition">localtuya edition</label>` +
    `<div class="setup-edition-control"><select id="localTuyaEdition" data-localtuya-edition>` +
    `<option value="xzetsubou"${fork ? " selected" : ""}>xZetsubou (supports sub-devices)</option>` +
    `<option value="original"${!fork ? " selected" : ""}>rospogrigio (original)</option></select>${icon("chevron")}</div></div>`;
}

function setupLocalTuya(d, s) {
  const fork = appState.localTuyaEdition === "xzetsubou";
  const supported = fork ? LOCAL_PROTOCOLS : LOCAL_PROTOCOLS.filter(v => v !== "3.5");
  const protocol = supported.includes(s.version) ? s.version : !s.version && fork ? "auto" : "";
  let html = localTuyaEditionSelector();
  if (s.sub && !fork) {
    return html;
  }
  html += setupField("friendly_name", fork ? "Device Name" : "Name", d.name || s.id) +
    setupField("host", s.sub ? "Gateway IP" : fork ? "IP Address" : "Host", s.host) +
    setupField("device_id", "Device ID", s.id) +
    setupField("local_key", s.sub ? "Gateway local key" : fork ? "Local Key" : "Local key", s.key, { secret: true }) +
    setupField("protocol_version", "Protocol Version", protocol, {
      empty: s.version && !protocol ? `${s.version} (unsupported)` : "Not available",
    });
  if (fork) html += setupField("node_id", "Sub-device node ID", s.sub ? s.nodeId : "", {
    empty: s.sub ? "Not available" : "Leave empty",
  });
  if (s.sub) html += setupField("gateway_id", "Gateway ID (reference)", s.gatewayId, { device: s.gateway });
  html += `<details class="setup-advanced" data-setup-advanced${appState.setupAdvancedOpen ? " open" : ""}>` +
    `<summary>Advanced options</summary><div>` +
    setupField("enable_debug", "Enable debug", false) +
    setupField("scan_interval", "Scan interval (seconds)", "", { empty: "Leave empty" }) +
    setupField("manual_dps_strings", "Manual DPS to add", "", { empty: "Leave empty" }) +
    setupField("reset_dpids", "Reset DP IDs", "", { empty: "Leave empty" });
  if (fork) html += setupField("device_sleep_time", "Device sleep time (seconds)", "", { empty: "Leave empty" });
  return html + `</div></details>`;
}

// JSON string literals are also valid Python string literals, including keys
// with quotes, backslashes, newlines, or HTML characters.
function pythonString(value) { return JSON.stringify(String(value)); }

function tinytuyaSnippet(d, s, masked = false) {
  const missing = [];
  if (!(s.sub ? s.gatewayId : s.id)) missing.push(s.sub ? "gateway ID" : "device ID");
  if (!s.host) missing.push(s.sub ? "gateway's local IP" : "local IP");
  if (!s.key) missing.push(s.sub ? "gateway key" : "local key");
  if (!LOCAL_PROTOCOLS.includes(s.version)) missing.push("numeric protocol version");
  if (s.sub && !s.nodeId) missing.push("sub-device node ID");
  if (s.sub && !s.id) missing.push("sub-device ID");
  if (missing.length) return { code: "", missing };
  const gateway = s.sub || isGateway(d), name = gateway ? "gateway" : "device";
  const key = masked ? "•".repeat(16) : s.key;
  const lines = ["import tinytuya", "", `${name} = tinytuya.Device(`,
    `    ${pythonString(s.sub ? s.gatewayId : s.id)},`,
    `    ${pythonString(s.host)},`, `    ${pythonString(key)},`, `    version=${s.version},`];
  if (s.device22) lines.push('    dev_type="device22",');
  lines.push(")");
  if (s.sub) lines.push("", "device = tinytuya.Device(", `    ${pythonString(s.id)},`,
    `    cid=${pythonString(s.nodeId)},`, "    parent=gateway,", ")");
  lines.push("", gateway && !s.sub ? "print(gateway.subdev_query())" : "print(device.status())");
  return { code: lines.join("\n"), missing };
}

function setupTinyTuya(d, s) {
  let html = setupField("dev_id", s.sub ? "Gateway device ID" : "Device ID",
      s.sub ? s.gatewayId : s.id, { device: s.gateway }) +
    setupField("address", s.sub ? "Gateway local IP" : "Local IP address", s.host) +
    setupField("local_key", s.sub ? "Gateway local key" : "Local key", s.key, { secret: true }) +
    setupField("version", "Protocol version", s.version);
  if (s.device22) html += setupField("dev_type", "Device type (dev_type)", "device22");
  if (s.sub) html += setupField("sub_id", "Sub-device ID", s.id) +
    setupField("cid", "Sub-device node ID", s.nodeId) +
    setupField("parent", "Parent gateway", "gateway");
  const snippet = tinytuyaSnippet(d, s);
  const preview = appState.panelKeyVisible ? snippet.code : tinytuyaSnippet(d, s, true).code;
  html += `<div class="setup-snippet"><div class="setup-snippet-head"><span class="setup-label">Python snippet</span>` +
    `<button class="setup-copy" type="button" aria-label="Copy Python snippet" data-setup-copy-snippet` +
    (snippet.code ? ` data-copy="${esc(snippet.code)}"` : " disabled") + `>Copy</button></div>`;
  if (snippet.code) html += `<pre class="mono" data-setup-snippet>${esc(preview)}</pre>`;
  else html += `<p class="setup-note">Complete the missing setup details above to generate the snippet.</p>`;
  return html + `</div>`;
}

function setupChildren(d) {
  if (!isGateway(d)) return "";
  const children = appState.deviceData.filter(s => isSubDevice(s) && gatewayIdOf(s) === d.id);
  const excludedCount = children.filter(s => appState.excludedIds.has(s.id) && matchesFilters(s)).length;
  const subs = children.filter(s => inCurrentView(s.id))
    .sort((a, b) => (a.name || a.id).localeCompare(b.name || b.id));
  return `<div class="setup-children"><h4>Sub-devices (${subs.length})</h4>` +
    (subs.length ? `<ul>` + subs.map(s => `<li>${deviceLink(s)}` +
      `<div class="setup-child-fields">${setupField("child_device_id", "Device ID", s.id)}` +
      `${setupField("child_node_id", "Node ID", s.node_id)}</div></li>`).join("") + `</ul>`
      : "") + (appState.deviceView === "active" && excludedCount
        ? `<p class="hint"><button class="link-btn" type="button" data-view-excluded>${plural(excludedCount, "excluded sub-device")}</button></p>` : "") + `</div>`;
}

function setupSection(d) {
  const s = setupConnection(d);
  const tabs = setupTabs("setup");
  const body = appState.setupTool === "tuya-local" ? setupTuyaLocal(d, s)
    : appState.setupTool === "localtuya" ? setupLocalTuya(d, s) : setupTinyTuya(d, s);
  const children = setupChildren(d);
  const tool = SETUP_TOOLS.find(t => t.id === appState.setupTool);
  const repo = appState.setupTool === "localtuya" && appState.localTuyaEdition === "original"
    ? "rospogrigio/localtuya" : tool.repo;
  const footnote = `<footer class="setup-footnote">GitHub · ` +
    `<a href="https://github.com/${repo}" target="_blank" rel="noopener noreferrer">${esc(repo)}</a></footer>`;
  return `<div class="setup-card"><div class="setup-tabs" role="tablist" aria-label="Local setup tool">${tabs}</div>` +
    `<div class="setup-content" id="setup-tool-panel" role="tabpanel" tabindex="0" ` +
    `aria-labelledby="setup-tab-${appState.setupTool}">${setupReadinessNote(d)}${body}${children}${footnote}</div></div>`;
}

function setupTabs(view) {
  return SETUP_TOOLS.map(t => `<button type="button" role="tab" id="${view}-tab-${t.id}" ` +
    `data-setup-tool="${t.id}" aria-controls="${view}-tool-panel" aria-selected="${t.id === appState.setupTool}" ` +
    `tabindex="${t.id === appState.setupTool ? 0 : -1}">${t.name}</button>`).join("");
}

// Setup guidance stays beside the values it explains; local diagnostics have
// their own page and never depend on the selected integration.
function setupReadinessNote(d) {
  const s = setupConnection(d), tool = appState.setupTool, fork = appState.localTuyaEdition === "xzetsubou";
  const missing = [], actions = [];
  const addAction = (id, label) => {
    const refreshing = id === "refresh" && $("refreshBtn").disabled;
    const scanning = id === "scan" && ((appState.lanData.job && appState.lanData.job.state === "running") || (appState.panelCheck && appState.panelCheck.kind === "pending"));
    actions.push(`<button type="button" class="link-btn" data-setup-action="${id}"${refreshing || scanning ? " disabled" : ""}>${refreshing ? "Refreshing…" : label}</button>`);
  };
  let text = "";
  if (s.sub && tool === "localtuya" && !fork) {
    text = "Sub-devices require the xZetsubou edition.";
    addAction("edition", "Use xZetsubou");
  } else {
    const needsGatewayId = s.sub && tool !== "localtuya";
    if (!(needsGatewayId ? s.gatewayId : s.id)) missing.push(needsGatewayId ? "gateway ID" : "device ID");
    if (s.sub && tool === "tinytuya" && !s.id) missing.push("sub-device ID");
    if (!s.host) missing.push(s.sub ? "gateway IP" : "local IP");
    if (!s.key) missing.push(s.sub ? "gateway local key" : "local key");
    const supported = tool === "localtuya" && !fork ? LOCAL_PROTOCOLS.filter(v => v !== "3.5") : LOCAL_PROTOCOLS;
    const auto = !s.version && (tool === "tuya-local" || (tool === "localtuya" && fork));
    if (!s.version && !auto) missing.push("protocol version");
    if (s.sub && !(s.nodeId || (tool === "tuya-local" && d.uuid))) missing.push("sub-device node ID");
    const unsupported = s.version && !supported.includes(s.version);
    const network = networkState(d);
    if (missing.length) text = `Missing: ${missing.join(", ")}.`;
    if (unsupported) text += `${text ? " " : ""}Protocol ${s.version} is not supported by this ${tool === "localtuya" ? "edition" : "tool"}.`;
    if (!text && (network.tone === "attention" || network.title === "Check again" || network.childOnline === false))
      text = "Local access needs attention. Check the latest connection in Local network.";
    if (!text) return "";
    if (unsupported && tool === "localtuya" && !fork && s.version === "3.5") addAction("edition", "Use xZetsubou");
    else if (!s.key || missing.some(label => ["device ID", "sub-device ID", "sub-device node ID"].includes(label)))
      addAction("refresh", "Refresh devices");
    else if (!s.host || !s.version && !auto || needsGatewayId && !s.gatewayId) addAction("scan", "Scan network");
    const manualIP = !s.host && (s.sub ? !!s.gateway : !!d.local_key || isGateway(d));
    addAction("network", manualIP ? "Enter IP manually" : "Local network");
  }
  return `<div class="setup-readiness" data-setup-readiness role="status"><p>${esc(text)}</p>` +
    `<div class="setup-note-actions">${actions.join("")}</div></div>`;
}

let initialized = false;
function initSetup(actions = {}) {
  if (initialized) return;
  initialized = true;
  ({ inCurrentView, matchesFilters, renderPanel } = actions);
  $("setupBody").addEventListener("keydown", setupTabKeydown);
  $("setupBody").addEventListener("change", setupEditionChange);
}

export { initSetup, repaintSetup, setupSection };
