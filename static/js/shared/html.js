// Html.
import { icon } from "./icons.js";
import { appState } from "./state.js";

function badge(on) {
  return `<span class="badge ${on ? "on" : "off"}">${on ? "online" : "offline"}</span>`;
}

function deviceLink(entry) {
  return `<button type="button" class="link-btn" data-open-device="${esc(entry.id)}">` +
    `${esc(entry.name || entry.id)}</button>` +
    (appState.excludedIds.has(entry.id) ? ' <span class="badge muted">Excluded</span>' : "");
}

function keyDisplay(value, visible) {
  const text = String(value ?? "");
  if (!text) return "-";
  if (visible) return esc(text);
  const length = Math.max(text.length, 12);
  return "•".repeat(Math.min(length, 20));
}

function renderLocalKeyToggle(scope) {
  const visible = scope === "panel" ? appState.panelKeyVisible : appState.tableKeysVisible;
  const label = `${visible ? "Hide" : "Show"} local ${scope === "panel" ? "key" : "keys"}`;
  return `<button class="icon-btn" type="button" data-key-toggle="${scope}" aria-label="${label}" aria-pressed="${visible}" title="${label}">${icon(visible ? "eyeOff" : "eye")}</button>`;
}

function renderLocalKey(value, visible) {
  const text = String(value ?? "");
  return `<button class="key mono" type="button" title="Copy local key" aria-label="Copy local key" data-copy="${esc(text)}">${keyDisplay(text, visible)}</button>`;
}

function renderNodeId(value) {
  const text = String(value ?? "");
  if (!text) return '<span class="dash" title="Tuya did not return a Node ID for this sub-device.">-</span>';
  const tip = "Node ID identifies this sub-device within its gateway. Use it with the gateway's IP, protocol version and local key. Click to copy.";
  return `<button class="node-id mono" type="button" title="${esc(tip)}" aria-label="Copy sub-device Node ID" data-copy="${esc(text)}">${esc(text)}</button>`;
}

function esc(s) {
  return String(s ?? "-").replace(/[&<>"]/g, c => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;" }[c]));
}

export { badge, deviceLink, esc, keyDisplay, renderLocalKey, renderLocalKeyToggle, renderNodeId };
