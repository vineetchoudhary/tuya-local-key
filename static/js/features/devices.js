// Devices.
import { api } from "../shared/api.js";
import { CHANGE_BADGES, SPEC_KEYS, TIME_KEYS } from "../shared/constants.js";
import { $ } from "../shared/dom.js";
import { dash } from "../shared/fields.js";
import { cmp, formatCacheAge, localTime, plural, renderTime } from "../shared/format.js";
import { esc, renderLocalKey, renderLocalKeyToggle, renderNodeId } from "../shared/html.js";
import { icon } from "../shared/icons.js";
import {
  applyLan,
  gatewayIdOf,
  isOnline,
  isSubDevice,
  keySource,
  lanBadgeText,
  lanStatus,
  localKeyOf,
  needsCheck,
  protocolCell,
  statusBadge,
  statusRank,
} from "../shared/model.js";
import { appState } from "../shared/state.js";

// Cross-feature actions are connected by app.js.
let applyExclusions, banner, changeBadge, closePanel, exclusionError, inCurrentView, indexChanges, isWatchingScan, loadHomes, loadLan, matchesFilters, openPanel, render, renderPanel, resetExclusions, resetLan, selectAllDevices, selectionIds, setDeviceSelected, show, syncSelectionUi, validExclusions, watchScan;

let cacheStatusTimer = null;

let refreshStatusTimer = null;

let searchIndex = new WeakMap();

// Column definitions drive both the header and the rows (kept in sync). The
// fields left out here (uuid, category, specs, …) live in the details panel.
const COLUMNS = [
  { key: "name",         label: "Name",         sortable: true,
    render: d => esc(d.name) + changeBadge(d.id) },
  { key: "online",       label: "Status",       sortable: true, render: statusBadge, sortValue: statusRank },
  { key: "id",           label: "ID",           cls: "mono" },
  { key: "local_key",    label: "Local Key",    header: renderLocalKeyHeader, render: localKeyCell },
  { key: "protocol_version", label: "Protocol", sortable: true, cls: "mono", render: protocolCell,
    sortValue: d => isSubDevice(d) ? "" : d.protocol_version },
  { key: "product_id",   label: "Product ID",   cls: "mono" },
  { key: "product_name", label: "Product Name" },
  { key: "update_time",  label: "Update Time",  sortable: true, render: d => renderTime(d, "update_time") },
];

function localKeyCell(d) {
  if (isSubDevice(d)) return renderNodeId(d.node_id);
  const source = keySource(d);
  if (!source) return renderLocalKey(localKeyOf(d), appState.tableKeysVisible);
  const tip = `Tuya lists this key on its sub-device ${source.name || source.id}, not on the gateway.`;
  // The sub-device's name stays in the tooltip, so this row doesn't read as the sub-device's.
  return renderLocalKey(localKeyOf(d), appState.tableKeysVisible) +
    `<span class="key-mark" title="${esc(tip)}" aria-hidden="true">*</span>` +
    `<span class="sr-only">Key from a sub-device</span>`;
}

function refreshStatus(msg, kind) {
  clearTimeout(refreshStatusTimer);
  const el = $("refreshStatus");
  el.className = `refresh-status${kind ? ` ${kind}` : ""}`;
  if (!msg) { el.classList.add("hidden"); return; }
  el.textContent = msg;
  el.classList.remove("hidden");
}

function updateCacheStatus() {
  if (!appState.devicesCachedAt) return;
  $("updated").innerHTML = `${icon("clock")} Last updated: ${formatCacheAge(appState.devicesCachedAt)}`;
  $("updated").classList.remove("hidden");
}

function startCacheStatusTimer() {
  clearInterval(cacheStatusTimer);
  updateCacheStatus();
  cacheStatusTimer = setInterval(updateCacheStatus, 15000);
}

function stopCacheStatusTimer() {
  clearInterval(cacheStatusTimer);
  cacheStatusTimer = null;
  appState.devicesCachedAt = null;
  $("updated").classList.add("hidden");
}

// ---- devices ----
// Shows the full loading screen unless inline=true is requested.
async function loadDevices(msg, inline, refresh) {
  const generation = ++appState.deviceLoadGeneration;
  appState.exclusionLoadGeneration++;
  banner("");
  if (inline) {
    $("refreshBtn").disabled = true;
    $("refreshBtn").textContent = "Refreshing…";
    refreshStatus("Fetching latest from Tuya…");
    renderPanel();
  } else {
    refreshStatus("");
    $("loadingMsg").textContent = msg || "Loading your devices…";
    show("loading");
  }
  try {
    const [devicesResponse, preferencesResponse] = await Promise.all([
      api(refresh ? "/api/devices?refresh=1" : "/api/devices"), api("/api/device-exclusions"),
    ]);
    if (generation !== appState.deviceLoadGeneration) return;
    const { ok, status, body } = devicesResponse;
    if (!ok) {
      if (status === 401) {
        appState.deviceData = [];
        resetExclusions();
        appState.deviceChanges = appState.snapshotReason = null;
        resetLan();
        indexChanges();
        refreshStatus("");
        stopCacheStatusTimer();
        if (body.error === "session_invalid") {
          banner("Your login expired. Please scan the QR code again.");
        } else {
          banner("Please log in again.");
        }
        show("login");
      } else {
        if (inline) refreshStatus("Refresh failed.", "error");
        banner(body.error === "fetch_failed" ? "Could not load devices from Tuya. Try again." : (body.error || "Failed to load devices."));
        if (!appState.deviceData.length) stopCacheStatusTimer();
        show(appState.deviceData.length ? "devices" : "login");
      }
      return;
    }
    // Pair the snapshot with the same account's preferences before showing any
    // rows. A login in another tab can change the account between these reads.
    if (!preferencesResponse.ok || !validExclusions(preferencesResponse.body) ||
        body.account_id !== preferencesResponse.body.account_id) {
      if (appState.accountId && body.account_id !== appState.accountId) {
        appState.deviceData = []; resetExclusions(); resetLan(); closePanel();
        appState.deviceChanges = appState.snapshotReason = null; stopCacheStatusTimer(); indexChanges();
      }
      exclusionError(appState.exclusionsLoaded
        ? "Could not refresh device exclusions. Your last saved view is still shown. Try again."
        : "Could not load device exclusions. Retry to show your devices safely.");
      render(); show("devices"); return;
    }
    applyExclusions(preferencesResponse.body);
    appState.deviceData = body.devices || [];
    loadHomes(body);
    appState.deviceChanges = body.changes || null;
    appState.snapshotReason = body.stale ? (body.stale_reason || "fetch_failed") : null;
    appState.devicesCachedAt = body.cached_at || Date.now() / 1000;
    // Revalidate proof against the device list just fetched. Invalidate it first
    // so a failed LAN reload cannot preserve proof for a key that has changed.
    for (const result of Object.values(appState.lanData.results || {})) result.key_current = false;
    await loadLan();
    if (generation !== appState.deviceLoadGeneration) return;
    applyLan();
    indexChanges();
    indexDevices();
    render();
    startCacheStatusTimer();
    if (body.refresh_failed) {
      banner("Refresh failed. Showing cached device list.");
      if (inline) refreshStatus("Refresh failed.", "error");
    } else if (inline) {
      refreshStatus("Device list refreshed.", "ok");
      refreshStatusTimer = setTimeout(() => refreshStatus(""), 3000);
    }
    show("devices");
    // A scan still running (e.g. the page was reloaded mid-scan) picks up again.
    // One already watched carries on as it is, scan box shut or not.
    if (appState.lanData.job && appState.lanData.job.state === "running" && !isWatchingScan()) {
      watchScan(appState.lanData.job.kind === "scan");
    }
  } catch (err) {
    if (generation !== appState.deviceLoadGeneration) return;
    if (inline) refreshStatus("Refresh failed.", "error");
    banner(refresh ? "Refresh failed. Try again." : "Failed to load devices.");
    show(appState.deviceData.length ? "devices" : "login");
  } finally {
    if (generation !== appState.deviceLoadGeneration) return;
    $("refreshBtn").disabled = false;
    $("refreshBtn").textContent = "Refresh";
    if (inline) renderPanel();
  }
}

function renderLocalKeyHeader() {
  // The account's full list keeps the heading stable through filtering and collapse.
  const label = appState.deviceData.some(isSubDevice) ? "Local Key / Node ID" : "Local Key";
  return `<span class="key-heading"><span>${label}</span>${renderLocalKeyToggle("table")}</span>`;
}

// The filter matches every field, specs included, so the haystack is built once
// per fetch instead of on every keystroke.
function indexDevices() {
  searchIndex = new WeakMap();
  for (const d of appState.deviceData) {
    // Local times are rendered, not sent, so add them to match what is on screen.
    const times = TIME_KEYS.map(key => localTime(d.epochs && d.epochs[key])).join(" ");
    // The change and LAN badges are rendered, not sent, so filtering on
    // "key changed" or "busy" works.
    const change = CHANGE_BADGES[appState.changeById.get(d.id)] || "";
    const lan = lanStatus(d) ? lanBadgeText(d) : "";
    const lent = keySource(d) ? `${localKeyOf(d)} key from a sub-device` : "";
    const check = needsCheck(d) ? "check needed" : "";
    searchIndex.set(d, `${JSON.stringify(d)} ${times} ${change} ${lan} ${lent} ${check}`.toLowerCase());
  }
}

function computeList() {
  if (!appState.exclusionsLoaded) return [];
  let list = appState.deviceData.filter(d => inCurrentView(d.id));
  if (appState.deviceView === "excluded") {
    const currentIds = new Set(appState.deviceData.map(d => d.id));
    list = list.concat([...appState.excludedIds].filter(id => !currentIds.has(id))
      .map(id => ({ id, name: id, _missing: true })).filter(matchesFilters));
  }
  if (appState.filterText) list = list.filter(d => (d._missing ? `${d.id} not in the current device list`.toLowerCase()
    : searchIndex.get(d) || "").includes(appState.filterText));
  return sortDevices(list);
}

function sortDevices(list) {
  if (appState.sortState.key) {
    const { key, dir } = appState.sortState;
    const column = COLUMNS.find(c => c.key === key);
    const value = column && column.sortValue ? column.sortValue : (d => d[key]);
    list = [...list].sort((a, b) => cmp(value(a), value(b)) * dir);
  }
  return list;
}

// Keep the export list flat. The table adds a gateway for context when only
// its sub-devices match, then sorts gateways and each set of children separately.
function groupedRows(list) {
  const viewDevices = appState.deviceData.filter(d => inCurrentView(d.id));
  const byId = new Map(viewDevices.map(d => [d.id, d]));
  const matched = new Set(list.map(d => d.id));
  const roots = new Set(), children = new Map();
  for (const d of list) {
    const parent = isSubDevice(d) && byId.get(gatewayIdOf(d));
    if (parent) {
      if (!children.has(parent.id)) children.set(parent.id, []);
      children.get(parent.id).push(d);
      roots.add(parent.id);
    } else {
      roots.add(d.id);
    }
  }
  const collapsed = appState.filterText ? appState.filteredCollapsedGateways : appState.collapsedGateways;
  const rows = [];
  const rootDevices = [...viewDevices, ...list.filter(d => d._missing)].filter(d => roots.has(d.id));
  for (const d of sortDevices(rootDevices)) {
    const subs = children.get(d.id) || [];
    const expanded = !collapsed.has(d.id);
    rows.push({ device: d, children: subs, expanded, context: !matched.has(d.id) });
    for (const sub of subs) rows.push({ device: sub, parent: d.id, hidden: !expanded });
  }
  return rows;
}

function deviceRowId(d) { return `device-row-${encodeURIComponent(d.id)}`; }

function nameCell(entry) {
  const d = entry.device, subs = entry.children || [];
  let toggle = entry.parent ? '<span class="gateway-spacer" aria-hidden="true"></span>' : "";
  if (subs.length) {
    const label = `${entry.expanded ? "Collapse" : "Expand"} sub-devices of ${d.name || d.id}`;
    toggle = `<button class="icon-btn gateway-toggle" type="button" data-gateway-toggle="${esc(d.id)}" ` +
      `aria-expanded="${entry.expanded}" aria-controls="${subs.map(deviceRowId).join(" ")}" ` +
      `aria-label="${esc(label)}" title="${esc(label)}">${icon("chevron")}</button>`;
  }
  const countTip = `${plural(subs.length, "sub-device")} connected to this gateway`;
  return `<span class="device-name">${toggle}<span>${esc(d.name)}${changeBadge(d.id)}</span>` +
    (subs.length ? `<span class="badge muted gateway-count" title="${esc(countTip)}">${subs.length}</span>` : "") + `</span>`;
}

function renderHead() {
  const select = appState.selectionMode ? `<th class="select-col"><input id="selectAllDevices" type="checkbox" ` +
    `aria-label="Select all shown devices" title="Select all shown devices"></th>` : "";
  $("thead").innerHTML = `<tr>${select}` + COLUMNS.map(c => {
    const label = c.header ? c.header() : c.label;
    const cls = c.key === "name" ? " name-col" : "";
    if (!c.sortable) return `<th${cls ? ` class="${cls.trim()}"` : ""}>${label}</th>`;
    const active = appState.sortState.key === c.key;
    const arrow = active ? (appState.sortState.dir === 1 ? "▲" : "▼") : "";
    return `<th class="sortable${cls}${active ? " active" : ""}" data-key="${c.key}">${label}<span class="arrow">${arrow}</span></th>`;
  }).join("") + (appState.selectionMode ? "" : `<th class="details-col"><span class="sr-only">Details</span></th>`) + `</tr>`;
}

function renderRows(list) {
  const entries = groupedRows(list);
  const current = list.filter(d => !d._missing), missing = list.length - current.length;
  const online = current.filter(isOnline).length;
  const hidden = entries.filter(entry => entry.hidden).length;
  const context = entries.filter(entry => entry.context).length;
  $("count").innerHTML = `${list.length} ${appState.deviceView === "excluded" ? "excluded " : ""}device${list.length===1?"":"s"} ` +
    `<span class="muted">· ${online} online · ${current.length-online} offline` +
    (missing ? ` · ${missing} not in the current list` : "") +
    (hidden ? ` · ${plural(hidden, "sub-device")} hidden` : "") +
    (context ? ` · ${plural(context, "gateway")} shown for context` : "") + `</span>`;
  $("rows").innerHTML = entries.map(entry => {
    const d = entry.device;
    const select = appState.selectionMode ? `<td class="select-col"><input type="checkbox" data-device-select="${esc(d.id)}" ` +
      `aria-label="Select ${esc(d.name || d.id)}"${entry.context ? " disabled" : ""}></td>` : "";
    const cells = COLUMNS.map(c => {
      const cls = [c.cls, c.key === "name" ? "name-col" : ""].filter(Boolean).join(" ");
      const val = d._missing ? c.key === "name" ? `<span class="device-name">${esc(d.id)}</span>`
        : c.key === "id" ? esc(d.id) : c.key === "online" ? '<span class="muted">Not in the current device list</span>' : dash()
        : c.key === "name" ? nameCell(entry) : c.render ? c.render(d) : esc(d[c.key]);
      return `<td data-column="${c.key}"${cls ? ` class="${cls}"` : ""}>${val}</td>`;
    }).join("");
    const label = `Show details for ${esc(d.name || d.id)}`;
    const open = appState.selectionMode ? "" : d._missing ? '<td class="details-col"></td>'
      : `<td class="details-col"><button class="icon-btn" type="button" data-open ` +
        `aria-label="${label}" title="Details">${icon("chevron")}</button></td>`;
    const classes = [entry.parent ? "sub-device-row" : "", d.id === appState.selectedId ? "selected" : "", d._missing ? "missing-device" : ""]
      .filter(Boolean).join(" ");
    return `<tr id="${deviceRowId(d)}" data-id="${esc(d.id)}" class="${classes}"` +
      (d._missing ? " data-missing" : "") +
      (entry.parent ? ` data-gateway-id="${esc(entry.parent)}"` : "") +
      (entry.hidden ? " hidden" : "") + `>${select}${cells}${open}</tr>`;
  }).join("");
  $("tableScroll").classList.toggle("hidden", !entries.length);
  $("emptyDevices").classList.toggle("hidden", !!entries.length);
  $("emptyDevices").innerHTML = entries.length ? "" : emptyListMessage();
  // The footnote for the * on a key from a sub-device, while one is listed.
  $("keyMarkNote").classList.toggle("hidden", !entries.some(entry =>
    !entry.hidden && !isSubDevice(entry.device) && keySource(entry.device)));
  syncSelectionUi(selectionIds(entries));
}

function emptyListMessage() {
  if (!appState.exclusionsLoaded) return "Device exclusions must be loaded before showing your devices.";
  if (appState.filterText) return "No devices match your filter.";
  const scope = appState.selectedHome === "unknown" ? " with an unknown home" : appState.selectedHome ? " in this home" : "";
  const roomScope = appState.selectedRoom === "none" ? " without a room" : appState.selectedRoom === "unknown"
    ? " with an unknown room" : appState.selectedRoom ? " in this room" : "";
  const location = scope + roomScope;
  if (appState.deviceView === "excluded") return `No excluded devices${location}.`;
  const inScope = appState.deviceData.filter(matchesFilters);
  if (inScope.length && inScope.every(d => appState.excludedIds.has(d.id)))
    return `All devices${location} are excluded.<br><button type="button" data-view-excluded>View excluded devices</button>`;
  if (appState.selectedStatus || appState.selectedCategory) return "No devices match your filters.";
  if (appState.selectedHome || appState.selectedRoom) return `No devices${location}.`;
  return "No devices in this account.";
}

// Every flat field of the fetched devices, in the order the server sent them.
function csvColumns() {
  const skip = new Set(["epochs", ...SPEC_KEYS]);
  const cols = [];
  for (const d of appState.deviceData) {
    for (const [key, value] of Object.entries(d)) {
      if (skip.has(key) || cols.includes(key)) continue;
      if (value !== null && typeof value === "object") continue;   // specs don't fit a cell
      cols.push(key);
    }
  }
  return cols;
}

function syncTableSelection(ids, disabled) {
  const available = new Set(ids), count = appState.bulkSelectedIds.size;
  const selectAll = $("selectAllDevices");
  if (selectAll) {
    selectAll.checked = !!ids.length && count === ids.length;
    selectAll.indeterminate = count > 0 && count < ids.length;
    selectAll.disabled = disabled || !ids.length;
  }
  for (const input of $("rows").querySelectorAll("[data-device-select]")) {
    input.checked = appState.bulkSelectedIds.has(input.dataset.deviceSelect);
    input.disabled = disabled || !available.has(input.dataset.deviceSelect);
    input.closest("tr").classList.toggle("bulk-selected", input.checked);
    input.closest("tr").classList.toggle("selection-disabled", input.disabled);
  }
  $("rows").classList.toggle("selection-mode", appState.selectionMode);
}

let initialized = false;
function initDevices(actions = {}) {
  if (initialized) return;
  initialized = true;
  ({ applyExclusions, banner, changeBadge, closePanel, exclusionError, inCurrentView, indexChanges, isWatchingScan, loadHomes, loadLan, matchesFilters, openPanel, render, renderPanel, resetExclusions, resetLan, selectAllDevices, selectionIds, setDeviceSelected, show, syncSelectionUi, validExclusions, watchScan } = actions);
  // sort on header click
  $("thead").addEventListener("click", (e) => {
    if (e.target.closest("[data-key-toggle]")) return;   // handled by the toggle listener
    const th = e.target.closest("th[data-key]");
    if (!th) return;
    const key = th.dataset.key;
    if (appState.sortState.key === key) appState.sortState.dir *= -1;   // toggle direction
    else { appState.sortState.key = key; appState.sortState.dir = 1; }  // new column, ascending
    render();
  });
  $("thead").addEventListener("change", (e) => {
    if (e.target.id === "selectAllDevices") selectAllDevices(e.target.checked);
  });
  $("rows").addEventListener("change", (e) => {
    const input = e.target.closest("[data-device-select]");
    if (input && !input.disabled) setDeviceSelected(input.dataset.deviceSelect, input.checked);
  });
  // Rows toggle selection in selection mode and open details while browsing.
  $("rows").addEventListener("click", (e) => {
    if (e.target.closest("[data-device-select], [data-view-excluded]")) return;
    const toggle = e.target.closest("[data-gateway-toggle]");
    if (toggle) {
      const collapsed = appState.filterText ? appState.filteredCollapsedGateways : appState.collapsedGateways;
      const id = toggle.dataset.gatewayToggle;
      if (collapsed.has(id)) collapsed.delete(id); else collapsed.add(id);
      renderRows(computeList());
      // Repainting replaces the button. Keep keyboard focus on its replacement.
      const row = document.getElementById(`device-row-${encodeURIComponent(id)}`);
      if (row) row.querySelector("[data-gateway-toggle]").focus();
      return;
    }
    if (e.target.closest("[data-copy]")) return;   // copying a key isn't a row click
    const tr = e.target.closest("tr[data-id]");
    if (!tr) return;
    if (appState.selectionMode) {
      const input = tr.querySelector("[data-device-select]");
      if (input && !input.disabled) {
        input.click();
        input.focus({ preventScroll: true });
      }
      return;
    }
    if (tr.hasAttribute("data-missing")) return;
    appState.panelOpener = e.target.closest("[data-open]") || tr;
    openPanel(tr.dataset.id);
  });
  $("refreshBtn").onclick = () => loadDevices("Updating device list from Tuya…", false, true);
  // CSV export (client-side) — respects the current filter + sort order
  $("csvBtn").onclick = () => {
    if (!appState.exclusionsLoaded) return;
    const cols = csvColumns();
    const rows = [cols.join(",")].concat(computeList().filter(d => !d._missing).map(d =>
      cols.map(c => `"${String((c === "local_key" ? localKeyOf(d) : d[c]) ?? "").replace(/"/g,'""')}"`).join(",")));
    const blob = new Blob([rows.join("\n")], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = appState.deviceView === "excluded" ? "tuya-local-key-excluded.csv" : "tuya-local-key.csv"; a.click();
    URL.revokeObjectURL(a.href);
  };
}

export {
  computeList,
  groupedRows,
  indexDevices,
  initDevices,
  loadDevices,
  refreshStatus,
  renderHead,
  renderRows,
  stopCacheStatusTimer,
  syncTableSelection,
};
