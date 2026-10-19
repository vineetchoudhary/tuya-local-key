// Clipboard.


function copyWithTextArea(text) {
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.setAttribute("readonly", "");
  ta.className = "clipboard-fallback";
  document.body.appendChild(ta);
  ta.focus();
  ta.select();
  ta.setSelectionRange(0, ta.value.length);
  try {
    if (!document.execCommand("copy")) throw new Error("copy failed");
  } finally {
    ta.remove();
  }
}

async function copyText(text) {
  try {
    copyWithTextArea(text);
    return;
  } catch (err) {
    if (!navigator.clipboard || !window.isSecureContext) throw err;
  }
  await navigator.clipboard.writeText(text);
}

// copy any value marked with data-copy (table cells and panel fields alike)
function copyOnClick(container) {
  container.addEventListener("click", async (e) => {
    const el = e.target.closest("[data-copy]");
    if (!el) return;
    const original = el.innerHTML;
    try {
      await copyText(el.dataset.copy || "");
      el.textContent = "copied!";
    } catch (err) {
      el.textContent = "copy failed";
    }
    setTimeout(() => { if (el.isConnected) el.innerHTML = original; }, 800);
  });
}

export { copyOnClick };
