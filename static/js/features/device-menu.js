// The device panel opens as a small directory of its available pages.
import { esc } from "../shared/html.js";
import { icon } from "../shared/icons.js";
import { appState } from "../shared/state.js";
import { reportState } from "./support.js";

const OPTIONS = [
  { page: "setup", id: "panelSetupOpen", body: "setupBody", icon: "setup", label: "Set up in…", note: "Connection settings for your local integration" },
  { page: "network", id: "panelNetworkOpen", body: "networkBody", icon: "network", label: "Local network", note: "IP address, protocol and connection checks" },
  { page: "values", id: "panelValuesOpen", body: "valuesBody", icon: "activity", label: "Current values", note: "Read from the device and compare saved values" },
  { page: "details", id: "panelInfoOpen", body: "panelBody", icon: "information", label: "Device information", note: "Identity, keys, connectivity, account and timeline" },
  { page: "raw", id: "panelRawOpen", body: "rawBody", icon: "code", label: "Raw JSON", note: "View or copy the complete device record" },
  { action: "support-report", id: "panelSupportDownload", icon: "report", label: "Download a support report", note: "Save diagnostics with private information removed" },
];

function menuRow(option, report) {
  const action = option.action;
  return `<li><button id="${option.id}" class="device-menu-item" type="button" ` +
    (action ? `data-support-report aria-busy="${report.busy}" aria-disabled="${report.busy}">`
      : `data-panel-page="${option.page}" aria-controls="${option.body}">`) +
    `<span class="device-menu-icon">${icon(option.icon)}</span>` +
    `<span class="device-menu-text"><span class="device-menu-label">${action && report.busy ? "Preparing report…" : esc(option.label)}</span>` +
    `<span class="device-menu-note">${esc(option.note)}</span></span>` +
    `<span class="device-menu-chevron">${icon(action ? "download" : "chevron")}</span></button></li>`;
}

function menuSection(d) {
  const report = reportState(d);
  const excluded = appState.excludedIds.has(d.id) ? `<li data-excluded-note>` +
    `<button type="button" class="device-menu-item" data-restore-device="${esc(d.id)}"${appState.exclusionBusy ? " disabled" : ""}>` +
    `<span class="device-menu-icon">${icon("restore")}</span><span class="device-menu-text">` +
    `<span class="device-menu-label">Restore device</span><span class="device-menu-note">Excluded from the main device list</span></span>` +
    `<span class="device-menu-chevron">${icon("chevron")}</span></button></li>` : "";
  return `<nav aria-label="Device options"><ul class="device-menu">${OPTIONS.map(option => menuRow(option, report)).join("")}${excluded}</ul></nav>` +
    (report.notice ? `<p class="device-menu-message" role="${report.notice.failed ? "alert" : "status"}">${esc(report.notice.message)}</p>` : "");
}

export { menuSection };
