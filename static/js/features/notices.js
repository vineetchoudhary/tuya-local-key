// Notices.
import { CHANGE_BADGES } from "../shared/constants.js";
import { $ } from "../shared/dom.js";
import { formatCacheAge, plural } from "../shared/format.js";
import { deviceLink, esc } from "../shared/html.js";
import { icon } from "../shared/icons.js";
import { unnamedLeft } from "../shared/model.js";
import { appState } from "../shared/state.js";

// Cross-feature actions are connected by app.js.
let inCurrentView, openPanel, show;

const CHANGES_DISMISSED_KEY = "tuya-local-key.changes-dismissed";

const LAN_CHANGES_DISMISSED_KEY = "tuya-local-key.lan-changes-dismissed";

const LAN_SUMMARY_DISMISSED_KEY = "tuya-local-key.lan-summary-dismissed";

function indexChanges() {
  appState.changeById = new Map();
  const sources = [[appState.deviceChanges, ["renamed", "added"]],
                   [appState.lanData.changes, ["ip_changed", "version_changed", "local_key_failed"]],
                   [appState.deviceChanges, ["key_changed"]]];
  for (const [changes, kinds] of sources) {   // last write wins
    if (!changes) continue;
    for (const kind of kinds) {
      for (const entry of changes[kind] || []) appState.changeById.set(entry.id, kind);
    }
  }
}

function changeBadge(id) {
  const kind = appState.changeById.get(id);
  return kind ? `<span class="change-badge ${kind}">${CHANGE_BADGES[kind]}</span>` : "";
}

function storedNumber(key) {
  try { return Number(localStorage.getItem(key)) || 0; }
  catch (e) { return 0; }   // private mode / storage blocked: just keep showing it
}

function storeNumber(key, value) {
  try { localStorage.setItem(key, String(value || 0)); }
  catch (e) {}
}

function dismissedChangesAt() { return storedNumber(CHANGES_DISMISSED_KEY); }

function cloudChangesShown() { return !!appState.deviceChanges && dismissedChangesAt() !== appState.devicesCachedAt; }

function lanChangesShown() {
  return !!appState.lanData.changes && storedNumber(LAN_CHANGES_DISMISSED_KEY) !== appState.lanData.changes_at;
}

function dismissChanges() {
  storeNumber(CHANGES_DISMISSED_KEY, appState.devicesCachedAt);
  if (appState.lanData.changes_at) storeNumber(LAN_CHANGES_DISMISSED_KEY, appState.lanData.changes_at);
  renderNotices();
}

function lanSummaryShown() {
  const s = appState.lanData.summary;
  return !!s && storedNumber(LAN_SUMMARY_DISMISSED_KEY) !== s.finished_at;
}

function dismissLanSummary() {
  if (appState.lanData.summary) storeNumber(LAN_SUMMARY_DISMISSED_KEY, appState.lanData.summary.finished_at);
  renderNotices();
}

function changeItems() {
  const visibleChanges = changes => Object.fromEntries(Object.entries(changes || {})
    .map(([kind, entries]) => [kind, Array.isArray(entries) ? entries.filter(e => inCurrentView(e.id, e)) : entries]));
  const c = visibleChanges(cloudChangesShown() ? appState.deviceChanges : {});
  const lan = visibleChanges(lanChangesShown() ? appState.lanData.changes : {});
  const items = [];
  const links = (entries) => entries.map(deviceLink).join(", ");
  const moved = (entries) => entries.map(e =>
    `${deviceLink(e)} <span class="muted">(${esc(e.was)} → ${esc(e.now)})</span>`).join(", ");
  const keys = c.key_changed || [];
  if (keys.length) {
    const it = keys.length === 1 ? "it" : "them";
    items.push(`<li class="rotated"><b>${plural(keys.length, "local key")} changed:</b> ` +
      `${links(keys)}. Anything still holding the old ` +
      `${keys.length === 1 ? "key" : "keys"} will fail until you update ${it}.</li>`);
  }
  const failed = lan.local_key_failed || [];
  if (failed.length) {
    // Tuya may already list the new key; say so, rather than send them to Refresh.
    const rotated = new Set(((appState.deviceChanges || {}).key_changed || []).map(e => e.id));
    const unexplained = failed.some(e => !rotated.has(e.id));
    items.push(`<li class="rotated"><b>${plural(failed.length, "device")} stopped answering to ` +
      `${failed.length === 1 ? "its" : "their"} local key:</b> ` +
      failed.map(e => deviceLink(e) +
        (rotated.has(e.id) ? ' <span class="muted">(Tuya lists a new key for it)</span>' : "")).join(", ") +
      `.${unexplained ? " Refresh the device list to check for a new key." : ""}</li>`);
  }
  const versions = lan.version_changed || [];
  if (versions.length)
    items.push(`<li class="rotated"><b>${plural(versions.length, "protocol version")} changed:</b> ` +
      `${moved(versions)}. A local integration set to the old version will fail until you update it.</li>`);
  if ((lan.ip_changed || []).length)
    items.push(`<li>${plural(lan.ip_changed.length, "device")} moved to a new IP: ` +
      `${moved(lan.ip_changed)}.</li>`);
  if ((c.added || []).length)
    items.push(`<li>${plural(c.added.length, "device")} added: ${links(c.added)}.</li>`);
  if ((c.removed || []).length)
    items.push(`<li>${plural(c.removed.length, "device")} removed: ` +
      `${c.removed.map(e => esc(e.name || e.id)).join(", ")}.</li>`);
  if ((c.renamed || []).length)
    items.push(`<li>${plural(c.renamed.length, "device")} renamed: ` +
      `${c.renamed.map(e => `${deviceLink(e)} <span class="muted">(was ${esc(e.was)})</span>`)
        .join(", ")}.</li>`);
  return items;
}

function snapshotNotice() {
  const age = formatCacheAge(appState.devicesCachedAt);
  const when = age === "now" ? "" : ` from ${age}`;
  if (appState.snapshotReason === "session_invalid") {
    return `<div class="notice warn" data-snapshot="session_invalid"><div class="grow">` +
      `<span class="notice-title">Your login expired</span>` +
      `Showing the device list saved${when}. These keys are still the ones your devices ` +
      `use. Log in again to check for changes.</div>` +
      `<button type="button" class="primary" data-relogin>Log in again</button></div>`;
  }
  return `<div class="notice warn" data-snapshot="fetch_failed"><div class="grow">` +
    `<span class="notice-title">Could not reach Tuya</span>` +
    `Showing the device list saved${when}. Try Refresh again in a moment.</div></div>`;
}

// "192.168.2.8 (Valve), 192.168.2.9 (Water Timer)": where each gateway answered,
// and the sub-devices whose key it answered to.
function unnamedGateways(list) {
  return list.filter(g => (g.sub_devices || []).some(id => inCurrentView(id))).map(g =>
    `<span class="mono">${esc(g.ip)}</span> (` + (g.sub_devices || []).filter(id => inCurrentView(id)).map(id => {
    const sub = appState.deviceData.find(s => s.id === id);
    return sub ? deviceLink(sub) : esc(id);
  }).join(", ") + ")").join(", ");
}

function lanSummaryNotice() {
  const s = appState.lanData.summary;
  const reached = s.sub_devices_reached || 0;   // only sub-devices whose gateway answered
  const offline = s.sub_devices_offline || 0;   // of those, the ones it reports offline
  const lines = [`Found ${s.matched} of ${plural(s.devices, "device")} on the local network across all account devices` +
    (reached ? `, plus ${plural(reached, "sub-device")} reached through ` +
      `${reached === 1 ? "its gateway" : "their gateways"}` : "") + "." +
    (offline ? ` ${reached === 1 ? "Its gateway reports it" : `Their gateways report ${offline} of them`} ` +
      `offline.` : "")];
  const ips = (list) => `<span class="ips mono">${list.map(esc).join(", ")}</span>`;
  const refused = s.refused || [], unmatched = s.unmatched || [], late = s.out_of_budget || [];
  if (s.cancelled) lines.push("The scan was cancelled, so only the devices it matched were updated.");
  if (refused.length) {
    // A refusal is all any device sends when nothing on it uses the port, so
    // say so, rather than imply every one is a busy Tuya device.
    const one = refused.length === 1;
    lines.push(`${plural(refused.length, "address", "addresses")} refused ` +
      `${one ? "the connection" : "connections"} on port 6668. ` +
      `${one ? "It may not be a Tuya device" : "They may not be Tuya devices"} at all, since any device ` +
      `that doesn't use that port refuses. A Tuya device also refuses while another local client, ` +
      `such as Home Assistant, holds its only connection.${ips(refused)}`);
  }
  if (unmatched.length)
    lines.push(`${plural(unmatched.length, "address", "addresses")} answered on port 6668 but not to any key in ` +
      `this account.${ips(unmatched)}`);
  if (late.length)
    lines.push(`${plural(late.length, "address", "addresses")} ran out of time before every key was tried.${ips(late)}`);
  const unnamed = unnamedLeft().filter(g => (g.sub_devices || []).some(id => inCurrentView(id)));
  if (unnamed.length)
    lines.push(`${plural(unnamed.length, "gateway")} answered to local keys Tuya lists on sub-devices: ` +
      `${unnamedGateways(unnamed)}. Tuya doesn't say which gateway has which key, so open a gateway ` +
      `and check it at its IP. When that leaves one gateway, it's checked too.`);
  // The likely router (s.routers) is left out of these lists without a word.
  return `<div class="notice" id="lanSummaryNotice"><div class="grow">` +
    `<span class="notice-title">Network scan</span>${lines.map(l => `<div>${l}</div>`).join("")}</div>` +
    `<button type="button" class="icon-btn" data-dismiss-lan-summary ` +
    `aria-label="Dismiss" title="Dismiss">${icon("close")}</button></div>`;
}

function renderNotices() {
  const parts = [];
  if (appState.snapshotReason) parts.push(snapshotNotice());
  const cloud = cloudChangesShown(), lan = lanChangesShown();
  const items = changeItems();
  if ((cloud || lan) && items.length) {
    const title = cloud && lan ? "What changed"
      : cloud ? "What changed since the previous list" : "What changed since the previous scan";
    parts.push(`<div class="notice" id="changesNotice"><div class="grow">` +
      `<span class="notice-title">${title}</span>` +
      `<ul>${items.join("")}</ul></div>` +
      `<button type="button" class="icon-btn" data-dismiss-changes ` +
      `aria-label="Dismiss" title="Dismiss">${icon("close")}</button></div>`);
  }
  if (lanSummaryShown()) parts.push(lanSummaryNotice());
  $("notices").innerHTML = parts.join("");
}

let initialized = false;
function initNotices(actions = {}) {
  if (initialized) return;
  initialized = true;
  ({ inCurrentView, openPanel, show } = actions);
  $("notices").addEventListener("click", (e) => {
    if (e.target.closest("[data-dismiss-changes]")) { dismissChanges(); return; }
    if (e.target.closest("[data-dismiss-lan-summary]")) { dismissLanSummary(); return; }
    // Re-login without logging out: the saved list stays put if the scan is abandoned.
    if (e.target.closest("[data-relogin]")) { show("login"); return; }
    const link = e.target.closest("[data-open-device]");
    if (link) { appState.panelOpener = link; openPanel(link.dataset.openDevice); }
  });
}

export { changeBadge, indexChanges, initNotices, renderNotices };
