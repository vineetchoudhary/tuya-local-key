// Panel.
import { $ } from "../shared/dom.js";
import { esc } from "../shared/html.js";
import { icon } from "../shared/icons.js";
import { statusBadge } from "../shared/model.js";
import { appState } from "../shared/state.js";

// Cross-feature actions are connected by app.js.
let loadDevices, repaintSetup, saveExclusions, showScanBox;

const panelPageCache = new Map();

const PANEL_PAGES = {};
function registerPanelPage(name, definition) { PANEL_PAGES[name] = definition; }

function panelPageKey(id, view) { return JSON.stringify([id, view]); }

function copyPanelState(state) {
  return { ...state,
    disclosures: (state.disclosures || []).map(item => ({ ...item })),
    draft: state.draft ? { ...state.draft } : null,
    selection: state.selection ? { ...state.selection } : null,
    setup: state.setup ? { ...state.setup } : null,
  };
}

function panelNodeSelector(el, body) {
  if (el.id) return `#${CSS.escape(el.id)}`;
  const parts = [];
  for (let node = el; node !== body; node = node.parentElement) {
    const siblings = [...node.parentElement.children].filter(sibling => sibling.tagName === node.tagName);
    parts.unshift(`${node.tagName.toLowerCase()}:nth-of-type(${siblings.indexOf(node) + 1})`);
  }
  return `#${body.id} > ${parts.join(" > ")}`;
}

function panelFocusSelector(el) {
  if (!el || !$("panel").contains(el)) return null;
  if (el.id) return `#${CSS.escape(el.id)}`;
  const body = $(PANEL_PAGES[appState.panelView].body);
  if (!body.contains(el)) return null;
  // Stable control attributes avoid storing copy values, which can be keys.
  for (const attr of ["data-setup-action", "data-network-action", "data-setup-tool", "data-open-device", "data-localtuya-edition", "data-key-toggle"]) {
    if (el.hasAttribute(attr)) return `#${body.id} [${attr}="${CSS.escape(el.getAttribute(attr))}"]`;
  }
  return panelNodeSelector(el, body);
}

function capturePanelPage() {
  if (!appState.panelPage || $("panel").dataset.deviceId !== appState.selectedId || $("panel").dataset.view !== appState.panelView) return;
  const body = $(PANEL_PAGES[appState.panelView].body), state = appState.panelPage.state;
  state.scroll = body.scrollTop;
  state.disclosures = [...body.querySelectorAll("details")].map(el => {
    const classes = [...el.classList].map(name => CSS.escape(name)).join(".");
    const unique = classes && body.querySelectorAll(`details.${classes}`).length === 1;
    return { selector: unique ? `details.${classes}` : panelNodeSelector(el, body), open: el.open };
  });
  if (appState.panelView === "setup") {
    const advanced = body.querySelector("[data-setup-advanced]");
    // Read live disclosure state; its toggle event may still be queued.
    if (advanced) appState.setupAdvancedOpen = advanced.open;
    state.setup = { tool: appState.setupTool, edition: appState.localTuyaEdition, advanced: appState.setupAdvancedOpen };
  }
  const input = body.querySelector("[data-lan-check] input");
  state.draft = input && input.value !== input.defaultValue ? {
    id: input.form.dataset.lanCheck, value: input.value,
  } : null;
  state.selection = input ? { id: input.form.dataset.lanCheck,
    start: input.selectionStart, end: input.selectionEnd } : null;
  panelPageCache.set(panelPageKey(appState.selectedId, appState.panelView), copyPanelState(state));
}

function newPanelPage(id, view) {
  const state = copyPanelState(panelPageCache.get(panelPageKey(id, view)) || {});
  // A forward visit inherits the current tool. Back restores its own snapshot.
  if (view === "setup") state.setup = { tool: appState.setupTool, edition: appState.localTuyaEdition, advanced: appState.setupAdvancedOpen };
  return { id, view, state };
}

function activatePanelPage(page) {
  if (appState.selectedId !== page.id) appState.panelKeyVisible = false;
  appState.panelPage = page; appState.selectedId = page.id; appState.panelView = page.view;
  if (appState.panelView === "setup" && page.state.setup) {
    appState.setupTool = page.state.setup.tool;
    appState.localTuyaEdition = page.state.setup.edition;
    appState.setupAdvancedOpen = page.state.setup.advanced;
  }
}

function focusPanelPage(selector) {
  const page = PANEL_PAGES[appState.panelView], body = $(page.body);
  const remembered = selector && $("panel").querySelector(selector);
  const normal = page.focus && body.querySelector(page.focus);
  const focus = remembered && !remembered.disabled ? remembered : normal && !normal.disabled ? normal : body;
  focus.focus({ preventScroll: true });
  if (focus.matches("[data-lan-check] input") && appState.panelPage.state.selection) {
    const selection = appState.panelPage.state.selection;
    if (selection.id === focus.form.dataset.lanCheck) focus.setSelectionRange(selection.start, selection.end);
  }
}

// Table and notice links start a new panel visit. Links inside it use navigatePanel.
function openPanel(id, view = "details") {
  if (!PANEL_PAGES[view] || !appState.deviceData.some(d => d.id === id)) return;
  const row = appState.panelOpener && appState.panelOpener.closest("#rows tr[data-id]");
  appState.panelOpenerSelector = row ? `#${CSS.escape(row.id)} [data-open]`
    : appState.panelOpener && appState.panelOpener.hasAttribute("data-open-device")
      ? `#notices [data-open-device="${CSS.escape(appState.panelOpener.dataset.openDevice)}"]` : null;
  appState.panelHistory = []; panelPageCache.clear();
  appState.panelKeyVisible = false;
  activatePanelPage(newPanelPage(id, view));
  if (!renderPanel(false)) return;
  document.body.classList.add("panel-open");
  $("panel").setAttribute("aria-hidden", "false");
  $("panel").focus();
  markSelectedRow();
}

function navigatePanel(id, view, opener = document.activeElement) {
  if (!appState.panelPage || !PANEL_PAGES[view] || !appState.deviceData.some(d => d.id === id)) return;
  if (id === appState.selectedId && view === appState.panelView) return;
  capturePanelPage();
  appState.panelHistory.push({ ...appState.panelPage, state: copyPanelState(appState.panelPage.state), returnFocus: panelFocusSelector(opener) });
  activatePanelPage(newPanelPage(id, view));
  if (!renderPanel(false)) return;
  focusPanelPage(); markSelectedRow();
}

function openSetup() { if (appState.selectedId) navigatePanel(appState.selectedId, "setup", $("panelSetupOpen")); }

function openNetwork(opener = document.activeElement) { if (appState.selectedId) navigatePanel(appState.selectedId, "network", opener); }

function backPanel() {
  if (!appState.panelPage) return;
  capturePanelPage();
  while (appState.panelHistory.length) {
    const page = appState.panelHistory.pop();
    // Refreshes can remove earlier devices. Never restore an old device record.
    if (!PANEL_PAGES[page.view] || !appState.deviceData.some(d => d.id === page.id)) continue;
    activatePanelPage(page);
    if (!renderPanel(false)) return;
    focusPanelPage(page.returnFocus); markSelectedRow();
    return;
  }
  closePanel();
}

function closePanel() {
  if (!appState.selectedId) return;
  appState.selectedId = null; appState.panelPage = null; appState.panelView = "details";
  appState.panelKeyVisible = false;
  appState.panelHistory = []; panelPageCache.clear();
  delete $("panel").dataset.deviceId; delete $("panel").dataset.view;
  document.body.classList.remove("panel-open");
  $("panel").setAttribute("aria-hidden", "true");
  markSelectedRow();
  const original = appState.panelOpener && appState.panelOpener.isConnected && appState.panelOpener.matches("button, a, [tabindex]") ? appState.panelOpener : null;
  const opener = original || (appState.panelOpenerSelector && document.querySelector(appState.panelOpenerSelector));
  if (opener && !opener.disabled && opener.getClientRects().length) opener.focus();
  else if ($("filter").getClientRects().length) $("filter").focus();
  appState.panelOpener = null; appState.panelOpenerSelector = null;
}

function markSelectedRow() {
  for (const tr of $("rows").querySelectorAll("tr[data-id]")) {
    tr.classList.toggle("selected", tr.dataset.id === appState.selectedId);
  }
}

// Regenerate content from current device/LAN data; history stores only UI state.
function renderPanel(capture = true) {
  if (!appState.panelPage) return false;
  const d = appState.deviceData.find(dev => dev.id === appState.selectedId);
  if (!d) { backPanel(); return false; }
  const page = PANEL_PAGES[appState.panelView], body = $(page.body);
  const active = document.activeElement;
  const focus = capture && body.contains(active) ? panelFocusSelector(active) : null;
  if (capture) capturePanelPage();
  const state = appState.panelPage.state, actionView = appState.panelView !== "details";
  $("panel").dataset.deviceId = d.id;
  $("panel").dataset.view = appState.panelView;
  $("panelTitle").textContent = page.title(d);
  $("panelTitle").classList.toggle("setup-title", actionView);
  $("panelTitle").title = actionView ? $("panelTitle").textContent : "";
  $("panelSub").classList.toggle("hidden", actionView);
  $("panelSub").innerHTML = actionView ? ""
    : [statusBadge(d), esc(d.product_name), esc(d.category)]
      .filter(part => part && part !== "-").join("<span>·</span>");
  const previous = appState.panelHistory[appState.panelHistory.length - 1];
  $("panelNav").classList.toggle("hidden", !previous);
  $("panelBack").innerHTML = `${icon("chevron")}${esc(previous ? PANEL_PAGES[previous.view].backLabel || "Back" : "Back")}`;
  $("panelBack").setAttribute("aria-controls", previous ? PANEL_PAGES[previous.view].body : "panelBody");
  $("panelSetupEntry").classList.toggle("hidden", actionView);
  for (const [view, definition] of Object.entries(PANEL_PAGES)) {
    const content = $(definition.body);
    content.classList.toggle("hidden", view !== appState.panelView);
    const excludedNote = appState.excludedIds.has(d.id) ? `<div class="excluded-panel-note" data-excluded-note>` +
      `<span class="badge muted">Excluded</span> Hidden from the main device list. ` +
      `<button type="button" class="link-btn" data-restore-device="${esc(d.id)}"${appState.exclusionBusy ? " disabled" : ""}>Restore device</button></div>` : "";
    content.innerHTML = view === appState.panelView ? excludedNote + page.render(d) : "";
  }
  for (const { selector, open } of state.disclosures || []) {
    const el = body.querySelector(selector);
    if (el) el.open = open;
  }
  const input = body.querySelector("[data-lan-check] input");
  if (input && state.draft) {
    if (input.form.dataset.lanCheck === state.draft.id) input.value = state.draft.value;
    else state.draft = null;
  }
  body.scrollTop = state.scroll || 0;
  if (focus) focusPanelPage(focus);
  return true;
}

function panelAction(action, opener) {
  if (!appState.selectedId) return;
  if (action === "network") { openNetwork(opener); return; }
  if (action === "edition") { appState.localTuyaEdition = "xzetsubou"; repaintSetup("[data-localtuya-edition]"); return; }
  if (action === "refresh") {
    if (!$("refreshBtn").disabled) loadDevices("Updating device list from Tuya…", true, true);
    return;
  }
  if (action === "scan") {
    closePanel(); showScanBox(true);
    $("scanTargets").scrollIntoView({ block: "center" }); $("scanTargets").focus();
  }
}

let initialized = false;
function initPanel(actions = {}) {
  if (initialized) return;
  initialized = true;
  ({ loadDevices, repaintSetup, saveExclusions, showScanBox } = actions);
  // Device links stay in the current view, including a gateway's setup view.
  $("panel").addEventListener("click", (e) => {
    const restore = e.target.closest("[data-restore-device]");
    if (restore) { saveExclusions([], [restore.dataset.restoreDevice]); return; }
    const tab = e.target.closest("[data-setup-tool]");
    if (tab) {
      appState.setupTool = tab.dataset.setupTool;
      repaintSetup(`[data-setup-tool="${appState.setupTool}"]`);
      return;
    }
    const action = e.target.closest("[data-setup-action], [data-network-action]");
    if (action) { panelAction(action.dataset.setupAction || action.dataset.networkAction, action); return; }
    const link = e.target.closest("[data-open-device]");
    if (link) { navigatePanel(link.dataset.openDevice, appState.panelView, link); return; }
    const quick = e.target.closest("[data-check-ip]");
    const form = quick && $("networkBody").querySelector("[data-lan-check]");
    if (form) {
      form.elements.ip.value = quick.dataset.checkIp;
      form.requestSubmit();
    }
  });
  $("panelClose").innerHTML = icon("close");
  $("panelClose").onclick = closePanel;
  $("panelSetupOpen").innerHTML = `Set up in…${icon("chevron")}`;
  $("panelSetupOpen").onclick = openSetup;
  $("panelNetworkOpen").innerHTML = `Local network${icon("chevron")}`;
  $("panelNetworkOpen").onclick = () => openNetwork($("panelNetworkOpen"));
  $("panelBack").onclick = backPanel;
  $("scrim").onclick = closePanel;
}

export { backPanel, closePanel, focusPanelPage, initPanel, navigatePanel, openPanel, registerPanelPage, renderPanel };
