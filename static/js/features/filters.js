// Filters.
import { api } from "../shared/api.js";
import { $, views } from "../shared/dom.js";
import { plural } from "../shared/format.js";
import { esc } from "../shared/html.js";
import { icon } from "../shared/icons.js";
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

function inCurrentView(id, fallback) {
  return appState.excludedIds.has(id) === (appState.deviceView === "excluded") && matchesHome(appState.deviceData.find(d => d.id === id) || fallback);
}

function homePreferenceKey() { return `tuya-local-key.home.${appState.accountId}`; }

function rememberHome() {
  appState.preferredHome = appState.selectedHome;
  try { localStorage.setItem(homePreferenceKey(), appState.selectedHome); } catch (e) {}
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
}

function positionFiltersPopover() {
  const popover = $("filtersPopover");
  if (popover.classList.contains("hidden")) return;
  const anchor = $("filtersBtn").getBoundingClientRect();
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
  const home = $("homeFilter"), select = $("selectDevicesBtn");
  (!home.disabled ? home : !select.disabled ? select : $("filtersClose")).focus();
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
    try { appState.preferredHome = localStorage.getItem(homePreferenceKey()) || ""; } catch (e) { appState.preferredHome = ""; }
    appState.selectedHome = ""; appState.homes = null;
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
  const filterCount = appState.excludedIds.size + Number(!!appState.selectedHome);
  const filterSummary = [appState.excludedIds.size ? plural(appState.excludedIds.size, "excluded device") : "",
    appState.selectedHome ? "1 home filter" : ""].filter(Boolean).join(", ");
  $("filterCount").textContent = filterCount;
  $("filterCount").classList.toggle("hidden", !filterCount);
  $("filtersBtn").dataset.active = String(!!filterCount);
  $("filtersBtn").setAttribute("aria-label", filterCount ? `Filter (${filterCount}): ${filterSummary}` : "Filter");
  $("clearAllFilters").disabled = disabled || !(filterCount || appState.filterText || appState.preferredHome || appState.selectionMode || appState.deviceView !== "active");
  $("clearAllFilters").textContent = appState.exclusionBusy && appState.pendingExclusionSave && appState.pendingExclusionSave.clearFilters ? "Clearing…" : "Clear all";
  $("viewActions").classList.toggle("hidden", appState.deviceView !== "excluded");
  $("backToDevicesBtn").disabled = disabled;
  $("restoreAll").classList.toggle("hidden", appState.deviceView !== "excluded" || !appState.excludedIds.size);
  $("restoreAll").disabled = disabled || !appState.excludedIds.size;
  $("restoreAll").textContent = appState.selectedHome ? "Restore all (all homes)" : "Restore all";
  $("restoreAll").title = "Restore every excluded device in this account, across all homes";
  $("retryExclusions").disabled = appState.exclusionBusy;
  $("filter").disabled = !appState.exclusionsLoaded;
  $("csvBtn").disabled = !appState.exclusionsLoaded;
  $("scanBtn").disabled = !appState.exclusionsLoaded;
  $("homeFilter").disabled = disabled || homeOptions().length < 2;
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
  if (appState.selectedId && !inCurrentView(appState.selectedId)) closePanel();
  render(); $("homeFilter").focus();
}

function resetDeviceFilters() {
  appState.selectedHome = ""; rememberHome();
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
  $("clearAllFilters").onclick = () => {
    if (!appState.exclusionsLoaded || appState.exclusionBusy) return;
    abandonFailedExclusionSave();
    if (appState.excludedIds.size) saveExclusions([], [], true, true);
    else resetDeviceFilters();
  };
  $("filtersIcon").innerHTML = icon("filters");
  $("filtersClose").innerHTML = icon("close");
  $("homeChevron").innerHTML = icon("chevron");
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
    if (e.target.closest("[data-refresh-homes]")) loadDevices("Updating device list from Tuya…", false, true);
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
  matchesHome,
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
