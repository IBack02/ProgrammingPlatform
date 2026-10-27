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
      if (getComputedStyle(panel).position === "static") panel.classList.add("tower-panel-relative");
      if (panel.querySelector(":scope > .tower-panel-decoration")) return;
      panel.classList.add("tower-decorated-panel");
      const decoration = document.createElement("span");
      decoration.className = "tower-panel-decoration";
      decoration.setAttribute("aria-hidden", "true");
      panel.append(decoration);
    });
  }
  decoratePanels();
  window.addEventListener("resize", () => {
    // A sticky sidebar becomes static at mobile breakpoints. Re-anchor its
    // ornament to the panel, and restore sticky positioning on wider screens.
    document.querySelectorAll(".tower-panel-relative").forEach(panel => panel.classList.remove("tower-panel-relative"));
    decoratePanels();
  });
  new MutationObserver(() => {
    if (!scheduled) { scheduled = true; requestAnimationFrame(decoratePanels); }
  }).observe(document.body, {childList: true, subtree: true});
})();
