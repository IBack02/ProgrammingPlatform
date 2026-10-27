"use strict";
(() => {
  if (!document.documentElement.classList.contains("tower-has-decoration")) return;
  const panels = ".card, .panel, .hero, .question-card, .paper, .journal-panel, .journal-overview, .collection-auth";
  let scheduled = false;
  function decoratePanels() {
    scheduled = false;
    document.querySelectorAll(panels).forEach(panel => {
      // A single border treatment per outer panel, never inside editors or controls.
      if (panel.parentElement.closest(panels) || panel.closest("dialog, [role=dialog], .overlay")) return;
      if (panel.querySelector(":scope > .tower-panel-decoration")) return;
      if (getComputedStyle(panel).position === "static") panel.classList.add("tower-panel-relative");
      panel.classList.add("tower-decorated-panel");
      const decoration = document.createElement("span");
      decoration.className = "tower-panel-decoration";
      decoration.setAttribute("aria-hidden", "true");
      panel.append(decoration);
    });
  }
  decoratePanels();
  new MutationObserver(() => {
    if (!scheduled) { scheduled = true; requestAnimationFrame(decoratePanels); }
  }).observe(document.body, {childList: true, subtree: true});
})();
