// Check only on app use. The browser and server both enforce a daily cooldown.
import { api } from "../shared/api.js";
import { $ } from "../shared/dom.js";

const INTERVAL = 24 * 60 * 60 * 1000;
const STORAGE_KEY = `tuya-local-key:update-check:v1:${new URL(".", document.baseURI).pathname}`;
let layoutActions, pending = false, state = { nextAutomaticAt: 0, result: null };

function updateLink(value) {
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" || url.username || url.password || url.port) return null;
    if (url.hostname === "github.com" && /^\/vineetchoudhary\/tuya-local-key\/releases\/tag\/v?\d+\.\d+(?:\.\d+)?$/i.test(url.pathname) && !url.search && !url.hash) return url.href;
    if (url.hostname === "my.home-assistant.io" && !url.hash &&
        (url.pathname === "/redirect/updates/" && !url.search ||
         url.pathname === "/redirect/supervisor_addon/" && /^\?addon=[a-zA-Z0-9_-]{1,128}$/.test(url.search))) return url.href;
  } catch (error) { /* Invalid links leave the badge hidden. */ }
  return null;
}

function readState() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY));
    if (saved && Number.isFinite(saved.nextAutomaticAt) && saved.nextAutomaticAt >= state.nextAutomaticAt) {
      state = { nextAutomaticAt: saved.nextAutomaticAt, result: saved.result && typeof saved.result === "object" ? saved.result : null };
    }
  } catch (error) { /* The server also enforces the cooldown if storage is unavailable. */ }
  const version = document.querySelector("[data-app-version]").dataset.appVersion;
  if (state.result && state.result.current_version !== version) state.result = null;
  const result = state.result;
  const published = result && typeof result.published_at === "string" ? Date.parse(result.published_at) : NaN;
  // A known release can finish its HA waiting period without another request.
  if (result && result.available === false && result.waiting_for_release === true &&
      result.installation === "home_assistant" && Number.isFinite(published) && Date.now() >= published + 3600000) {
    state.result = { ...result, available: true, waiting_for_release: false,
      update_url: "https://my.home-assistant.io/redirect/updates/" };
    saveState();
    return true;
  }
  return false;
}

function saveState() {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(state)); } catch (error) {}
}

function renderUpdates() {
  const body = state.result || {}, url = body.available === true && updateLink(body.update_url);
  const wasAvailable = !$("updateBadge").classList.contains("hidden");
  for (const id of ["updateBadge", "footerUpdateBadge"]) {
    const badge = $(id);
    badge.classList.toggle("hidden", !url);
    if (url) {
      const destination = body.installation === "home_assistant" ? "Open Home Assistant to update" : "View the GitHub release";
      badge.href = url;
      badge.title = `Version ${body.latest_version} is available. ${destination}.`;
      badge.setAttribute("aria-label", `Update available: version ${body.latest_version}. ${destination} (opens in a new tab).`);
    } else {
      badge.removeAttribute("href"); badge.removeAttribute("title"); badge.removeAttribute("aria-label");
    }
  }
  const check = $("footerCheckUpdates");
  check.classList.toggle("hidden", !!url);
  check.disabled = pending;
  check.setAttribute("aria-busy", String(pending));
  check.textContent = pending ? "Checking…" : "Check for updates";
  if (wasAvailable !== !!url) layoutActions();
}

function showUpdateResult(body, valid) {
  const link = $("updateDialogLink"), url = body.available === true && updateLink(body.update_url);
  const failed = !valid || body.last_check_succeeded === false || body.status === "unavailable" || body.available === true && !url;
  let title, message;
  if (failed) {
    title = "Could not check for updates";
    message = "Please try again.";
  } else if (url) {
    title = "Update available";
    message = `Version ${body.latest_version} is available.`;
  } else if (body.waiting_for_release) {
    title = "Update coming soon";
    message = "Home Assistant updates become available once the release is an hour old. Please check again later.";
  } else {
    title = "No updates available";
    message = "Tuya Local Key is up to date.";
  }
  $("updateDialogTitle").textContent = title;
  $("updateDialogText").textContent = message;
  link.classList.toggle("hidden", failed || !url);
  if (!failed && url) link.href = url;
  else link.removeAttribute("href");
  $("updateDialog").showModal();
}

async function checkUpdates(manual = false) {
  if (pending || document.hidden) return;
  if (readState()) renderUpdates();
  if (!manual && Date.now() < state.nextAutomaticAt) return;
  pending = true;
  // Reserve the window before networking. Errors also wait for user activity
  // after 24 hours, unless the user explicitly asks for a manual check.
  state.nextAutomaticAt = Date.now() + INTERVAL;
  saveState(); renderUpdates();
  const response = await api(manual ? "/api/updates/check" : "/api/updates", manual ? { method: "POST" } : undefined);
  pending = false;
  const body = response.body && typeof response.body === "object" ? response.body : {};
  const valid = response.ok && typeof body.available === "boolean" && typeof body.current_version === "string";
  if (valid) state.result = body;
  saveState();
  renderUpdates();
  if (manual) showUpdateResult(body, valid);
}

let initialized = false;
function initUpdates(actions) {
  if (initialized) return;
  initialized = true;
  ({ layoutActions } = actions);
  readState(); renderUpdates();
  $("footerCheckUpdates").addEventListener("click", () => checkUpdates(true));
  $("updateDialog").addEventListener("click", e => {
    if (e.target === $("updateDialog")) $("updateDialog").close();
  });
  $("updateDialog").addEventListener("close", () => {
    ($("footerCheckUpdates").classList.contains("hidden") ? $("footerUpdateBadge") : $("footerCheckUpdates")).focus({ preventScroll: true });
  });
  const activity = e => {
    if (e.target instanceof Element && e.target.closest("#footerCheckUpdates")) return;
    checkUpdates();
  };
  for (const event of ["click", "keydown", "input", "change"]) document.addEventListener(event, activity, { passive: true });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) checkUpdates(); });
  window.addEventListener("pageshow", () => checkUpdates());
  window.addEventListener("storage", e => { if (e.key === STORAGE_KEY) { readState(); renderUpdates(); } });
  checkUpdates();
}

export { initUpdates };
