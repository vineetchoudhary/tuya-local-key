// Filters.
import { api } from "../shared/api.js";
import { categoryLabel, categoryOf } from "../shared/categories.js";
import { $, views } from "../shared/dom.js";
import { plural } from "../shared/format.js";
import { esc } from "../shared/html.js";
import { icon } from "../shared/icons.js";
import { lanFilterStatus } from "../shared/model.js";
import { appState } from "../shared/state.js";

// Cross-feature actions are connected by app.js.
let closeMoreMenu, closePanel, computeList, focusPanelPage, groupedRows, loadDevices, render, renderHead, renderRows, resetLan, syncTableSelection;

// Filters never change the source device records used for setup and scans.
function homeIdsOf(d) {
  return Array.isArray(d && d.home_ids) ? d.home_ids.filter(id => typeof id === "string" && id) : [];
}

function matchesHome(d) {
  if (!appState.selectedHome) return true;
  const ids = homeIdsOf(d);
  return appState.selectedHome === "unknown" ? !ids.length : ids.includes(appState.selectedHome.slice(5));
}

function roomStatus(d) {
  if (d && d.room_status === "assigned" && typeof d.room_id === "string" && d.room_id) return "assigned";
  return d && d.room_status === "unassigned" ? "unassigned" : "unknown";
}

function matchesRoom(d) {
  if (!appState.selectedRoom) return true;
  const status = roomStatus(d);
  if (appState.selectedRoom === "none") return status === "unassigned";
  if (appState.selectedRoom === "unknown") return status === "unknown";
  return status === "assigned" && d.room_id === appState.selectedRoom.slice(5);
}

const STATUS_GROUPS = [
  { label: "Cloud", options: [
    { value: "cloud:online", label: "Online" },
    { value: "cloud:offline", label: "Offline" },
    { value: "cloud:unknown", label: "Unknown" },
  ] },
  { label: "LAN", options: [
    { value: "lan:ok", label: "Reachable" },
    { value: "lan:via_gateway", label: "Via gateway" },
    { value: "lan:offline", label: "Offline (via gateway)" },
    { value: "lan:busy", label: "Busy" },
    { value: "lan:key_mismatch", label: "Key mismatch" },
    { value: "lan:unreachable", label: "Unreachable" },
    { value: "lan:not_found", label: "Not found" },
    { value: "lan:gateway_not_found", label: "Gateway not found" },
    { value: "lan:check_needed", label: "Check needed" },
    { value: "lan:not_checked", label: "Not checked" },
  ] },
];
const STATUS_VALUES = new Set(["", ...STATUS_GROUPS.flatMap(group => group.options.map(option => option.value))]);

function matchesStatus(d) {
  if (!appState.selectedStatus) return true;
  const device = d || {};
  if (appState.selectedStatus.startsWith("cloud:")) {
    const status = device.online === true ? "online" : device.online === false ? "offline" : "unknown";
    return appState.selectedStatus === `cloud:${status}`;
  }
  return appState.selectedStatus === `lan:${lanFilterStatus(device)}`;
}

function matchesCategory(d) {
  if (!appState.selectedCategory) return true;
  const code = categoryOf(d);
  return appState.selectedCategory === "unknown" ? !code : appState.selectedCategory === `category:${code}`;
}

function matchesFilters(d) { return matchesHome(d) && matchesRoom(d) && matchesStatus(d) && matchesCategory(d); }

function inCurrentView(id, fallback) {
  return appState.excludedIds.has(id) === (appState.deviceView === "excluded") && matchesFilters(appState.deviceData.find(d => d.id === id) || fallback);
}

function homePreferenceKey() { return `tuya-local-key.home.${appState.accountId}`; }
function roomPreferenceKey() { return `tuya-local-key.room.${appState.accountId}`; }
function deviceFiltersPreferenceKey() { return `tuya-local-key.filters.${appState.accountId}`; }

function rememberDeviceFilters() {
  try { localStorage.setItem(deviceFiltersPreferenceKey(), JSON.stringify({
    status: appState.selectedStatus, category: appState.selectedCategory,
  })); } catch (e) {}
}

function loadDeviceFilters() {
  appState.selectedStatus = appState.selectedCategory = "";
  try {
    const saved = JSON.parse(localStorage.getItem(deviceFiltersPreferenceKey()));
    if (!saved || typeof saved !== "object") return;
    if (STATUS_VALUES.has(saved.status)) appState.selectedStatus = saved.status;
    if (typeof saved.category === "string" && (saved.category === "unknown" ||
        (saved.category.startsWith("category:") && saved.category.slice(9).trim())))
      appState.selectedCategory = saved.category;
  } catch (e) {}
}

function rememberHome() {
  appState.preferredHome = appState.selectedHome;
  try { localStorage.setItem(homePreferenceKey(), appState.selectedHome); } catch (e) {}
}

function rememberRoom() {
  appState.preferredRoom = appState.selectedRoom;
  try { localStorage.setItem(roomPreferenceKey(), appState.selectedRoom); } catch (e) {}
}

function roomOptions() {
  const options = [{ value: "", label: "All rooms" }];
  if (!appState.roomsLoaded) return options;
  // Build choices from the full home scope, independent of search/exclusions.
  const devices = appState.deviceData.filter(matchesHome), rooms = new Map();
  for (const d of devices) {
    if (roomStatus(d) === "assigned" && !rooms.has(d.room_id)) rooms.set(d.room_id, {
      id: d.room_id, name: typeof d.room_name === "string" && d.room_name.trim() ? d.room_name : `Room ${d.room_id}`,
      order: Number.isFinite(Number(d.room_display_order)) ? Number(d.room_display_order) : 0,
    });
  }
  const names = new Map();
  rooms.forEach(room => names.set(room.name, (names.get(room.name) || 0) + 1));
  for (const room of [...rooms.values()].sort((a, b) => a.order - b.order || a.name.localeCompare(b.name) || a.id.localeCompare(b.id)))
    options.push({ value: `room:${room.id}`, label: names.get(room.name) > 1 ? `${room.name} (${room.id})` : room.name });
  if (devices.some(d => roomStatus(d) === "unassigned")) options.push({ value: "none", label: "No room" });
  if (devices.some(d => roomStatus(d) === "unknown") || [...appState.excludedIds].some(id =>
    !appState.deviceData.some(d => d.id === id) && matchesHome(null)))
    options.push({ value: "unknown", label: "Unknown room" });
  return options;
}

function loadRooms(body) {
  appState.roomsLoaded = body.rooms_loaded === true;
  const previousRoom = appState.selectedRoom;
  appState.selectedRoom = roomOptions().some(option => option.value === appState.preferredRoom) ? appState.preferredRoom : "";
  if (appState.selectedRoom !== previousRoom) {
    abandonFailedExclusionSave(); appState.bulkSelectedIds.clear(); appState.filteredCollapsedGateways.clear();
  }
  // Old/partial snapshots cannot disprove a remembered room assignment.
  if (appState.roomsLoaded && !appState.deviceData.filter(matchesHome).some(d => roomStatus(d) === "unknown") &&
      appState.selectedRoom !== appState.preferredRoom) rememberRoom();
}

function unknownHomeNeeded() {
  return appState.deviceData.some(d => !homeIdsOf(d).length) ||
    [...appState.excludedIds].some(id => !appState.deviceData.some(d => d.id === id));
}

function homeOptions() {
  const options = [{ value: "", label: "All homes" }];
  if (appState.homes === null) return options;
  const names = new Map();
  appState.homes.forEach(home => names.set(home.name, (names.get(home.name) || 0) + 1));
  for (const home of appState.homes) options.push({ value: `home:${home.id}`,
    label: names.get(home.name) > 1 ? `${home.name} (${home.id})` : home.name });
  if (unknownHomeNeeded()) options.push({ value: "unknown", label: "Unknown home" });
  return options;
}

function loadHomes(body) {
  const ids = new Set();
  appState.homes = Array.isArray(body.homes) ? body.homes.filter(home => home && typeof home.id === "string" &&
    home.id && !ids.has(home.id) && ids.add(home.id)).map(home => ({
      id: home.id, name: typeof home.name === "string" && home.name.trim() ? home.name : `Home ${home.id}`,
    })) : null;
  const previousHome = appState.selectedHome;
  appState.selectedHome = appState.homes !== null && homeOptions().some(option => option.value === appState.preferredHome) ? appState.preferredHome : "";
  if (appState.selectedHome !== previousHome) {
    abandonFailedExclusionSave(); appState.bulkSelectedIds.clear(); appState.filteredCollapsedGateways.clear();
  }
  // An older cache cannot disprove the saved preference. Keep it until a fresh
  // home list arrives, without pretending its device memberships are known.
  if (appState.homes !== null && appState.selectedHome !== appState.preferredHome) rememberHome();
  loadRooms(body);
}

function renderHomeFilter() {
  const options = homeOptions();
  if (!options.some(option => option.value === appState.selectedHome)) {
    abandonFailedExclusionSave(); appState.bulkSelectedIds.clear(); appState.selectedHome = ""; rememberHome();
  }
  $("homeFilter").innerHTML = options.map(option => `<option value="${esc(option.value)}">${esc(option.label)}</option>`).join("");
  $("homeFilter").value = appState.selectedHome;
  $("homeFilter").disabled = !appState.exclusionsLoaded || appState.exclusionBusy || options.length < 2;
  $("homeHint").classList.toggle("hidden", !appState.exclusionsLoaded || appState.homes !== null || !appState.deviceData.length);
  const refresh = $("homeHint").querySelector("[data-refresh-homes]");
  refresh.disabled = $("refreshBtn").disabled;
  refresh.textContent = refresh.disabled ? "Refreshing…" : "Refresh";
  renderRoomFilter();
  renderDeviceFilters();
}

function categoryOptions() {
  // Keep choices stable when another filter/search or an exclusion hides a row.
  const codes = new Set(appState.deviceData.map(categoryOf).filter(Boolean));
  if (appState.selectedCategory.startsWith("category:")) codes.add(appState.selectedCategory.slice(9));
  const options = [...codes].map(code => ({ value: `category:${code}`, label: categoryLabel(code) }))
    .sort((a, b) => a.label.localeCompare(b.label));
  if (appState.selectedCategory === "unknown" || appState.deviceData.some(d => !categoryOf(d)) ||
      [...appState.excludedIds].some(id => !appState.deviceData.some(d => d.id === id)))
    options.push({ value: "unknown", label: "Unknown category" });
  return [{ value: "", label: "All categories" }, ...options];
}

function optionMarkup(option) {
  return `<option value="${esc(option.value)}">${esc(option.label)}</option>`;
}

function renderDeviceFilters() {
  $("statusFilter").innerHTML = '<option value="">All statuses</option>' + STATUS_GROUPS.map(group =>
    `<optgroup label="${esc(group.label)}">${group.options.map(optionMarkup).join("")}</optgroup>`).join("");
  $("statusFilter").value = appState.selectedStatus;
  $("categoryFilter").innerHTML = categoryOptions().map(optionMarkup).join("");
  $("categoryFilter").value = appState.selectedCategory;
}

function renderRoomFilter() {
  const options = roomOptions();
  if (!options.some(option => option.value === appState.selectedRoom)) {
    abandonFailedExclusionSave(); appState.bulkSelectedIds.clear(); appState.selectedRoom = ""; rememberRoom();
  }
  $("roomFilter").innerHTML = options.map(option => `<option value="${esc(option.value)}">${esc(option.label)}</option>`).join("");
  $("roomFilter").value = appState.selectedRoom;
  $("roomFilter").disabled = !appState.exclusionsLoaded || appState.exclusionBusy || options.length < 2;
  const unknown = appState.deviceData.filter(matchesHome).some(d => roomStatus(d) === "unknown");
  const showHint = appState.exclusionsLoaded && appState.deviceData.length && (!appState.roomsLoaded || unknown);
  $("roomHint").classList.toggle("hidden", !showHint);
  $("roomHintText").textContent = appState.roomsLoaded ? "Some rooms could not be loaded." : "Room information is not in this saved list.";
  const refresh = $("roomHint").querySelector("[data-refresh-rooms]");
  refresh.disabled = $("refreshBtn").disabled;
  refresh.textContent = refresh.disabled ? "Refreshing…" : "Refresh";
}

function positionFiltersPopover() {
  const popover = $("filtersPopover");
  const button = $("filtersBtn");
  // Refresh temporarily hides the device view. Measuring its hidden anchor
  // would place the popover at the viewport origin until the next interaction.
  if (popover.classList.contains("hidden") || !button.getClientRects().length) return;
  const anchor = button.getBoundingClientRect();
  const { width, height } = popover.getBoundingClientRect();
  const left = Math.max(16, Math.min(anchor.right - width, innerWidth - width - 16));
  let top = anchor.bottom + 8;
  if (top + height > innerHeight - 16 && anchor.top - height - 8 >= 16) top = anchor.top - height - 8;
  popover.style.left = `${left}px`;
  popover.style.top = `${Math.max(16, Math.min(top, innerHeight - height - 16))}px`;
}

function closeFilters(refocus = false) {
  $("filtersPopover").classList.add("hidden");
  $("filtersBtn").setAttribute("aria-expanded", "false");
  if (refocus) $("filtersBtn").focus();
}

function openFilters() {
  closeMoreMenu();
  $("filtersPopover").classList.remove("hidden");
  $("filtersBtn").setAttribute("aria-expanded", "true");
  positionFiltersPopover();
  const target = [$("homeFilter"), $("roomFilter"), $("statusFilter"), $("categoryFilter"), $("selectDevicesBtn")].find(control => !control.disabled);
  (target || $("filtersClose")).focus();
}

function validExclusions(body) {
  return typeof body.account_id === "string" && !!body.account_id &&
    Number.isInteger(body.revision) && body.revision >= 0 &&
    Array.isArray(body.excluded_ids) && body.excluded_ids.every(id => typeof id === "string" && id.length);
}

function resetExclusions() {
  closeFilters();
  appState.accountGeneration++; appState.exclusionLoadGeneration++;
  appState.excludedIds = new Set(); appState.bulkSelectedIds.clear(); appState.deviceView = "active";
  appState.selectionMode = false; appState.homes = null; appState.selectedHome = appState.preferredHome = "";
  appState.roomsLoaded = false; appState.selectedRoom = appState.preferredRoom = "";
  appState.selectedStatus = appState.selectedCategory = "";
  appState.exclusionsLoaded = false; appState.exclusionRevision = -1; appState.accountId = null;
  appState.exclusionBusy = false; appState.exclusionFailure = ""; appState.pendingExclusionSave = null;
}

function applyExclusions(body) {
  if (!validExclusions(body)) return false;
  if (appState.accountId !== body.account_id) {
    closeFilters();
    appState.accountGeneration++;
    resetLan();
    closePanel(); appState.bulkSelectedIds.clear(); appState.deviceView = "active"; appState.selectionMode = false;
    appState.collapsedGateways.clear(); appState.filteredCollapsedGateways.clear();
    appState.tableKeysVisible = false; appState.panelKeyVisible = false;
    appState.exclusionBusy = false; appState.pendingExclusionSave = null;
    appState.exclusionRevision = -1; appState.excludedIds = new Set();
    appState.accountId = body.account_id;
    loadDeviceFilters();
    try { appState.preferredHome = localStorage.getItem(homePreferenceKey()) || ""; } catch (e) { appState.preferredHome = ""; }
    try { appState.preferredRoom = localStorage.getItem(roomPreferenceKey()) || ""; } catch (e) { appState.preferredRoom = ""; }
    appState.selectedHome = ""; appState.homes = null;
    appState.selectedRoom = ""; appState.roomsLoaded = false;
  }
  // A GET started before a successful PATCH must not put the older set back.
  if (body.revision < appState.exclusionRevision) return true;
  const previouslyVisible = appState.selectedId && inCurrentView(appState.selectedId);
  appState.excludedIds = new Set(body.excluded_ids); appState.exclusionRevision = body.revision;
  appState.exclusionsLoaded = true; appState.exclusionFailure = "";
  if (previouslyVisible && !inCurrentView(appState.selectedId)) closePanel();
  return true;
}

function exclusionError(message) {
  appState.exclusionFailure = message;
  $("exclusionErrorText").textContent = message;
  $("exclusionError").classList.toggle("hidden", !message);
}

function abandonFailedExclusionSave() {
  if (!appState.exclusionBusy && appState.pendingExclusionSave) {
    appState.pendingExclusionSave = null; exclusionError("");
  }
}

function selectionIds(entries = groupedRows(computeList())) {
  return entries.filter(entry => !entry.hidden && !entry.context).map(entry => entry.device.id);
}

function syncSelectionUi(ids = selectionIds()) {
  const available = new Set(ids);
  for (const id of appState.bulkSelectedIds) if (!available.has(id)) appState.bulkSelectedIds.delete(id);
  const count = appState.bulkSelectedIds.size, disabled = !appState.exclusionsLoaded || appState.exclusionBusy;
  $("bulkActions").classList.toggle("hidden", !appState.selectionMode);
  $("selectionCount").textContent = `${count} selected`;
  $("excludeSelected").classList.toggle("hidden", appState.deviceView !== "active");
  $("restoreSelected").classList.toggle("hidden", appState.deviceView !== "excluded");
  $("excludeSelected").textContent = appState.exclusionBusy ? "Saving…" : "Exclude selected";
  $("restoreSelected").textContent = appState.exclusionBusy ? "Saving…" : "Restore selected";
  for (const id of ["excludeSelected", "restoreSelected", "clearSelection"]) $(id).disabled = disabled || !count;
  $("clearSelection").classList.toggle("hidden", !count);
  $("selectionDoneBtn").disabled = disabled;
  $("selectDevicesBtn").disabled = disabled || appState.selectionMode || !ids.length;
  $("filtersBtn").disabled = disabled;
  $("excludedViewBtn").textContent = `Excluded devices (${appState.excludedIds.size})`;
  $("excludedViewBtn").classList.toggle("hidden", !appState.excludedIds.size && appState.deviceView !== "excluded");
  $("excludedViewBtn").setAttribute("aria-pressed", String(appState.deviceView === "excluded"));
  $("excludedViewBtn").disabled = disabled;
  const filterCount = appState.excludedIds.size + Number(!!appState.selectedHome) + Number(!!appState.selectedRoom) +
    Number(!!appState.selectedStatus) + Number(!!appState.selectedCategory);
  const filterSummary = [appState.excludedIds.size ? plural(appState.excludedIds.size, "excluded device") : "",
    appState.selectedHome ? "1 home filter" : "", appState.selectedRoom ? "1 room filter" : "",
    appState.selectedStatus ? "1 status filter" : "", appState.selectedCategory ? "1 category filter" : ""].filter(Boolean).join(", ");
  $("filterCount").textContent = filterCount;
  $("filterCount").classList.toggle("hidden", !filterCount);
  $("filtersBtn").dataset.active = String(!!filterCount);
  $("filtersBtn").setAttribute("aria-label", filterCount ? `Filter (${filterCount}): ${filterSummary}` : "Filter");
  $("clearAllFilters").disabled = disabled || !(filterCount || appState.filterText || appState.preferredHome || appState.preferredRoom || appState.selectionMode || appState.deviceView !== "active");
  $("clearAllFilters").textContent = appState.exclusionBusy && appState.pendingExclusionSave && appState.pendingExclusionSave.clearFilters ? "Clearing…" : "Clear all";
  $("viewActions").classList.toggle("hidden", appState.deviceView !== "excluded");
  $("backToDevicesBtn").disabled = disabled;
  $("restoreAll").classList.toggle("hidden", appState.deviceView !== "excluded" || !appState.excludedIds.size);
  $("restoreAll").disabled = disabled || !appState.excludedIds.size;
  $("restoreAll").textContent = appState.selectedHome ? "Restore all (all homes)" : appState.selectedRoom ? "Restore all (all rooms)"
    : appState.selectedStatus || appState.selectedCategory ? "Restore all (all filters)" : "Restore all";
  $("restoreAll").title = "Restore every excluded device in this account, across all filters";
  $("retryExclusions").disabled = appState.exclusionBusy;
  $("filter").disabled = !appState.exclusionsLoaded;
  $("csvBtn").disabled = !appState.exclusionsLoaded;
  $("scanBtn").disabled = !appState.exclusionsLoaded;
  $("homeFilter").disabled = disabled || homeOptions().length < 2;
  $("roomFilter").disabled = disabled || roomOptions().length < 2;
  $("statusFilter").disabled = disabled;
  $("categoryFilter").disabled = disabled || categoryOptions().length < 2;
  syncTableSelection(ids, disabled);
  exclusionError(appState.exclusionFailure);
  positionFiltersPopover();
}

function setDeviceView(view, clearFilter = false) {
  if (!appState.exclusionsLoaded || appState.exclusionBusy) return;
  abandonFailedExclusionSave();
  closeFilters();
  appState.deviceView = view; appState.selectionMode = false; appState.bulkSelectedIds.clear(); appState.filteredCollapsedGateways.clear();
  if (clearFilter) { appState.filterText = ""; $("filter").value = ""; }
  if (appState.selectedId && !inCurrentView(appState.selectedId)) closePanel();
  render(); (view === "excluded" ? $("backToDevicesBtn") : $("filtersBtn")).focus();
}

function setSelectionMode(active) {
  if (!appState.exclusionsLoaded || appState.exclusionBusy) return;
  abandonFailedExclusionSave(); appState.bulkSelectedIds.clear(); appState.selectionMode = active;
  closeFilters();
  renderHead(); renderRows(computeList());
  const target = active ? $("selectAllDevices") : $("filtersBtn");
  (target && !target.disabled && target.getClientRects().length ? target : $("selectionDoneBtn")).focus();
}

async function refreshExclusions() {
  if (!appState.exclusionsLoaded || appState.exclusionBusy) return;
  const generation = ++appState.exclusionLoadGeneration, session = appState.accountGeneration, expectedAccount = appState.accountId;
  const result = await api("/api/device-exclusions");
  if (generation !== appState.exclusionLoadGeneration || session !== appState.accountGeneration) return;
  if (!result.ok || !validExclusions(result.body)) {
    exclusionError("Could not refresh device exclusions. Your last saved view is still shown.");
    return;
  }
  if (result.body.account_id !== expectedAccount) { loadDevices(); return; }
  applyExclusions(result.body); render();
}

async function saveExclusions(exclude = [], restore = [], restoreAll = false, clearFilters = false) {
  if (!appState.exclusionsLoaded || appState.exclusionBusy) return;
  const expectedAccount = appState.accountId, session = appState.accountGeneration;
  const focusWasInPanel = $("panel").contains(document.activeElement);
  appState.exclusionLoadGeneration++; appState.exclusionBusy = true;
  const affectedCount = restoreAll ? appState.excludedIds.size : exclude.length || restore.length;
  appState.pendingExclusionSave = { exclude: [...exclude], restore: [...restore], restoreAll, clearFilters };
  exclusionError(""); syncSelectionUi();
  const result = await api("/api/device-exclusions", {
    method: "PATCH", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(restoreAll ? { restore_all: true, account_id: expectedAccount }
      : { exclude, restore, account_id: expectedAccount }),
  });
  if (session !== appState.accountGeneration || expectedAccount !== appState.accountId) return;
  appState.exclusionBusy = false;
  if (!result.ok || !validExclusions(result.body) || result.body.account_id !== expectedAccount) {
    if (result.body.error === "account_changed" || (result.ok && result.body.account_id !== expectedAccount)) {
      appState.pendingExclusionSave = null;
      exclusionError("Your account changed. Refresh exclusions before trying again.");
    } else {
      exclusionError(clearFilters ? "Could not clear all filters. Your filters and exclusions have been kept. Try again."
        : "Could not save device exclusions. Your list and selection have been kept. Try again.");
    }
    syncSelectionUi(); return;
  }
  applyExclusions(result.body); appState.bulkSelectedIds.clear(); appState.pendingExclusionSave = null; appState.selectionMode = false;
  if (clearFilters) { resetDeviceFilters(); return; }
  $("exclusionStatus").textContent = `${plural(affectedCount, "device")} ${exclude.length ? "excluded" : "restored"}.`;
  render();
  const focused = document.activeElement;
  if (!focused || focused === document.body || !focused.isConnected || !focused.getClientRects().length) {
    if (appState.selectedId && focusWasInPanel) focusPanelPage();
    else $("filter").focus();
  }
}

function changeHomeFilter(value) {
  abandonFailedExclusionSave(); appState.bulkSelectedIds.clear(); appState.filteredCollapsedGateways.clear();
  appState.selectedHome = value; rememberHome();
  if (!roomOptions().some(option => option.value === appState.selectedRoom)) appState.selectedRoom = "";
  rememberRoom();
  if (appState.selectedId && !inCurrentView(appState.selectedId)) closePanel();
  render(); $("homeFilter").focus();
}

function changeRoomFilter(value) {
  abandonFailedExclusionSave(); appState.bulkSelectedIds.clear(); appState.filteredCollapsedGateways.clear();
  appState.selectedRoom = value; rememberRoom();
  if (appState.selectedId && !inCurrentView(appState.selectedId)) closePanel();
  render(); $("roomFilter").focus();
}

function changeDeviceFilter(field, control, value) {
  abandonFailedExclusionSave(); appState.bulkSelectedIds.clear(); appState.filteredCollapsedGateways.clear();
  appState[field] = value; rememberDeviceFilters();
  if (appState.selectedId && !inCurrentView(appState.selectedId)) closePanel();
  render(); $(control).focus();
}

function resetDeviceFilters() {
  appState.selectedHome = ""; rememberHome();
  appState.selectedRoom = ""; rememberRoom();
  appState.selectedStatus = appState.selectedCategory = ""; rememberDeviceFilters();
  appState.filterText = ""; $("filter").value = "";
  appState.deviceView = "active"; appState.selectionMode = false; appState.bulkSelectedIds.clear(); appState.filteredCollapsedGateways.clear();
  if (appState.selectedId && !inCurrentView(appState.selectedId)) closePanel();
  $("exclusionStatus").textContent = "All filters cleared.";
  render();
  const target = $("filtersPopover").classList.contains("hidden") ? $("filtersBtn")
    : $("homeFilter").disabled ? $("filtersClose") : $("homeFilter");
  target.focus();
}

function setDeviceSelected(id, selected) {
  if (appState.exclusionBusy || !appState.exclusionsLoaded || !appState.selectionMode || !selectionIds().includes(id)) return;
  abandonFailedExclusionSave();
  if (selected) appState.bulkSelectedIds.add(id); else appState.bulkSelectedIds.delete(id);
  syncSelectionUi();
}

function selectAllDevices(selected) {
  if (appState.exclusionBusy || !appState.exclusionsLoaded || !appState.selectionMode) return;
  abandonFailedExclusionSave();
  const ids = selectionIds();
  for (const id of ids) {
    if (selected) appState.bulkSelectedIds.add(id); else appState.bulkSelectedIds.delete(id);
  }
  syncSelectionUi(ids);
}

let initialized = false;
function initFilters(actions = {}) {
  if (initialized) return;
  initialized = true;
  ({ closeMoreMenu, closePanel, computeList, focusPanelPage, groupedRows, loadDevices, render, renderHead, renderRows, resetLan, syncTableSelection } = actions);
  $("excludedViewBtn").onclick = () => setDeviceView(appState.deviceView === "active" ? "excluded" : "active");
  $("backToDevicesBtn").onclick = () => setDeviceView("active");
  $("selectDevicesBtn").onclick = () => setSelectionMode(true);
  $("selectionDoneBtn").onclick = () => setSelectionMode(false);
  $("excludeSelected").onclick = () => saveExclusions([...appState.bulkSelectedIds]);
  $("restoreSelected").onclick = () => saveExclusions([], [...appState.bulkSelectedIds]);
  $("restoreAll").onclick = () => saveExclusions([], [], true);
  $("clearSelection").onclick = () => {
    abandonFailedExclusionSave(); appState.bulkSelectedIds.clear(); syncSelectionUi(); $("selectAllDevices").focus();
  };
  $("homeFilter").onchange = (e) => changeHomeFilter(e.target.value);
  $("roomFilter").onchange = (e) => changeRoomFilter(e.target.value);
  $("statusFilter").onchange = (e) => changeDeviceFilter("selectedStatus", "statusFilter", e.target.value);
  $("categoryFilter").onchange = (e) => changeDeviceFilter("selectedCategory", "categoryFilter", e.target.value);
  $("clearAllFilters").onclick = () => {
    if (!appState.exclusionsLoaded || appState.exclusionBusy) return;
    abandonFailedExclusionSave();
    if (appState.excludedIds.size) saveExclusions([], [], true, true);
    else resetDeviceFilters();
  };
  $("filtersIcon").innerHTML = icon("filters");
  $("filtersClose").innerHTML = icon("close");
  $("homeChevron").innerHTML = icon("chevron");
  $("roomChevron").innerHTML = icon("chevron");
  $("statusChevron").innerHTML = icon("chevron");
  $("categoryChevron").innerHTML = icon("chevron");
  $("filtersBtn").onclick = () => {
    if ($("filtersPopover").classList.contains("hidden")) openFilters(); else closeFilters(true);
  };
  $("filtersClose").onclick = () => closeFilters(true);
  document.addEventListener("click", (e) => {
    if (!$("filterWrap").contains(e.target)) closeFilters();
  });
  document.addEventListener("focusin", (e) => {
    if (!$("filterWrap").contains(e.target)) closeFilters();
  });
  window.addEventListener("resize", positionFiltersPopover);
  window.addEventListener("scroll", positionFiltersPopover, true);
  $("retryExclusions").onclick = () => {
    if (appState.pendingExclusionSave) saveExclusions(appState.pendingExclusionSave.exclude, appState.pendingExclusionSave.restore,
      appState.pendingExclusionSave.restoreAll, appState.pendingExclusionSave.clearFilters);
    else loadDevices();
  };
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && !views.devices.classList.contains("hidden")) refreshExclusions();
  });
  window.addEventListener("focus", () => {
    if (!views.devices.classList.contains("hidden")) refreshExclusions();
  });
  document.addEventListener("click", (e) => {
    if (e.target.closest("[data-view-excluded]")) setDeviceView("excluded", true);
    if (e.target.closest("[data-refresh-homes], [data-refresh-rooms]")) loadDevices("Updating device list from Tuya…", false, true);
  });
  // filter
  $("filter").oninput = (e) => {
    abandonFailedExclusionSave();
    appState.filterText = e.target.value.toLowerCase();
    appState.bulkSelectedIds.clear();
    appState.filteredCollapsedGateways.clear();
    renderRows(computeList());
  };
}

export {
  applyExclusions,
  closeFilters,
  exclusionError,
  inCurrentView,
  initFilters,
  loadHomes,
  matchesFilters,
  matchesHome,
  positionFiltersPopover,
  renderHomeFilter,
  resetExclusions,
  saveExclusions,
  selectAllDevices,
  selectionIds,
  setDeviceSelected,
  setDeviceView,
  setSelectionMode,
  syncSelectionUi,
  validExclusions,
};
