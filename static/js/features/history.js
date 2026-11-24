// Saved history is read independently of cloud refreshes and device filters.
import { api } from "../shared/api.js";
import { $ } from "../shared/dom.js";
import { esc } from "../shared/html.js";
import { localTime, plural } from "../shared/format.js";
import { icon } from "../shared/icons.js";
import { appState } from "../shared/state.js";

const LABELS = {
  added: "Added to Tuya list", removed: "No longer listed in Tuya", reappeared: "Listed again in Tuya",
  renamed: "Device name", key_changed: "Local key changed", key_available: "Local key became available",
  key_unavailable: "Tuya no longer returned a local key", home_changed: "Home membership",
  room_changed: "Room", room_renamed: "Room name", gateway_changed: "Gateway",
  node_changed: "Node ID", role_changed: "Connection role", identity_changed: "Device identity",
  ip_changed: "Local IP", protocol_changed: "Protocol", connection_changed: "Local connection",
  sub_device_changed: "Availability reported by gateway",
};
const ERRORS = {
  history_invalid: "Saved history could not be read. The existing file has been preserved.",
  history_version: "This history was saved by an unsupported app version. Its file has been preserved.",
  history_key_unavailable: "The encryption key for saved history is missing or invalid. Restore the matching data and key, or reset the history files.",
  history_configuration: "Check the history configuration: enabled setting, retention (1 to 3650 days), and separate storage paths.",
  history_delete_failed: "Saved history could not be deleted. Check that the app can write to its data directory.",
  history_busy: "Another process is using the history file. Try again.",
  history_cursor_expired: "History was cleared, restored or expired. Reload history to continue.",
  invalid_history_query: "Check the history filters and date range.",
};
let openPanel, renderPanel, focusPanelPage, renderNotices, loadDevices;
let generation = 0, cursor = null, saved = null, entries = [], expectedAccount = null, busy = false, activePage = null;
const FILTER_IDS = ["historyKind", "historySource", "historySince", "historyUntil"];
const entryTime = new Intl.DateTimeFormat(undefined, {
  day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
});

function resetHistory() {
  generation++; cursor = saved = null; entries = []; expectedAccount = null; busy = false;
  activePage = null;
  $("historyEntries").replaceChildren(); $("historyStatus").textContent = "";
  $("historyEntries").setAttribute("aria-busy", "false");
  $("historyStatus").classList.remove("error");
  $("historyFilters").reset();
  $("historyFilterOptions").open = false;
  $("historyFilterOptions").classList.remove("hidden");
  updateFilterCount();
  $("historyClearDialog").close("cancel");
}

function expandedEntries() {
  return [...$("historyEntries").querySelectorAll("details[open]")].map(el => el.dataset.historySequence);
}

function captureHistory(state) {
  state.history = { ...Object.fromEntries(FILTER_IDS.map(id => [id, $(id).value])),
    filtersOpen: $("historyFilterOptions").open, expanded: expandedEntries(), epoch: saved && saved.epoch,
    loaded: entries.length };
}

function historyContext(id) {
  if (!id) return null;
  const device = expectedAccount === appState.accountId && saved && saved.devices
    ? saved.devices.find(d => d.id === id) : null;
  return { id, name: device ? device.name : id };
}

function historySubtitle(d) {
  const id = d.id;
  const current = appState.deviceData.find(d => d.id === id);
  if (current) return current.name || id;
  const archived = saved && saved.devices && saved.devices.find(d => d.id === id);
  return archived ? `${archived.name} · no longer listed` : id;
}

function enterHistory(page) {
  resetHistory(); activePage = page; expectedAccount = appState.accountId;
  const ui = page.state.history;
  if (ui) {
    for (const id of FILTER_IDS) $(id).value = ui[id] || "";
    $("historyFilterOptions").open = !!ui.filtersOpen;
  }
  updateFilterCount(); loadHistory(false, { ...ui, scroll: page.state.scroll || 0 });
}

function filterCount() {
  return FILTER_IDS.filter(id => !!$(id).value).length;
}

function updateFilterCount() {
  const count = filterCount();
  $("historyFilterCount").textContent = String(count);
  $("historyFilterCount").classList.toggle("hidden", !count);
  $("historyFilterCount").setAttribute("aria-label", `${count} active ${count === 1 ? "filter" : "filters"}`);
  $("historyClearFilters").disabled = !count;
}

function queryParams() {
  const params = new URLSearchParams({ account_id: expectedAccount, device_id: activePage.id });
  for (const [id, key] of [["historyKind", "kind"], ["historySource", "source"]]) {
    if ($(id).value) params.set(key, $(id).value);
  }
  for (const [id, key] of [["historySince", "since"], ["historyUntil", "until"]]) {
    const value = $(id).value;
    if (!value) continue;
    const [year, month, day] = value.split("-").map(Number);
    const date = new Date(year, month - 1, day + (key === "until" ? 1 : 0));
    params.set(key, String(date.getTime() / 1000 - (key === "until" ? 0.001 : 0)));
  }
  return params;
}

function valueText(value, kind) {
  if (kind === "connection_changed") return ({ ok: "reachable", busy: "busy", unreachable: "unreachable", key_mismatch: "saved connection details did not work" })[value] || String(value);
  if (kind === "sub_device_changed") return value ? "online" : "offline";
  if (kind === "role_changed") return value ? "sub-device" : "direct device";
  if (value === null || value === undefined) return "None";
  if (Array.isArray(value)) return value.length ? value.map(v => valueText(v, kind)).join(", ") : "None";
  if (typeof value === "object") {
    if (value.version) return value.version + (value.device22 ? " (device22)" : "");
    if (value.state) return value.state === "unassigned" ? "No room" : value.name || value.id;
    if (value.name || value.id) return value.name || value.id;
    return JSON.stringify(value);
  }
  return String(value);
}

function changeHtml(change) {
  const values = Object.hasOwn(change, "before") || Object.hasOwn(change, "after")
    ? `<div class="history-values"><dl class="history-before"><dt>Before</dt><dd>${esc(valueText(change.before, change.kind))}</dd></dl>` +
      `<span class="history-value-arrow" aria-hidden="true">→</span>` +
      `<dl class="history-after"><dt>After</dt><dd>${esc(valueText(change.after, change.kind))}</dd></dl></div>` : "";
  const note = change.kind === "connection_changed" && change.after === "ok" && change.key_authenticated === false
    ? `<p class="history-change-note">The reply identifies the device. Protocol 3.1 does not authenticate the key.</p>` : "";
  return `<li class="history-change"><h3>${esc(LABELS[change.kind] || "Change")}</h3>${values}${note}</li>`;
}

function eventHtml(entry) {
  const source = entry.source === "lan" ? "Local" : "Cloud";
  const detected = new Date(entry.observed_at * 1000);
  const inherited = entry.device_id !== activePage.id;
  const observations = entry.changes.filter(change => change.previous_observed_at).map(change =>
    `<dt>${esc(LABELS[change.kind] || "Change")} last known</dt><dd>${esc(localTime(change.previous_observed_at))}</dd>`).join("");
  return `<article class="history-entry" data-history-sequence="${entry.sequence}">` +
    `<div class="history-entry-header"><time datetime="${detected.toISOString()}" title="Detected ${esc(localTime(entry.observed_at))}">` +
    `${esc(entryTime.format(detected))}</time><span class="badge muted">${source}</span></div>` +
    (inherited ? `<p class="history-context">Gateway change <strong>${esc(entry.name)}</strong></p>` : "") +
    `<ul class="history-change-list" role="list">${entry.changes.map(changeHtml).join("")}</ul>` +
    `<details class="history-observation" data-history-sequence="${entry.sequence}"><summary>Observation details</summary>` +
    `<dl><dt>Detected</dt><dd>${esc(localTime(entry.observed_at))}</dd>` +
    (entry.previous_observed_at ? `<dt>Previous observation</dt><dd>${esc(localTime(entry.previous_observed_at))}</dd>` : "") +
    `${observations}</dl>` +
    (!inherited && entry.related_device_ids.length
      ? `<p>Also applies through this gateway to ${plural(entry.related_device_ids.length, "sub-device")}.</p>` : "") +
    `</details></article>`;
}

function setBusy(value) {
  busy = value;
  $("historyEntries").setAttribute("aria-busy", String(value));
  for (const id of ["historyRefresh", "historyClear", "historyMore"]) $(id).disabled = value;
}

async function loadHistory(append = false, restore = null) {
  if (!activePage || !appState.historyEnabled) return;
  if (!expectedAccount) {
    $("historyStatus").textContent = "Refresh devices to load your account before viewing history.";
    return;
  }
  const page = activePage, restoreScroll = restore ? restore.scroll : null;
  const priorEpoch = saved && saved.epoch, expanded = expandedEntries();
  const request = ++generation, account = expectedAccount, session = appState.accountGeneration;
  updateFilterCount();
  const params = queryParams();
  if (append && cursor) params.set("cursor", cursor);
  if (!append) { entries = []; cursor = null; $("historyEntries").replaceChildren(); }
  setBusy(true);
  $("historyStatus").classList.remove("error"); $("historyStatus").textContent = "Loading history…";
  const { ok, body } = await api(`api/history?${params}`);
  if (request !== generation || page !== activePage || session !== appState.accountGeneration || account !== appState.accountId) return;
  setBusy(false);
  if (!ok || body.account_id !== account) {
    const accountLost = body.error === "account_changed" || body.account_id && body.account_id !== account || body.error === "not_logged_in";
    if (accountLost) resetHistory();
    $("historyStatus").classList.add("error");
    $("historyStatus").textContent = body.error === "account_changed" || body.account_id && body.account_id !== account
      ? "The account changed. Refresh devices before opening history again."
      : body.error === "not_logged_in" ? "You are logged out. Refresh devices to log in again."
      : ERRORS[body.error] || "History could not be loaded. Try again. Current devices are still available.";
    $("historyMore").classList.add("hidden");
    $("historyClear").disabled = true;
    if (body.reset_token && body.account_id === account) {
      saved = { reset_token: body.reset_token };
      $("historyClear").textContent = "Reset saved history";
      $("historyClear").disabled = false;
    }
    return;
  }
  saved = body;
  appState.historyError = body.recording_error || null; renderNotices();
  $("historyClear").textContent = "Clear history";
  if (!body.enabled) {
    appState.historyEnabled = false;
    renderNotices();
    $("historyStatus").textContent = "Change history is disabled in configuration." + (body.recording_error ? " " + (ERRORS[body.recording_error] || "Saved history could not be deleted.") : "");
    $("historyRefresh").disabled = true;
    $("historyClear").disabled = true;
    $("historyFilterOptions").classList.add("hidden");
    $("historyMore").classList.add("hidden"); return;
  }
  entries.push(...body.events); cursor = body.next_cursor;
  if (restore && restore.epoch === body.epoch && cursor && entries.length < (restore.loaded || 0)) {
    return loadHistory(true, restore);
  }
  $("historyClear").disabled = body.total === 0;
  const storage = body.mode === "temporary" ? "Temporary history: recording in memory only." : `Saved for up to ${body.retention_days} days or ${body.max_events.toLocaleString()} entries.`;
  $("historyStatus").textContent = `${storage} Tracking since ${localTime(body.started_at)}.` +
    (body.pruned ? " Older entries have been removed by retention." : "") +
    (body.recording_error ? "\n" + (ERRORS[body.recording_error] || "Some changes could not be saved. The next successful observation will be compared with the last saved baseline.") : "");
  $("historyStatus").classList.toggle("error", !!body.recording_error);
  const filtered = filterCount() > 0;
  const restored = restore && restore.epoch === body.epoch ? restore.expanded || [] : [];
  const opened = new Set(restore ? restored : priorEpoch === body.epoch ? expanded : []);
  $("historyEntries").innerHTML = entries.length ? entries.map(eventHtml).join("") :
    `<p class="history-empty">${filtered ? "No entries match these filters." : "No changes recorded for this device yet."}</p>`;
  for (const detail of $("historyEntries").querySelectorAll("details")) detail.open = opened.has(detail.dataset.historySequence);
  $("historyMore").classList.toggle("hidden", !cursor);
  renderPanel(!restore);
  if (restore && page.returnFocus) focusPanelPage(page.returnFocus);
  if (restoreScroll !== null) $("historyBody").scrollTop = restoreScroll;
}

function openHistory(deviceId) {
  if (!deviceId || !appState.historyEnabled) return;
  appState.panelOpener = document.activeElement;
  openPanel(deviceId, "history");
}

let initialized = false;
function initHistory(actions) {
  if (initialized) return;
  initialized = true; ({ openPanel, renderPanel, focusPanelPage, renderNotices, loadDevices } = actions);
  $("historyKind").innerHTML += Object.entries(LABELS).map(([kind, label]) => `<option value="${kind}">${label}</option>`).join("");
  $("historyKindChevron").innerHTML = icon("chevron");
  $("historySourceChevron").innerHTML = icon("chevron");
  $("historyRefresh").onclick = () => expectedAccount ? loadHistory() : loadDevices();
  $("historyMore").onclick = () => loadHistory(true);
  $("historyFilters").onsubmit = e => { e.preventDefault(); loadHistory(); };
  $("historyFilters").onchange = () => {
    updateFilterCount(); renderPanel(); loadHistory();
  };
  $("historyClearFilters").onclick = event => {
    event.preventDefault(); event.stopPropagation();
    $("historyFilters").reset();
    updateFilterCount(); renderPanel(); loadHistory();
  };
  $("historyClear").onclick = () => {
    const dialog = $("historyClearDialog");
    if (busy || !saved || dialog.open) return;
    dialog.returnValue = "";
    $("historyClearTitle").textContent = saved.reset_token ? "Reset saved history?" : "Clear this device's history?";
    $("historyClearText").textContent = saved.reset_token
      ? "This resets unreadable history for every device in this account and removes its encryption key. Recording starts again with a new baseline. Current device and network information remain available."
      : `This clears history for ${historySubtitle({ id: activePage.id })} only. Other devices' history is kept. Current device and network information remain available.`;
    dialog.showModal();
  };
  $("historyClearDialog").addEventListener("close", async () => {
    if ($("historyClearDialog").returnValue !== "clear" || !saved) return;
    const account = expectedAccount, session = appState.accountGeneration, page = activePage;
    const epoch = saved.epoch, resetToken = saved.reset_token, deviceId = page.id; generation++; setBusy(true);
    const { ok, body } = await api(resetToken ? "api/history/reset" : "api/history", {
      method: resetToken ? "POST" : "DELETE", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ account_id: account, device_id: deviceId, epoch, reset_token: resetToken, confirm_reset: !!resetToken }) });
    if (page !== activePage || session !== appState.accountGeneration || account !== appState.accountId || expectedAccount !== account) return;
    setBusy(false);
    if (ok) loadHistory();
    else $("historyStatus").textContent = ERRORS[body.error] || "History could not be cleared. Its existing entries have been preserved.";
  });
}

export { captureHistory, enterHistory, historyContext, historySubtitle, initHistory, openHistory, resetHistory };
