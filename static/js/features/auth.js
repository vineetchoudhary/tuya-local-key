// Auth.
import { api } from "../shared/api.js";
import { $ } from "../shared/dom.js";
import { appState } from "../shared/state.js";

// Cross-feature actions are connected by app.js.
let banner, loadDevices, refreshStatus, resetExclusions, resetLan, show, stopCacheStatusTimer;

let pollTimer = null;

function startPolling(token) {
  stopPolling();
  const started = Date.now();
  pollTimer = setInterval(async () => {
    if (Date.now() - started > 180000) { // 3 min safety
      stopPolling(); banner("QR expired. Try again."); show("login"); return;
    }
    const { status, body } = await api("/api/login/poll", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
    if (body.status === "confirmed") { stopPolling(); banner(""); loadDevices(); }
    else if (status === 404 || body.status === "expired") { stopPolling(); banner("QR expired. Try again."); show("login"); }
    else if (status >= 500) { stopPolling(); banner("Couldn't save your login session. Please try again."); }
  }, 2000);
}

function stopPolling() { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } }

async function logOut() {
  appState.deviceLoadGeneration++; resetExclusions();
  await api("/api/logout", { method: "POST" });
  appState.deviceData = []; resetLan(); $("usercode").value = ""; $("filter").value = ""; appState.filterText = ""; refreshStatus(""); stopCacheStatusTimer(); show("login");
}

let initialized = false;
function initAuth(actions = {}) {
  if (initialized) return;
  initialized = true;
  ({ banner, loadDevices, refreshStatus, resetExclusions, resetLan, show, stopCacheStatusTimer } = actions);
  // ---- login ----
  $("startBtn").onclick = async () => {
    const code = $("usercode").value.trim();
    if (!code) { banner("Enter your user code first."); return; }
    banner(""); $("startBtn").disabled = true;
    const { ok, body } = await api("/api/login/start", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_code: code }),
    });
    $("startBtn").disabled = false;
    if (!ok) { banner(body.error || "Login failed to start."); return; }
    $("qrImg").src = body.qr;
    show("qr");
    startPolling(body.token);
  };
  $("cancelBtn").onclick = () => { stopPolling(); show("login"); };
  // Logging out deletes the saved list and scan results too, so it asks first.
  $("logoutBtn").onclick = () => {
    const dialog = $("logoutDialog");
    if (dialog.open) return;
    dialog.returnValue = "";
    dialog.showModal();
  };
  $("logoutDialog").addEventListener("click", (e) => {
    if (e.target === $("logoutDialog")) $("logoutDialog").close("cancel");   // the backdrop
  });
  $("logoutDialog").addEventListener("close", () => {
    if ($("logoutDialog").returnValue === "logout") { logOut(); return; }
    // Back to the button that opened it, or to ⋮ when that button is in the menu.
    ($("logoutBtn").closest("#moreMenu") ? $("moreBtn") : $("logoutBtn")).focus();
  });
}

export { initAuth };
