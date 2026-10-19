// Header.
import { $ } from "../shared/dom.js";
import { icon } from "../shared/icons.js";

// ---- header actions: whatever doesn't fit on the header's line goes in the ⋮ menu ----
const ACTION_IDS = ["csvBtn", "scanBtn", "refreshBtn", "logoutBtn"];

const OVERFLOW_FIRST = ["logoutBtn", "csvBtn", "scanBtn", "refreshBtn"];

let actionsWidth = null;

function layoutActions() {
  const bar = $("headerActions");
  if (bar.classList.contains("hidden")) return;
  closeMoreMenu();
  // Everything back on the bar, in order, so each button can be measured.
  for (const id of ACTION_IDS) {
    const button = $(id);
    button.classList.remove("menu-item");
    button.removeAttribute("role");
    bar.insertBefore(button, $("moreWrap"));
  }
  $("moreWrap").classList.add("hidden");

  const header = bar.parentElement, css = getComputedStyle(header);
  const gap = parseFloat(css.columnGap) || 10;
  const inner = header.clientWidth - parseFloat(css.paddingLeft) - parseFloat(css.paddingRight);
  const title = header.querySelector(".app-icon").offsetWidth + gap +
    header.querySelector("h1").scrollWidth + gap;
  const room = inner - title;
  const width = (ids) => ids.reduce((sum, id) => sum + $(id).offsetWidth, 0) +
    gap * Math.max(0, ids.length - 1);
  if (width(ACTION_IDS) <= room) return;
  $("moreWrap").classList.remove("hidden");
  const more = $("moreWrap").offsetWidth;
  let kept = [...ACTION_IDS];
  for (const id of OVERFLOW_FIRST) {
    if (width(kept) + (kept.length ? gap : 0) + more <= room) break;
    kept = kept.filter(k => k !== id);
  }
  for (const id of ACTION_IDS) {
    if (kept.includes(id)) continue;
    const button = $(id);
    button.classList.add("menu-item");
    button.setAttribute("role", "menuitem");
    $("moreMenu").appendChild(button);
  }
}

function menuItems() { return [...$("moreMenu").querySelectorAll(".menu-item:not(:disabled)")]; }

function openMoreMenu() {
  $("moreMenu").classList.remove("hidden");
  $("moreBtn").setAttribute("aria-expanded", "true");
  const first = menuItems()[0];
  if (first) first.focus();
}

function closeMoreMenu(refocus) {
  if ($("moreMenu").classList.contains("hidden")) return;
  $("moreMenu").classList.add("hidden");
  $("moreBtn").setAttribute("aria-expanded", "false");
  if (refocus) $("moreBtn").focus();
}

let initialized = false;
function initHeader({ goHome }) {
  if (initialized) return;
  initialized = true;
  $("homeLink").addEventListener("click", (e) => {
    e.preventDefault();
    goHome();
  });
  $("moreBtn").innerHTML = icon("more");
  $("moreBtn").onclick = () => {
    if ($("moreMenu").classList.contains("hidden")) openMoreMenu(); else closeMoreMenu();
  };
  // A chosen item runs its own handler first, then the menu closes.
  $("moreMenu").addEventListener("click", (e) => { if (e.target.closest(".menu-item")) closeMoreMenu(); });
  document.addEventListener("click", (e) => { if (!e.target.closest("#moreWrap")) closeMoreMenu(); });
  $("moreWrap").addEventListener("keydown", (e) => {
    if ($("moreMenu").classList.contains("hidden")) return;
    const items = menuItems(), at = items.indexOf(document.activeElement);
    if (e.key === "Escape") { e.stopPropagation(); closeMoreMenu(true); }
    else if (e.key === "Tab") closeMoreMenu();
    else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const step = e.key === "ArrowDown" ? 1 : -1;
      if (items.length) items[(at + step + items.length) % items.length].focus();
    }
  });
  // The header narrows with the window, and when the details panel opens beside it.
  if (window.ResizeObserver) {
    new ResizeObserver(() => {
      const width = document.querySelector("header").clientWidth;
      if (width !== actionsWidth) { actionsWidth = width; layoutActions(); }
    }).observe(document.querySelector("header"));
  } else {
    window.addEventListener("resize", layoutActions);
  }
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(layoutActions);
}

export { closeMoreMenu, initHeader, layoutActions };
