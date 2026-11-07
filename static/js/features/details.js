// Details.
import { DETAIL_SECTIONS, LAN_KEYS, SPEC_KEYS } from "../shared/constants.js";
import { fieldList, section } from "../shared/fields.js";
import { esc, keyDisplay, renderLocalKeyToggle } from "../shared/html.js";
import { isSubDevice, keySource } from "../shared/model.js";
import { appState } from "../shared/state.js";

function fieldSections(d) {
  const shown = new Set(["epochs", ...SPEC_KEYS, ...LAN_KEYS]);
  const labels = isSubDevice(d) ? { local_key: "Gateway local key" } : {};
  let html = "";
  for (const { title, keys, render: custom } of DETAIL_SECTIONS) {
    if (custom) { html += section(title, custom(d)); continue; }
    const present = keys.filter(key => key in d || (key === "local_key" && keySource(d)));
    present.forEach(key => shown.add(key));
    if (present.length) html += `<section class="information-section">${section(title, fieldList(d, present, labels))}</section>`;
  }
  const rest = Object.keys(d).filter(key => !shown.has(key));
  if (rest.length) html += `<section class="information-section">${section("Other fields", fieldList(d, rest))}</section>`;
  return html;
}

// One row per data point: dp id (from local_strategy), current value (status),
// and the type/range/access from the device specification.
function dpRows(d) {
  const status = d.status || {}, fns = d.function || {}, ranges = d.status_range || {};
  const dpIds = {};
  for (const [dpId, info] of Object.entries(d.local_strategy || {})) {
    if (info && info.status_code) dpIds[info.status_code] = dpId;
  }
  const codes = [...new Set([
    ...Object.keys(ranges), ...Object.keys(fns), ...Object.keys(status), ...Object.keys(dpIds),
  ])];
  codes.sort((a, b) => {
    const ia = a in dpIds ? Number(dpIds[a]) : Infinity;
    const ib = b in dpIds ? Number(dpIds[b]) : Infinity;
    if (ia !== ib) return ia < ib ? -1 : 1;
    return a.localeCompare(b);
  });
  return codes.map(code => {
    const spec = ranges[code] || {}, fn = fns[code] || {};
    return {
      code,
      dpId: dpIds[code],
      value: status[code],
      hasValue: code in status,
      type: spec.type || fn.type || "",
      values: spec.values || fn.values || "",
      access: `${ranges[code] ? "R" : ""}${fns[code] ? "W" : ""}`,
      reportType: spec.report_type || "",
      hint: [fn.name, fn.desc].filter(Boolean).join(": "),
    };
  });
}

// Tuya spec values are a JSON string; show the useful part, keep the rest in the tooltip.
function dpRange(raw, type) {
  if (!raw) return "";
  let spec;
  try { spec = JSON.parse(raw); } catch (e) { return String(raw); }
  if (!spec || typeof spec !== "object") return String(raw);
  if (Array.isArray(spec.range)) return spec.range.join(", ");
  if (Array.isArray(spec.label)) return spec.label.join(", ");
  if ("min" in spec || "max" in spec) {
    const unit = String(spec.unit ?? "").trim();
    const extra = [];
    if (Number(spec.scale)) extra.push(`scale ${spec.scale}`);
    if (spec.step !== undefined && Number(spec.step) !== 1) extra.push(`step ${spec.step}`);
    return `${spec.min ?? "?"}–${spec.max ?? "?"}${unit ? ` ${unit}` : ""}` +
      (extra.length ? ` · ${extra.join(" · ")}` : "");
  }
  return Object.keys(spec).length ? String(raw) : "";
}

// Escape hatch for anything the sections above don't cover. The displayed copy
// respects the local-key toggle; the copy button always yields the real values.
function rawSection(d) {
  const full = JSON.stringify(d, null, 2);
  const masked = appState.panelKeyVisible || !d.local_key
    ? full
    : JSON.stringify({ ...d, local_key: keyDisplay(d.local_key, false) }, null, 2);
  return `<section class="raw-record"><p class="panel-intro">The complete saved device record.</p>` +
    `<div class="raw-actions"><button type="button" data-copy="${esc(full)}">Copy JSON</button>` +
    (d.local_key ? `<span class="key-heading">${appState.panelKeyVisible ? "Hide key" : "Show key"}${renderLocalKeyToggle("panel")}</span>` : "") + `</div>` +
    `<pre class="mono">${esc(masked)}</pre></section>`;
}

export { dpRange, dpRows, fieldSections, rawSection };
