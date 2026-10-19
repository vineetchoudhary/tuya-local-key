// Shell.
import { $, views } from "../shared/dom.js";
import { appState } from "../shared/state.js";

// Cross-feature actions are connected by app.js.
let closePanel, layoutActions;

function show(name) {
  for (const k in views) views[k].classList.toggle("hidden", k !== name);
  // The list's actions stay put while a refresh reloads it, and go with the session.
  $("headerActions").classList.toggle("hidden",
    !(name === "devices" || (name === "loading" && appState.deviceData.length)));
  layoutActions();
  // A refresh (devices -> loading -> devices) keeps the open device; losing the
  // session does not.
  if (name === "login" || name === "qr") {
    closePanel(); appState.collapsedGateways.clear(); appState.filteredCollapsedGateways.clear();
  }
}

function banner(msg) {
  const b = $("banner");
  if (!msg) { b.classList.add("hidden"); return; }
  b.textContent = msg; b.classList.remove("hidden");
}

let initialized = false;
function initShell(actions = {}) {
  if (initialized) return;
  initialized = true;
  ({ closePanel, layoutActions } = actions);

}

export { banner, initShell, show };
