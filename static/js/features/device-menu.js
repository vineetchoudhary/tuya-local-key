// The device panel opens as a small directory of its available pages.
import { esc } from "../shared/html.js";
import { icon } from "../shared/icons.js";
import { appState } from "../shared/state.js";

const OPTIONS = [
  { page: "setup", id: "panelSetupOpen", body: "setupBody", icon: "setup", label: "Set up in…", note: "Connection settings for your local integration" },
  { page: "network", id: "panelNetworkOpen", body: "networkBody", icon: "network", label: "Local network", note: "IP address, protocol and connection checks" },
  { page: "values", id: "panelValuesOpen", body: "valuesBody", icon: "activity", label: "Current values", note: "Read from the device and compare saved values" },
  { page: "details", id: "panelInfoOpen", body: "panelBody", icon: "information", label: "Device information", note: "Identity, keys, connectivity, account and timeline" },
  { page: "raw", id: "panelRawOpen", body: "rawBody", icon: "code", label: "Raw JSON", note: "View or copy the complete device record" },
];

function menuRow(option) {
  return `<li><button id="${option.id}" class="device-menu-item" type="button" ` +
    `data-panel-page="${option.page}" aria-controls="${option.body}">` +
    `<span class="device-menu-icon">${icon(option.icon)}</span>` +
    `<span class="device-menu-text"><span class="device-menu-label">${esc(option.label)}</span>` +
    `<span class="device-menu-note">${esc(option.note)}</span></span>` +
    `<span class="device-menu-chevron">${icon("chevron")}</span></button></li>`;
}

function menuSection(d) {
  const excluded = appState.excludedIds.has(d.id) ? `<li data-excluded-note>` +
    `<button type="button" class="device-menu-item" data-restore-device="${esc(d.id)}"${appState.exclusionBusy ? " disabled" : ""}>` +
    `<span class="device-menu-icon">${icon("restore")}</span><span class="device-menu-text">` +
    `<span class="device-menu-label">Restore device</span><span class="device-menu-note">Excluded from the main device list</span></span>` +
    `<span class="device-menu-chevron">${icon("chevron")}</span></button></li>` : "";
  return `<nav aria-label="Device options"><ul class="device-menu">${OPTIONS.map(menuRow).join("")}${excluded}</ul></nav>`;
}

export { menuSection };
