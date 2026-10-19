// Format.
import { esc } from "./html.js";

function utcStamp(epoch) {
  const ms = epochMs(epoch);
  if (ms === null) return "";
  return new Date(ms).toISOString().replace("T", " ").replace(/\.\d+Z$/, " UTC");
}

function formatCacheAge(cachedAt) {
  if (!cachedAt) return "now";
  const seconds = Math.max(0, Math.floor((Date.now() - cachedAt * 1000) / 1000));
  if (seconds < 5) return "now";
  if (seconds < 60) return "a few seconds ago";
  const minutes = Math.floor(seconds / 60);
  if (minutes === 1) return "1 minute ago";
  if (minutes < 60) return `${minutes} minutes ago`;
  const hours = Math.floor(minutes / 60);
  if (hours === 1) return "1 hour ago";
  if (hours < 24) return `${hours} hours ago`;
  const days = Math.floor(hours / 24);
  return days === 1 ? "1 day ago" : `${days} days ago`;
}

function plural(n, noun, many) { return `${n} ${n === 1 ? noun : (many || `${noun}s`)}`; }

// The server humanizes timestamps in UTC and sends the raw epoch alongside, so
// only the browser can name the viewer's own zone.
const tzFormat = (() => {
  try { return new Intl.DateTimeFormat(undefined, { timeZoneName: "short" }); }
  catch (e) { return null; }
})();

function epochMs(epoch) {
  const value = Number(epoch);
  if (!value || !isFinite(value)) return null;   // 0/None/unparseable: no time to show
  return value > 1e12 ? value : value * 1000;    // Tuya reports seconds or milliseconds
}

// "2024-07-03 15:16:40 IST" — local time, "" when there is no usable epoch.
function localTime(epoch) {
  const ms = epochMs(epoch);
  if (ms === null) return "";
  const date = new Date(ms);
  if (isNaN(date.getTime())) return "";
  const pad = (n) => String(n).padStart(2, "0");
  const stamp = `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
  const zone = tzFormat
    ? (tzFormat.formatToParts(date).find(part => part.type === "timeZoneName") || {}).value
    : "";
  return zone ? `${stamp} ${zone}` : stamp;
}

function renderTime(d, key) {
  return esc(localTime(d.epochs && d.epochs[key]) || d[key]);
}

function cmp(a, b) {
  if (typeof a === "number" && typeof b === "number") return a - b;   // e.g. the Status rank
  // Booleans: true sorts first when ascending.
  if (typeof a === "boolean" || typeof b === "boolean") return (a === b) ? 0 : (a ? -1 : 1);
  // Timestamps are "YYYY-MM-DD HH:MM:SS" strings — lexical order == chronological.
  a = (a ?? "").toString().toLowerCase();
  b = (b ?? "").toString().toLowerCase();
  return a < b ? -1 : a > b ? 1 : 0;
}

export { cmp, formatCacheAge, localTime, plural, renderTime, utcStamp };
