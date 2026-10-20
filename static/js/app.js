// Application composition and shared event routing.
import { api } from "./shared/api.js";
import * as auth from "./features/auth.js";
import { copyOnClick } from "./shared/clipboard.js";
import * as details from "./features/details.js";
import * as devices from "./features/devices.js";
import { $, initDom } from "./shared/dom.js";
import * as filters from "./features/filters.js";
import * as header from "./features/header.js";
import * as network from "./features/network.js";
import * as notices from "./features/notices.js";
import * as panel from "./features/panel.js";
import * as setup from "./features/setup.js";
import * as shell from "./features/shell.js";
import { appState } from "./shared/state.js";

export function render() {
  filters.renderHomeFilter();
  notices.renderNotices();
  devices.renderHead();
  devices.renderRows(devices.computeList());
  panel.renderPanel();
}

function goHome() {
  if ($("devices").classList.contains("hidden")) return;
  const excluded = appState.deviceView === "excluded";
  if (!excluded && !appState.selectedId && !appState.selectionMode) return;
  if ((excluded || appState.selectionMode) && (!appState.exclusionsLoaded || appState.exclusionBusy)) return;
  panel.closePanel();
  if (excluded) filters.setDeviceView("active");
  else if (appState.selectionMode) filters.setSelectionMode(false);
  $("homeLink").focus({ preventScroll: true });
}

async function boot() {
  const { body } = await api("/api/state");
  if (body.logged_in) devices.loadDevices(); else shell.show("login");
}

let initialized = false;
export function initApp() {
  if (initialized) return;
  initialized = true;
  initDom();
  const config = JSON.parse($("app-config").textContent);
  appState.gatewayCategories = new Set(config.gatewayCategories);
  panel.registerPanelPage("details", {
    body: "panelBody", backLabel: "Back to device details",
    title: d => d.name || "(unnamed)",
    render: d => details.fieldSections(d) + details.dpSection(d) + details.rawSection(d),
  });
  panel.registerPanelPage("setup", {
    body: "setupBody", backLabel: "Back to set up in…",
    title: d => `Set up “${d.name || d.id}” in…`, render: setup.setupSection,
    focus: '[aria-selected="true"]',
  });
  panel.registerPanelPage("network", {
    body: "networkBody", backLabel: "Back to local network",
    title: d => `Local network for “${d.name || d.id}”`, render: network.networkSection,
    focus: "[data-lan-check] input",
  });
  shell.initShell({
    closePanel: panel.closePanel,
    layoutActions: header.layoutActions,
    positionFiltersPopover: filters.positionFiltersPopover,
  });
  auth.initAuth({
    banner: shell.banner,
    loadDevices: devices.loadDevices,
    refreshStatus: devices.refreshStatus,
    resetExclusions: filters.resetExclusions,
    resetLan: network.resetLan,
    show: shell.show,
    stopCacheStatusTimer: devices.stopCacheStatusTimer,
  });
  devices.initDevices({
    applyExclusions: filters.applyExclusions,
    banner: shell.banner,
    changeBadge: notices.changeBadge,
    closePanel: panel.closePanel,
    exclusionError: filters.exclusionError,
    inCurrentView: filters.inCurrentView,
    indexChanges: notices.indexChanges,
    isWatchingScan: network.isWatchingScan,
    loadHomes: filters.loadHomes,
    loadLan: network.loadLan,
    matchesFilters: filters.matchesFilters,
    openPanel: panel.openPanel,
    render,
    renderPanel: panel.renderPanel,
    resetExclusions: filters.resetExclusions,
    resetLan: network.resetLan,
    selectAllDevices: filters.selectAllDevices,
    selectionIds: filters.selectionIds,
    setDeviceSelected: filters.setDeviceSelected,
    show: shell.show,
    syncSelectionUi: filters.syncSelectionUi,
    validExclusions: filters.validExclusions,
    watchScan: network.watchScan,
  });
  filters.initFilters({
    closeMoreMenu: header.closeMoreMenu,
    closePanel: panel.closePanel,
    computeList: devices.computeList,
    focusPanelPage: panel.focusPanelPage,
    groupedRows: devices.groupedRows,
    loadDevices: devices.loadDevices,
    render,
    renderHead: devices.renderHead,
    renderRows: devices.renderRows,
    resetLan: network.resetLan,
    syncTableSelection: devices.syncTableSelection,
  });
  notices.initNotices({
    inCurrentView: filters.inCurrentView,
    openPanel: panel.openPanel,
    show: shell.show,
  });
  panel.initPanel({
    loadDevices: devices.loadDevices,
    repaintSetup: setup.repaintSetup,
    saveExclusions: filters.saveExclusions,
    showScanBox: network.showScanBox,
  });
  setup.initSetup({
    inCurrentView: filters.inCurrentView,
    matchesFilters: filters.matchesFilters,
    renderPanel: panel.renderPanel,
  });
  network.initNetwork({
    exclusionError: filters.exclusionError,
    indexChanges: notices.indexChanges,
    indexDevices: devices.indexDevices,
    loadDevices: devices.loadDevices,
    render,
    renderPanel: panel.renderPanel,
  });
  header.initHeader({ goHome });
  // Each eye controls only its own area, without repainting the other one.
  document.addEventListener("click", (e) => {
    const toggle = e.target.closest("[data-key-toggle]");
    if (!toggle) return;
    if (toggle.dataset.keyToggle === "panel") {
      appState.panelKeyVisible = !appState.panelKeyVisible;
      panel.renderPanel();
    } else {
      appState.tableKeysVisible = !appState.tableKeysVisible;
      devices.renderHead(); devices.renderRows(devices.computeList());
      $("thead").querySelector("[data-key-toggle]").focus({ preventScroll: true });
    }
  });
  copyOnClick($("rows"));
  copyOnClick($("panelBody"));
  copyOnClick($("setupBody"));
  copyOnClick($("networkBody"));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !$("filtersPopover").classList.contains("hidden")) {
      e.preventDefault(); e.stopPropagation(); filters.closeFilters(true); return;
    }
    // Escape in the confirmation closes only the dialog, not the panel behind it.
    if (e.key === "Escape" && !$("logoutDialog").open) {
      if (appState.selectionMode && !appState.selectedId) { e.preventDefault(); filters.setSelectionMode(false); }
      else panel.backPanel();
    }
  });
  boot();
}

initApp();
